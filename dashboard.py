# =============================================================================
# dashboard.py — Super Quant Dashboard
# =============================================================================
# Ponto de entrada visual do sistema. Roda com: python dashboard.py
# Abre no browser em: http://127.0.0.1:8050
#
# Ao abrir, executa automaticamente a atualização incremental (busca candles
# e ticks novos desde a última sessão) — sem nenhuma ação manual do usuário.
# =============================================================================

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import dash
from dash import dcc, html, Input, Output, State, callback_context, no_update, ALL
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
import threading
import json
import logging
from datetime import datetime

# Log de erros de sincronização em arquivo dedicado (nunca console — evita
# o UnicodeEncodeError já documentado quando a mensagem de erro carrega
# caractere fora do cp1252 do console do Windows). Logger NOMEADO e
# separado do root de propósito: `logging.basicConfig` configura o root e
# também capturava (poluindo o arquivo com) o log de rotina do Werkzeug/
# Flask a cada request — achado ao testar de verdade depois de subir os
# processos. `diario_trade.py` pega o MESMO logger pelo nome (compartilha
# o handler já anexado aqui, mesmo processo).
log_sync_posicoes = logging.getLogger('sync_posicoes')
log_sync_posicoes.setLevel(logging.ERROR)
log_sync_posicoes.propagate = False
if not log_sync_posicoes.handlers:
    _handler_sync = logging.FileHandler(str(Path(__file__).parent / 'log_sync_posicoes.log'), encoding='utf-8')
    _handler_sync.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log_sync_posicoes.addHandler(_handler_sync)

import config_backtest as cfg
import config_operacional
import persistencia
import run_backtest
import mt5_loader
import diario_trade
import aprendizagem
import tracados
import indice_medo_ganancia
import correlacao_ativos
import calendario_economico as cal_eco
import ai_analyst
import gerenciador_processos as gp

# Flag: MT5 disponível
try:
    import MetaTrader5 as mt5
    MT5_OK = True
except ImportError:
    MT5_OK = False

# =============================================================================
# PALETA E TOKENS
# =============================================================================

# 2026-07-17: os valores viraram `var(--x)` (definidas em assets/style.css,
# com override em `:root[data-theme="light"]`) pro modo claro/escuro do
# SQ Padrão — o navegador resolve sozinho, ao vivo, sem tocar em nenhum dos
# ~300 usos de `C['x']` espalhados pelo arquivo. EXCEÇÃO: Plotly
# (`gauge_cockpit`) não entende `var(...)` — usa `PALETA_GAUGE` abaixo, que
# mantém os hex literais de verdade por tema.
C = {
    'bg':        'var(--bg)',
    'panel':     'var(--panel)',
    'border':    'var(--border)',
    'gold':      'var(--gold)',
    'green':     'var(--green)',
    'red':       'var(--red)',
    'text':      'var(--text)',
    'text2':     'var(--text2)',
    'blue':      'var(--blue)',
    'yellow':    'var(--yellow)',
}

# Paleta paralela em hex literal — só pra Plotly (SVG renderizado a partir
# do JSON da figura, não entende `var(--x)`). Mesmos 10 tokens de `C`/
# assets/style.css, mantidos em sincronia manualmente (ver comentário lá).
PALETA_GAUGE = {
    'dark': {
        'bg': '#0A0A0F', 'panel': '#12121A', 'border': '#1C1C28',
        'gold': '#C8A96E', 'green': '#2ECC8A', 'red': '#E05C5C',
        'text': '#E8E8F0', 'text2': '#8888AA', 'blue': '#4A90D9', 'yellow': '#F0C040',
    },
    'light': {
        'bg': '#E4E7F0', 'panel': '#FFFFFF', 'border': '#C7CCDC',
        'gold': '#A87C2E', 'green': '#128A55', 'red': '#B33A3A',
        'text': '#14141C', 'text2': '#5B5B78', 'blue': '#3573B5', 'yellow': '#8A6508',
    },
}

FONTS = {
    'display': 'Space Grotesk, system-ui, sans-serif',
    'mono':    'JetBrains Mono, monospace',
    'body':    'Inter, system-ui, sans-serif',
}

# =============================================================================
# ESTADO GLOBAL
# =============================================================================

estado = {
    'bases':        {},    # {'WIN': df_win, 'WDO': df_wdo}
    'ativo':        'WIN',
    'atualizando':  False,
    'ultima_atualizacao': None,
    'erro':         None,
    'analise_ia':   '',
    'contrato_win': '—',
    'contrato_wdo': '—',
    'cotacoes_correlacionadas': [],     # ver correlacao_ativos.obter_cotacoes_correlacionadas
    'fechamentos_di': None,            # Series — ver correlacao_ativos.obter_fechamentos_di
    'posicoes_abertas': None,          # DataFrame — ver sincronizar_posicoes
    'sincronizando_posicoes': False,
    'reconciliando': False,            # ver _reconciliar_background
}
lock = threading.Lock()

# =============================================================================
# CARREGAMENTO INICIAL (ao abrir o dashboard)
# =============================================================================

def carregar_bases():
    """Carrega bases salvas e roda atualização incremental em background."""
    with lock:
        estado['atualizando'] = True
        estado['erro'] = None

    try:
        # Carrega o que já está salvo (instantâneo)
        for ativo in cfg.ATIVOS:
            df = persistencia.carregar_candles(ativo)
            if df is not None:
                with lock:
                    estado['bases'][ativo] = df

        # Atualização incremental em background (busca candles novos).
        # Trava entre PROCESSOS (não só threads) — dashboard.py e
        # dashboard_98.py rodam em paralelo, cada um seu próprio processo,
        # e escrever o mesmo Parquet ao mesmo tempo dos dois já corrompeu
        # WIN_candles.parquet de verdade (2026-07-08). Se outro processo já
        # estiver atualizando, pula este ciclo — os dados já carregados
        # (linhas acima) continuam servindo, só ficam sem os candles mais
        # recentes por até 30s (o próximo ciclo tenta de novo).
        if MT5_OK:
            with persistencia.lock_atualizacao() as adquiriu_lock:
                if not adquiriu_lock:
                    with lock:
                        estado['erro'] = None
                else:
                    try:
                        mt5_loader.conectar()
                        for ativo in cfg.ATIVOS:
                            if persistencia.base_existe(ativo):
                                df_novo = run_backtest.atualizar_incremental_silencioso(ativo)
                                if df_novo is not None:
                                    with lock:
                                        estado['bases'][ativo] = df_novo

                        # Detecta contratos vigentes para exibição no status
                        with lock:
                            t = mt5_loader.detectar_contrato_mais_recente('WIN')
                            w = mt5_loader.detectar_contrato_mais_recente('WDO')
                            estado['contrato_win'] = t or '—'
                            estado['contrato_wdo'] = w or '—'
                            # Ativos correlacionados (DI1FUT/WINFUT/WDOFUT)
                            # pro painel do hero — mesma cadência de 30s,
                            # nunca no tick de 1s (evita chamadas MT5
                            # extras por segundo). Ver correlacao_ativos.py.
                            estado['cotacoes_correlacionadas'] = correlacao_ativos.obter_cotacoes_correlacionadas(
                                estado['bases'].get('WIN'), estado['bases'].get('WDO'))
                            estado['fechamentos_di'] = correlacao_ativos.obter_fechamentos_di()

                        mt5_loader.desconectar()
                    except Exception as e:
                        with lock:
                            estado['erro'] = f"MT5: {str(e)[:60]}"

    except Exception as e:
        with lock:
            estado['erro'] = str(e)[:80]
    finally:
        with lock:
            estado['atualizando'] = False
            estado['ultima_atualizacao'] = datetime.now().strftime('%H:%M:%S')


# Inicia carregamento em background quando o app sobe
threading.Thread(target=carregar_bases, daemon=True).start()

# =============================================================================
# HELPERS DE VISUALIZAÇÃO
# =============================================================================

def _cor_delta(val):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return C['text2']
    return C['green'] if val >= 0 else C['red']

def _seta(val):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return '—'
    return '▲' if val >= 0 else '▼'

def _fmt(val, decimais=0):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return '—'
    return f"{val:,.{decimais}f}"

def _fmt_ts(iso_str) -> str:
    """Formata um timestamp ISO (de metricas.montar_regime/_info_bipolar)
    como 'dd/mm HH:MM' pra leitura humana nos cards de regime/bipolaridade."""
    if not iso_str:
        return '—'
    try:
        return pd.Timestamp(iso_str).strftime('%d/%m %H:%M')
    except (ValueError, TypeError):
        return '—'


def gauge_cockpit(valor_norm: float, label: str, sub: str, cor: str, tema: str = 'light') -> go.Figure:
    """
    Velocímetro estilo cockpit aeronáutico — arco angular com agulha.
    valor_norm: 0.0 (mínimo) a 1.0 (máximo), 0.5 = neutro.

    `tema` (2026-07-17) seleciona `PALETA_GAUGE['dark'|'light']` — Plotly
    renderiza em SVG a partir do JSON da figura, não entende `var(--x)`
    (ao contrário do resto da UI, que usa `C['x']` = 'var(--x)' e reage
    sozinho a data-theme). `cor` continua vindo já resolvido em hex
    literal pelo chamador (ver construir_hero).
    """
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    ang_min, ang_max = 225, -45  # graus, sentido horário no SVG
    ang = ang_min + (ang_max - ang_min) * valor_norm

    fig = go.Figure()

    # Arco de fundo (zona vermelha → amarela → verde)
    for i, (a_start, a_end, c) in enumerate([
        (225, 165, p['red']),
        (165, 105, p['yellow']),
        (105, 45,  p['green']),
        (45,  -45, p['green']),
    ]):
        thetas = np.linspace(np.radians(a_start), np.radians(a_end), 30)
        x_out = [0.85 * np.cos(t) for t in thetas]
        y_out = [0.85 * np.sin(t) for t in thetas]
        x_in  = [0.65 * np.cos(t) for t in thetas][::-1]
        y_in  = [0.65 * np.sin(t) for t in thetas][::-1]
        fig.add_trace(go.Scatter(
            x=x_out + x_in, y=y_out + y_in,
            fill='toself', fillcolor=c,
            line=dict(width=0), opacity=0.25,
            showlegend=False, hoverinfo='skip'
        ))

    # Arco de progresso (preenchido até o valor atual)
    ang_rad_start = np.radians(225)
    ang_rad_end   = np.radians(ang)
    if ang_rad_end < ang_rad_start:
        thetas_prog = np.linspace(ang_rad_start, ang_rad_end, 40)
    else:
        thetas_prog = np.linspace(ang_rad_start, ang_rad_end, 40)
    x_prog = [0.75 * np.cos(t) for t in thetas_prog]
    y_prog = [0.75 * np.sin(t) for t in thetas_prog]
    fig.add_trace(go.Scatter(
        x=x_prog, y=y_prog,
        mode='lines', line=dict(color=cor, width=6),
        showlegend=False, hoverinfo='skip'
    ))

    # Agulha
    ang_rad = np.radians(ang)
    fig.add_trace(go.Scatter(
        x=[0, 0.72 * np.cos(ang_rad)],
        y=[0, 0.72 * np.sin(ang_rad)],
        mode='lines', line=dict(color=p['text'], width=3),
        showlegend=False, hoverinfo='skip'
    ))

    # Centro
    fig.add_trace(go.Scatter(
        x=[0], y=[0], mode='markers',
        marker=dict(color=cor, size=10),
        showlegend=False, hoverinfo='skip'
    ))

    # Label central
    fig.add_annotation(x=0, y=-0.55, text=sub,
                       font=dict(family=FONTS['mono'], size=13, color=cor),
                       showarrow=False)
    fig.add_annotation(x=0, y=-0.75, text=label,
                       font=dict(family=FONTS['display'], size=11, color=p['text2']),
                       showarrow=False)

    fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(visible=False, range=[-1.1, 1.1]),
        yaxis=dict(visible=False, range=[-1.1, 1.1], scaleanchor='x'),
        height=200,
        # `responsive=True` no dcc.Graph (ver construir_hero) some sozinho
        # sem isso quando `height` já vem fixo no layout (mesmo bug do
        # radar da Roda da Vida, 2026-07-12) — sem `autosize`, o gauge
        # mede o container só 1x na montagem e nunca corrige se pegar um
        # width errado nessa hora, o que piora reconstruindo do zero a
        # cada 1s (2026-07-16, achado real do Jesué).
        autosize=True,
    )
    return fig


def grafico_candles(df: pd.DataFrame, n: int = 60) -> go.Figure:
    """Gráfico de candles M5 com pivôs marcados e volume com delta."""
    if df is None or df.empty:
        fig = go.Figure()
        fig.update_layout(paper_bgcolor=C['panel'], plot_bgcolor=C['bg'],
                          font=dict(color=C['text']))
        return fig

    d = df.tail(n).copy().reset_index(drop=True)

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.75, 0.25],
        vertical_spacing=0.02,
    )

    # Candles
    fig.add_trace(go.Candlestick(
        x=d['datetime'],
        open=d['open'], high=d['high'],
        low=d['low'], close=d['close'],
        increasing_line_color=C['green'],
        decreasing_line_color=C['red'],
        increasing_fillcolor=C['green'],
        decreasing_fillcolor=C['red'],
        name='Price', showlegend=False,
        line=dict(width=1),
    ), row=1, col=1)

    # Pivôs fortes
    if 'topo_forte' in d.columns:
        topos_f = d[d['topo_forte'] == True]
        if not topos_f.empty:
            fig.add_trace(go.Scatter(
                x=topos_f['datetime'], y=topos_f['preco_topo'],
                mode='markers', marker=dict(symbol='triangle-down', color=C['red'], size=10),
                name='Topo forte', showlegend=False,
            ), row=1, col=1)

    if 'fundo_forte' in d.columns:
        fundos_f = d[d['fundo_forte'] == True]
        if not fundos_f.empty:
            fig.add_trace(go.Scatter(
                x=fundos_f['datetime'], y=fundos_f['preco_fundo'],
                mode='markers', marker=dict(symbol='triangle-up', color=C['green'], size=10),
                name='Fundo forte', showlegend=False,
            ), row=1, col=1)

    # S/R mais próximo
    if 'sr_acima' in d.columns:
        sr_ac = d['sr_acima'].dropna()
        if not sr_ac.empty:
            fig.add_hline(y=sr_ac.iloc[-1], line=dict(color=C['red'], width=1, dash='dot'),
                          opacity=0.5, row=1, col=1)
    if 'sr_abaixo' in d.columns:
        sr_ab = d['sr_abaixo'].dropna()
        if not sr_ab.empty:
            fig.add_hline(y=sr_ab.iloc[-1], line=dict(color=C['green'], width=1, dash='dot'),
                          opacity=0.5, row=1, col=1)

    # Volume colorido por direção + delta
    if 'amplitude_abs' in d.columns:
        cores_vol = [C['green'] if c >= o else C['red']
                     for c, o in zip(d['close'], d['open'])]
        col_vol = 'amplitude_abs'
        if 'delta' in d.columns:
            delta_num = pd.to_numeric(d['delta'], errors='coerce').fillna(0)
            fig.add_trace(go.Bar(
                x=d['datetime'], y=delta_num.abs(),
                marker_color=[C['green'] if v >= 0 else C['red'] for v in delta_num],
                opacity=0.7, name='Delta', showlegend=False,
            ), row=2, col=1)
        else:
            fig.add_trace(go.Bar(
                x=d['datetime'], y=[1]*len(d),
                marker_color=cores_vol, opacity=0.5,
                showlegend=False,
            ), row=2, col=1)

    fig.update_layout(
        paper_bgcolor=C['panel'],
        plot_bgcolor=C['bg'],
        font=dict(family=FONTS['mono'], color=C['text'], size=11),
        margin=dict(l=50, r=10, t=10, b=10),
        xaxis_rangeslider_visible=False,
        xaxis2=dict(showgrid=False),
        yaxis=dict(gridcolor=C['border'], gridwidth=0.5),
        yaxis2=dict(gridcolor=C['border'], gridwidth=0.5, title='Delta'),
        hovermode='x unified',
    )
    return fig


# =============================================================================
# COMPONENTES DE CARD
# =============================================================================

def card(titulo: str, valor: str, sub: str = '', cor: str = None,
         largura: str = '160px', altura: str = 'auto') -> html.Div:
    cor = cor or C['text']
    return html.Div([
        html.Div(titulo, style={
            'fontSize': '10px', 'color': C['text2'],
            'textTransform': 'uppercase', 'letterSpacing': '0.08em',
            'fontFamily': FONTS['display'], 'marginBottom': '6px',
        }),
        html.Div(valor, style={
            'fontSize': '20px', 'fontWeight': '700',
            'fontFamily': FONTS['mono'], 'color': cor,
            'lineHeight': '1.1',
        }),
        html.Div(sub, style={
            'fontSize': '11px', 'color': C['text2'],
            'fontFamily': FONTS['display'], 'marginTop': '4px',
        }) if sub else None,
    ], style={
        'background': C['panel'],
        'border': f'1px solid {C["border"]}',
        'boxShadow': 'var(--shadow-card)',
        'borderRadius': '10px',
        'padding': '14px 16px',
        'minWidth': largura,
        'height': altura,
    })


def secao(titulo: str, cor_titulo: str = None) -> html.Div:
    cor = cor_titulo or C['gold']
    return html.Div([
        html.Span(titulo, style={
            'fontSize': '11px', 'fontFamily': FONTS['display'],
            'fontWeight': '600', 'color': cor,
            'textTransform': 'uppercase', 'letterSpacing': '0.1em',
        }),
        html.Div(style={
            'flex': '1', 'height': '1px',
            'background': C['border'], 'marginLeft': '12px',
        }),
    ], style={
        'display': 'flex', 'alignItems': 'center',
        'marginBottom': '16px', 'marginTop': '8px',
    })


# =============================================================================
# MÉTRICAS CALCULADAS A PARTIR DA BASE (compartilhado — ver metricas.py)
# =============================================================================

from metricas import (
    extrair_metricas, candles_para_grafico, niveis_sr_para_grafico,
    mme21_para_grafico, mme21_ponto_live, analisar_tracados,
    analisar_rastreador_tendencia,
)


# =============================================================================
# CONSTRUÇÃO DOS BLOCOS DO DASHBOARD
# =============================================================================

# Balão explicativo dos velocímetros do hero (2026-07-16, pedido do Jesué:
# "o que indica e o que fazer com isso", num balão que aponta pro
# velocímetro clicado). Os gauges vivem dentro de `construir_hero()`
# (reconstruído a cada 1s) — clicá-los com `n_clicks` do Dash sofreria o
# MESMO bug já documentado acima (contador zerado a cada recriação), então
# o clique é 100% client-side (`assets/hero_gauges.js`, delegação de evento
# em `document` + `dash_clientside.set_props` direto no
# `store-gauge-selecionado`) — o balão em si (`balao-gauge-explicacao`)
# mora FORA de `hero-conteudo` no layout estático, então nunca é recriado
# pelo tick de 1s, só pelo callback `renderizar_balao_gauge` abaixo.
_EXPLICACAO_GAUGES = {
    'forca': {
        'titulo': 'Força — quem domina agora',
        'indica': 'Compara volume/delta dos candles recentes pra ver quem pesa mais no fluxo: comprador, vendedor ou nenhum dos dois (neutro).',
        'fazer': 'Puxado pra um lado = o fluxo imediato favorece essa direção, evite operar contra ele. Em NEUTRO, não force — espere o fluxo definir um lado.',
    },
    'contexto': {
        'titulo': 'Contexto — o cenário estrutural favorece?',
        'indica': 'Olha a estrutura de fundo (tendência, posição em relação aos níveis de S/R) pra dizer se o momento estrutural favorece continuar a favor do movimento.',
        'fazer': 'FAVORÁVEL reforça manter o viés atual; DESFAVORÁVEL pede cautela mesmo se a Força parecer boa; NEUTRO = sem viés estrutural, decida mais pelo price action do momento.',
    },
    'medo_ganancia': {
        'titulo': 'Medo/Ganância — o humor do mercado',
        'indica': 'Índice adaptado (3 fatores: momentum, volatilidade, dólar x ações) — não é o oficial da CNN, é um primeiro palpite não calibrado do clima emocional do mercado.',
        'fazer': 'Extremos de medo ou ganância costumam preceder reversões — não é sinal de entrada isolado, é contexto pra calibrar risco e desconfiar de euforia/pânico.',
    },
    'correlacao': {
        'titulo': 'Correlação — DI1FUT×WINFUT×WDOFUT batem com a tese?',
        'indica': 'Mede se o DI está seguindo o WDO (correlação positiva) e inverso ao WIN (negativa) nos últimos 20 pregões — a leitura de que isso indica direcionamento mais confiável.',
        'fazer': 'Direcionamento Claro reforça confiança pra operar a favor do fluxo; Parcial/Neutra pedem mais cautela; Invertida é alerta — os 3 ativos se comportando ao contrário do esperado, considere reduzir tamanho ou ficar de fora.',
    },
}
# Posição horizontal (px) do centro de cada gauge dentro da fileira, pra
# posicionar a "seta" do balão apontando pro velocímetro certo — gauges de
# 200px + gap de 8px (ver `gauges` em `construir_hero`), centros em
# 100/308/516/724/932. 'big_players' (Fase 4, 2026-07-21) é o 5º —
# condensa a antiga seção própria "Big Players Footprints".
_POSICAO_TAIL_GAUGE = {'forca': 100, 'contexto': 308, 'medo_ganancia': 516, 'correlacao': 724, 'big_players': 932}


def _gauge_clicavel(chave: str, grafico: dcc.Graph) -> html.Div:
    """Envolve um gauge num `html.Div` com `data-gauge` — o clique é
    capturado por delegação de evento em `assets/hero_gauges.js`, não por
    `n_clicks` do Dash (ver comentário acima sobre por que)."""
    return html.Div(grafico, className='gauge-clicavel', **{'data-gauge': chave},
                     style={'cursor': 'pointer'})


def _balao_explicacao_gauge(gauge_selecionado: str) -> html.Div:
    """Balão com "o que indica" / "o que fazer" do velocímetro selecionado,
    com uma seta (2 triângulos CSS empilhados) apontando pro gauge de
    origem. Renderizado pelo callback `renderizar_balao_gauge` — nunca por
    `construir_hero`, ver comentário acima."""
    info = _EXPLICACAO_GAUGES.get(gauge_selecionado) or _EXPLICACAO_GAUGES['forca']
    left_px = _POSICAO_TAIL_GAUGE.get(gauge_selecionado, 100)
    return html.Div([
        html.Div(style={
            'position': 'absolute', 'top': '-8px', 'left': f'{left_px}px', 'transform': 'translateX(-50%)',
            'width': '0', 'height': '0', 'borderLeft': '8px solid transparent',
            'borderRight': '8px solid transparent', 'borderBottom': f'8px solid {C["border"]}',
        }),
        html.Div(style={
            'position': 'absolute', 'top': '-6px', 'left': f'{left_px}px', 'transform': 'translateX(-50%)',
            'width': '0', 'height': '0', 'borderLeft': '7px solid transparent',
            'borderRight': '7px solid transparent', 'borderBottom': f'7px solid {C["bg"]}',
        }),
        html.Div(info['titulo'], style={
            'fontFamily': FONTS['display'], 'fontSize': '12px', 'fontWeight': '700',
            'color': C['gold'], 'marginBottom': '8px',
        }),
        html.Div([
            html.Span('O que indica: ', style={'fontWeight': '700', 'color': C['text']}),
            html.Span(info['indica'], style={'color': C['text2']}),
        ], style={'fontSize': '11px', 'fontFamily': FONTS['body'], 'lineHeight': '1.5', 'marginBottom': '8px'}),
        html.Div([
            html.Span('O que fazer: ', style={'fontWeight': '700', 'color': C['text']}),
            html.Span(info['fazer'], style={'color': C['text2']}),
        ], style={'fontSize': '11px', 'fontFamily': FONTS['body'], 'lineHeight': '1.5'}),
    ], style={
        'position': 'relative', 'marginTop': '24px', 'padding': '16px 20px',
        'background': C['bg'], 'border': f'1px solid {C["border"]}', 'borderRadius': '10px',
    })


def _balao_big_players(resumo: dict | None) -> html.Div:
    """Balão do velocímetro 'Big Players' (Fase 4, 2026-07-21) — ÚNICO
    dinâmico entre os gauges (os outros usam texto estático de
    `_EXPLICACAO_GAUGES`): mostra os 3 fatos + a ação que antes eram 4
    cards numa seção própria (lado dominante/absorção/volume anômalo/o
    que fazer). Mesma moldura visual de `_balao_explicacao_gauge` (seta +
    fundo), conteúdo calculado fresco no clique (ver
    `renderizar_balao_gauge`)."""
    left_px = _POSICAO_TAIL_GAUGE.get('big_players', 932)
    seta = [
        html.Div(style={
            'position': 'absolute', 'top': '-8px', 'left': f'{left_px}px', 'transform': 'translateX(-50%)',
            'width': '0', 'height': '0', 'borderLeft': '8px solid transparent',
            'borderRight': '8px solid transparent', 'borderBottom': f'8px solid {C["border"]}',
        }),
        html.Div(style={
            'position': 'absolute', 'top': '-6px', 'left': f'{left_px}px', 'transform': 'translateX(-50%)',
            'width': '0', 'height': '0', 'borderLeft': '7px solid transparent',
            'borderRight': '7px solid transparent', 'borderBottom': f'7px solid {C["bg"]}',
        }),
    ]
    titulo = html.Div('Big Players — resumo institucional', style={
        'fontFamily': FONTS['display'], 'fontSize': '12px', 'fontWeight': '700',
        'color': C['gold'], 'marginBottom': '8px',
    })
    if not resumo or not resumo.get('tem_dado'):
        corpo = [html.Div(
            resumo.get('mensagem', 'Sem dado suficiente para esta análise.') if resumo else 'Sem dado suficiente para esta análise.',
            style={'fontSize': '11px', 'fontFamily': FONTS['body'], 'color': C['text2'], 'lineHeight': '1.5'},
        )]
    else:
        def _linha(rotulo, texto, cor=None):
            return html.Div([
                html.Span(f'{rotulo}: ', style={'fontWeight': '700', 'color': C['text']}),
                html.Span(texto, style={'color': cor or C['text2']}),
            ], style={'fontSize': '11px', 'fontFamily': FONTS['body'], 'lineHeight': '1.5', 'marginBottom': '6px'})
        corpo = [
            _linha('Lado dominante', f"{resumo['conclusao_lado']} ({resumo['detalhe_lado']})"),
            _linha('Absorção', resumo['absorcao_texto']),
            _linha('Volume', resumo['volume_texto']),
            _linha('O que fazer', resumo['acao_texto'], cor=C.get(resumo['cor_acao'], C['text2'])),
        ]
    return html.Div([*seta, titulo, *corpo], style={
        'position': 'relative', 'marginTop': '24px', 'padding': '16px 20px',
        'background': C['bg'], 'border': f'1px solid {C["border"]}', 'borderRadius': '10px',
    })


def construir_hero(m_win: dict, m_wdo: dict, ativo: str, indice_mg: dict | None = None,
                    correlacao: dict | None = None, cotacoes_correlacionadas: list | None = None,
                    tema: str = 'light', big_players: dict | None = None) -> html.Div:
    """Seção hero: velocímetros de força e contexto do ativo selecionado,
    mais o Índice de Medo e Ganância e o alinhamento DI1FUT×WINFUT×WDOFUT
    (ambos independem do ativo selecionado — ver `indice_medo_ganancia.py`
    e `correlacao_ativos.py`), com um painel de variação % do dia dos 3
    ativos ao lado do velocímetro de correlação (2026-07-16, simplificado
    de 4 pra 3 ativos a pedido do Jesué).

    Não inclui mais o seletor WIN/WDO — esse bloco virou estático no
    cabeçalho (ver `_estilo_botao_ativo`) porque esta função é reconstruída
    a cada 1s (`atualizar_dashboard`), e recriar os `html.Button` com
    `n_clicks=0` a cada tick zerava o contador de cliques no browser — causa
    raiz do bug "troca pra WDO e volta sozinho pro WIN" (ver CHANGELOG.md
    2026-07-04). O antigo botão "WIN × WDO" (puro placeholder, nunca
    calculou nada) foi removido e substituído por este velocímetro real.
    """
    m = m_win if ativo == 'WIN' else m_wdo
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])

    # Cores dos gauges — hex literal de PALETA_GAUGE (Plotly, ver
    # gauge_cockpit), não C['x'] (esse é 'var(--x)', não resolvido em SVG).
    sf = m.get('score_forca', 0.5)
    sc = m.get('score_contexto', 0.5)
    cor_forca   = p['green'] if sf > 0.6 else (p['red'] if sf < 0.4 else p['yellow'])
    cor_ctx     = p['green'] if sc > 0.6 else (p['red'] if sc < 0.4 else p['yellow'])

    label_forca = 'COMPRADORES' if sf > 0.6 else ('VENDEDORES' if sf < 0.4 else 'NEUTRO')
    label_ctx   = 'FAVORÁVEL' if sc > 0.6 else ('DESFAVORÁVEL' if sc < 0.4 else 'NEUTRO')

    gauges = [
        _gauge_clicavel('forca', dcc.Graph(id='gauge-forca',
                  figure=gauge_cockpit(sf, 'FORÇA', label_forca, cor_forca, tema),
                  config={'displayModeBar': False, 'responsive': True},
                  style={'width': '200px'})),
        _gauge_clicavel('contexto', dcc.Graph(id='gauge-contexto',
                  figure=gauge_cockpit(sc, 'CONTEXTO', label_ctx, cor_ctx, tema),
                  config={'displayModeBar': False, 'responsive': True},
                  style={'width': '200px'})),
    ]
    legendas = []
    if indice_mg is not None:
        cor_mg = p.get(indice_mg['cor'], p['yellow'])
        gauges.append(_gauge_clicavel('medo_ganancia', dcc.Graph(
            id='gauge-medo-ganancia',
            figure=gauge_cockpit(indice_mg['valor'] / 100.0, 'MEDO/GANÂNCIA', indice_mg['nivel_nome'], cor_mg, tema),
            config={'displayModeBar': False, 'staticPlot': True, 'responsive': True},
            style={'width': '200px'},
        )))
        legendas.append(html.Div(
            'Medo/Ganância: índice adaptado (3 fatores só com WIN/WDO — momentum, volatilidade, '
            'dólar x ações — não é o índice oficial da CNN, primeiro palpite não calibrado)',
            style={'fontSize': '9px', 'fontFamily': FONTS['display'], 'color': C['text2'],
                   'marginTop': '2px', 'width': '100%'},
        ))
    if correlacao is not None:
        cor_corr = p.get(correlacao['cor'], p['yellow'])
        gauges.append(_gauge_clicavel('correlacao', dcc.Graph(
            id='gauge-correlacao',
            figure=gauge_cockpit((correlacao['alinhamento'] + 1) / 2, 'CORRELAÇÃO', correlacao['nivel_nome'], cor_corr, tema),
            config={'displayModeBar': False, 'staticPlot': True, 'responsive': True},
            style={'width': '200px'},
        )))
        # Big Players (Fase 4, Modo Aprendizagem, 2026-07-21) — condensado num
        # único velocímetro, ao lado dos outros gauges (pedido do Jesué: "não
        # teve nenhuma relevância pra mim até agora... resuma em um indicador
        # só"). % do lado dominante no ponteiro; os outros 3 fatos (absorção/
        # volume anômalo/ação) ficam no balão ao clicar — ver
        # renderizar_balao_gauge/_balao_big_players (a única leitura DINÂMICA
        # dos gauges, as outras usam texto estático de _EXPLICACAO_GAUGES).
        # Inserido AQUI (antes do painel de texto CORRELACIONADOS) pra ficar
        # visualmente junto dos outros velocímetros, não depois do painel.
        if big_players is not None and big_players.get('tem_dado'):
            cor_bp = p.get(big_players['cor_lado'], p['yellow'])
            gauges.append(_gauge_clicavel('big_players', dcc.Graph(
                id='gauge-big-players',
                figure=gauge_cockpit(big_players['pct_c'] / 100.0, 'BIG PLAYERS', big_players['lado_dominante'], cor_bp, tema),
                config={'displayModeBar': False, 'staticPlot': True, 'responsive': True},
                style={'width': '200px'},
            )))
        legendas.append(html.Div(
            f"DI1FUT×WDOFUT: {correlacao['corr_di_wdo']:+.2f} · DI1FUT×WINFUT: {correlacao['corr_di_win']:+.2f} "
            f"(Pearson, últimos {correlacao['janela_dias']} pregões) — tese: DI acompanha o WDO e é inverso "
            f"ao WIN; quanto mais perto disso, mais claro o direcionamento. Primeiro palpite, não calibrado.",
            style={'fontSize': '9px', 'fontFamily': FONTS['display'], 'color': C['text2'],
                   'marginTop': '2px', 'width': '100%'},
        ))
        legendas.append(html.Div([
            html.Span('Faixas do alinhamento: ', style={'color': C['text2']}),
            html.Span('≥0.6 Direcionamento Claro', style={'color': C['green'], 'fontWeight': '700'}),
            html.Span(' · ', style={'color': C['text2']}),
            html.Span('0.2–0.6 Parcial', style={'color': C['yellow'], 'fontWeight': '700'}),
            html.Span(' · ', style={'color': C['text2']}),
            html.Span('-0.2–0.2 Neutra', style={'color': C['text2'], 'fontWeight': '700'}),
            html.Span(' · ', style={'color': C['text2']}),
            html.Span('≤-0.2 Invertida', style={'color': C['red'], 'fontWeight': '700'}),
        ], style={'fontSize': '9px', 'fontFamily': FONTS['display'], 'marginTop': '2px', 'width': '100%'}))
        if cotacoes_correlacionadas:
            linhas = []
            for cot in cotacoes_correlacionadas:
                var = cot['variacao_pct']
                cor_var = C['green'] if var >= 0 else C['red']
                seta = '▲' if var >= 0 else '▼'
                linhas.append(html.Div([
                    html.Span(cot['label'], style={
                        'fontSize': '11px', 'color': C['text2'], 'fontFamily': FONTS['display'],
                    }),
                    html.Span(f'{seta} {var:+.2f}%', style={
                        'fontSize': '12px', 'fontWeight': '700', 'color': cor_var,
                        'fontFamily': FONTS['mono'],
                    }),
                ], style={'display': 'flex', 'justifyContent': 'space-between', 'gap': '14px', 'padding': '3px 0'}))
            gauges.append(html.Div([
                html.Div('CORRELACIONADOS', style={
                    'fontSize': '9px', 'color': C['text2'], 'fontFamily': FONTS['display'],
                    'letterSpacing': '0.08em', 'marginBottom': '4px',
                }),
                *linhas,
            ], style={'minWidth': '140px', 'padding': '2px 16px', 'borderLeft': f'1px solid {C["border"]}'}))

    filhos = [html.Div(gauges, style={'display': 'flex', 'gap': '8px', 'alignItems': 'center'})]
    filhos += legendas
    return html.Div(filhos)


def _estilo_botao_ativo(nome: str, ativo_atual: str) -> dict:
    """Estilo do botão seletor de ativo (WIN/WDO) dado o ativo selecionado.
    Reutilizado tanto no layout estático do cabeçalho quanto no callback
    dedicado `atualizar_estilo_ativo_selecionado` — o `html.Button` em si
    nunca é recriado, só o `style` muda (ver docstring de `construir_hero`
    sobre por que isso é obrigatório). Movido do hero pro cabeçalho fixo em
    2026-07-15 — fica visível em qualquer aba, não só no Quant Lab."""
    ativo_flag = (ativo_atual == nome)
    base = {
        'padding': '4px 14px', 'borderRadius': '6px',
        'cursor': 'pointer', 'fontFamily': FONTS['display'],
        'fontSize': '12px', 'fontWeight': '700',
        'transition': 'transform 0.08s ease, background 0.15s ease',
        'background': C['green'] if ativo_flag else C['panel'],
        'color': C['bg'] if ativo_flag else C['text2'],
        'border': f'1px solid {C["green"] if nome == "WIN" else C["border"]}',
    }
    return base


def _botao_toggle_sr(id_botao: str) -> html.Button:
    """Ícone de olho sobreposto no canto do gráfico — mostra/oculta as
    linhas de S/R desenhadas (ver store-sr-visivel e a função
    `desenharNiveisSR` em assets/lwchart.js)."""
    return html.Button('👁', id=id_botao, type='button', n_clicks=0,
                        title='Mostrar/ocultar níveis de S/R no gráfico',
                        style={
                            'position': 'absolute', 'top': '8px', 'right': '8px',
                            'width': '26px', 'height': '24px', 'fontSize': '13px',
                            'lineHeight': '24px', 'cursor': 'pointer', 'zIndex': '2',
                            'background': C['panel'], 'color': C['text'],
                            'border': f'1px solid {C["border"]}', 'borderRadius': '6px',
                            'padding': '0',
                        })


def _botao_toggle_mme21(id_botao: str) -> html.Button:
    """Ícone de olho da MME21 — mesmo padrão de `_botao_toggle_sr`, só que
    posicionado à esquerda dele (right: 38px em vez de 8px) pra não
    sobrepor no mesmo canto do gráfico."""
    return html.Button('👁', id=id_botao, type='button', n_clicks=0,
                        title='Mostrar/ocultar MME21 no gráfico',
                        style={
                            'position': 'absolute', 'top': '8px', 'right': '38px',
                            'width': '26px', 'height': '24px', 'fontSize': '13px',
                            'lineHeight': '24px', 'cursor': 'pointer', 'zIndex': '2',
                            'background': C['panel'], 'color': 'var(--mme21)',
                            'border': f'1px solid {C["border"]}', 'borderRadius': '6px',
                            'padding': '0',
                        })


def _botao_reset_zoom(id_botao: str) -> html.Button:
    """Botão "voltar ao zoom padrão" — o zoom/pan manual do usuário agora
    sobrevive ao refresh de 30s (ver window.SQ_CHART.atualizarCandlesCompletos
    em assets/lwchart.js), então precisa de uma saída explícita quando ele
    quiser voltar à visão padrão (fitContent). Puro DOM via classe
    `.btn-reset-zoom` (wireBotaoResetZoom em assets/lwchart.js) — sem
    round-trip Dash, mesma ideia dos botões da barra de Traçados."""
    return html.Button('⟲', id=id_botao, type='button', n_clicks=0,
                        className='btn-reset-zoom',
                        title='Voltar ao zoom padrão',
                        style={
                            'position': 'absolute', 'top': '8px', 'right': '68px',
                            'width': '26px', 'height': '24px', 'fontSize': '14px',
                            'lineHeight': '24px', 'cursor': 'pointer', 'zIndex': '2',
                            'background': C['panel'], 'color': C['text'],
                            'border': f'1px solid {C["border"]}', 'borderRadius': '6px',
                            'padding': '0',
                        })


def _estilo_aba(chave: str, aba_ativa: str) -> dict:
    """Estilo de uma aba do cabeçalho (QUANT LAB/BACKTESTING/DIÁRIO/
    CONFIGURAÇÕES) dado qual está ativa — mesmo princípio de
    `_estilo_botao_ativo`: usado no layout estático inicial E no callback
    `atualizar_estilo_aba_ativa`, o `html.Button` nunca é recriado (a troca
    de aba do clássico é implementada pela primeira vez neste módulo — até
    aqui os 4 botões existiam só como decoração, sem callback nenhum)."""
    ativa = (chave == aba_ativa)
    return {
        'background': 'transparent', 'border': 'none',
        'borderBottom': f'2px solid {C["gold"]}' if ativa else '2px solid transparent',
        'color': C['text'] if ativa else C['text2'],
        'padding': '8px 20px', 'cursor': 'pointer',
        'fontFamily': FONTS['display'], 'fontSize': '12px',
        'fontWeight': '600' if ativa else '400',
        'letterSpacing': '0.06em',
    }


def construir_market_view(m: dict) -> html.Div:
    """Cards de leitura rápida — cada card mostra número, conclusão e janela temporal."""

    def _card_mv(titulo, numero, conclusao, detalhe, cor):
        """Card com três camadas: número bruto, conclusão interpretada, contexto/janela."""
        return html.Div([
            html.Div(titulo, style={
                'fontSize': '10px', 'color': C['text2'],
                'textTransform': 'uppercase', 'letterSpacing': '0.08em',
                'fontFamily': FONTS['display'], 'marginBottom': '6px',
            }),
            html.Div(numero, style={
                'fontSize': '18px', 'fontWeight': '700',
                'fontFamily': FONTS['mono'], 'color': cor, 'lineHeight': '1.2',
            }),
            html.Div(conclusao, style={
                'fontSize': '12px', 'color': C['text'],
                'fontFamily': FONTS['display'], 'marginTop': '4px',
            }),
            html.Div(detalhe, style={
                'fontSize': '10px', 'color': C['text2'],
                'fontFamily': FONTS['display'], 'marginTop': '2px',
            }),
        ], style={
            'background': C['panel'],
            'border': f'1px solid {C["border"]}',
            'boxShadow': 'var(--shadow-card)',
            'borderLeft': f'3px solid {cor}',
            'borderRadius': '10px', 'padding': '12px 14px',
            'minWidth': '160px', 'flex': '1',
        })

    tem_fluxo     = m.get('tem_fluxo', False)
    delta_acum    = m.get('delta_acum')
    razao         = m.get('razao_agressao')
    janela_n      = m.get('janela_fluxo_n', 0)
    fluxo_periodo = m.get('fluxo_periodo', '—')

    # --- CARD 1: QUEM MANDA (fluxo)
    if tem_fluxo and delta_acum is not None and razao is not None:
        pct_c = round(razao * 100)
        pct_v = 100 - pct_c
        delta_sinal = '+' if delta_acum >= 0 else ''

        if pct_c > 55:
            numero_qm = f'C {pct_c}% · V {pct_v}%'
            conclusao_qm = 'Compradores dominam o fluxo'
            cor_qm = C['green']
        elif pct_v > 55:
            numero_qm = f'V {pct_v}% · C {pct_c}%'
            conclusao_qm = 'Vendedores dominam o fluxo'
            cor_qm = C['red']
        else:
            numero_qm = f'C {pct_c}% · V {pct_v}%'
            conclusao_qm = 'Fluxo disputado — sem dominância'
            cor_qm = C['yellow']

        detalhe_qm = f'Delta {delta_sinal}{delta_acum:,} · {janela_n} candles · {fluxo_periodo}'
    else:
        numero_qm = '—'
        conclusao_qm = 'Tick não disponível neste período'
        detalhe_qm = 'Fluxo calculado só onde há dado de tick'
        cor_qm = C['text2']

    card_qm = _card_mv('Quem Manda', numero_qm, conclusao_qm, detalhe_qm, cor_qm)

    # Card "Tendência" removido do Market View (Modo Aprendizagem.txt,
    # 2026-07-20) — a leitura de tendência agora mora na seção "Rastreador
    # de Tendência" (ver construir_rastreador_tendencia/
    # analisar_rastreador_tendencia em metricas.py), logo abaixo desta.
    # `m['tendencia']`/`m['fase']`/`m['saude']` continuam computados (usados
    # em outros lugares, ex: o `tendencia` passado pro Rastreador) — só o
    # CARD nesta tela some.

    # --- CARD 3: VOLATILIDADE
    amp_rel = m.get('amp_dia_atr', np.nan)
    atr_val = m.get('atr', np.nan)
    def _nan(v): return isinstance(v, float) and np.isnan(v)

    if not _nan(amp_rel):
        if amp_rel >= 1.5:
            numero_vol = f'{amp_rel:.1f}× ATR'
            conclusao_vol = 'Volatilidade alta — amplitude acima do normal'
            cor_vol = C['yellow']
        elif amp_rel <= 0.7:
            numero_vol = f'{amp_rel:.1f}× ATR'
            conclusao_vol = 'Volatilidade baixa — mercado comprimido'
            cor_vol = C['text2']
        else:
            numero_vol = f'{amp_rel:.1f}× ATR'
            conclusao_vol = 'Volatilidade normal'
            cor_vol = C['green']
        atr_str = f'ATR: {_fmt(atr_val, 0)} pts' if not _nan(atr_val) else ''
        detalhe_vol = f'{atr_str} · amplitude do dia vs. média 14 candles'
    else:
        numero_vol, conclusao_vol, detalhe_vol, cor_vol = '—', 'Calculando...', '', C['text2']

    card_vol = _card_mv('Volatilidade', numero_vol, conclusao_vol, detalhe_vol, cor_vol)

    # --- CARD 4: BUILD-UP
    if m.get('buildup_ativo'):
        dur = m.get('buildup_duracao', 0)
        pct = m.get('buildup_pct_atr', np.nan)
        numero_bu = f'{dur} candles'
        conclusao_bu = 'Compressão ativa — tensão acumulando'
        detalhe_bu = f'Amplitude {pct:.0%} do ATR · janela de 8 candles' if not _nan(pct) else ''
        cor_bu = C['gold']
    else:
        numero_bu = 'Inativo'
        conclusao_bu = 'Sem compressão detectada'
        detalhe_bu = 'Barras com amplitude normal'
        cor_bu = C['text2']

    card_bu = _card_mv('Build-up', numero_bu, conclusao_bu, detalhe_bu, cor_bu)

    # --- CARD 5: DOUBLE PRESSURE
    dp  = m.get('double_pressure', False)
    dpq = str(m.get('dp_qualidade', '—'))
    dps = m.get('dp_streak', 0)
    cor_dp = {'alta': C['green'], 'media': C['yellow'],
              'baixa': C['text2'], 'indefinido': C['text2']}.get(dpq, C['text2'])

    if dp and dps > 0:
        numero_dp = f'{dps} candles'
        mapa_dp = {
            'alta':  'Double pressure alta — fuga com qualidade superior',
            'media': 'Double pressure média — fluxo alinhado com direção',
            'baixa': 'Double pressure fraca — sinal inicial',
        }
        conclusao_dp = mapa_dp.get(dpq, 'Double pressure detectado')
        detalhe_dp = 'Delta e direção do candle alinhados'
    else:
        numero_dp = 'Não detectado'
        conclusao_dp = 'Fluxo sem alinhamento com direção'
        detalhe_dp = ''
        cor_dp = C['text2']

    card_dp = _card_mv('Double Pressure', numero_dp, conclusao_dp, detalhe_dp, cor_dp)

    # --- CARD 6: JANELA DO PREGÃO
    from datetime import time as dtime
    t_atual = datetime.now().time()
    janela_nome = 'Fora do pregão'
    proxima_janela = ''
    for nome, (ini, fim) in cfg.JANELAS_PREGAO.items():
        h_i, m_i = map(int, ini.split(':'))
        h_f, m_f = map(int, fim.split(':'))
        if dtime(h_i, m_i) <= t_atual < dtime(h_f, m_f):
            janela_nome = nome.upper()
            proxima_janela = f'Vai até {fim}'
            break

    mapa_janela = {
        'ABERTURA': 'Leilão e spread alto — atenção redobrada',
        'PRIME':    'Janela de alta atividade — setups mais confiáveis',
        'ALMOCO':   'Melhor janela histórica do Jesué',
        'TARDE':    'Volume reduzindo — setups mais seletivos',
        'FINAL':    'Fechamento de posições — volatilidade errática',
    }
    conclusao_jan = mapa_janela.get(janela_nome, 'Mercado fechado ou fora do pregão')
    hora_atual = datetime.now().strftime('%H:%M')

    card_jan = _card_mv('Janela', janela_nome, conclusao_jan,
                         f'{hora_atual} · {proxima_janela}', C['gold'])

    return html.Div([
        secao('MARKET VIEW'),
        html.Div([card_qm, card_vol, card_bu, card_dp, card_jan],
                 style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
    ])


# =============================================================================
# BLOCOS DE FORÇA (combustível/aproximação/rejeição) + camada de ação —
# 2026-07-13, ver especificacao_forca_zonas_price_action.md. Módulo-level
# (não fechado sobre nenhum escopo) pra ser reaproveitado tanto por
# _card_nivel_sr (Price Action, dentro de construir_price_action) quanto por
# _card_estudo_tracado (Estudos/Traçados, função top-level) — mesmos dados
# (metricas._extrair_niveis/analisar_tracados), mesma leitura.
# =============================================================================

def _rotulo_bloco(v):
    if pd.isna(v): return None
    if v >= 65: return 'forte'
    if v >= 45: return 'moderado(a)'
    return 'fraco(a)'


_POSTURA_TXT = {
    'favorece_entrada': 'Confluência favorável entre os 3 blocos — favorece considerar entrada.',
    'aguardar_confirmacao': 'Sinal presente, mas incompleto — aguardar confirmação adicional antes de operar.',
    'sinal_fraco': 'Fatores conflitantes entre os blocos, ou zona já desgastada — sinal fraco, operar reduzido se operar.',
    'evitar': 'Fatores predominantemente contrários, ou dados insuficientes — evitar operar nesta zona agora.',
}
_MAGNITUDE_TXT = {
    'curta': 'Combustível baixo — se reagir, espere um movimento curto.',
    'moderada': 'Combustível mediano — se reagir, espere um movimento moderado.',
    'longa': 'Zona ainda fresh (pouco desgastada) — se reagir, há potencial pra um movimento mais longo.',
}
_POSTURA_LABEL = {
    'favorece_entrada': ('FAVORECE ENTRADA', C['green'], C['bg']),
    'aguardar_confirmacao': ('AGUARDAR CONFIRMAÇÃO', C['yellow'], C['bg']),
    'sinal_fraco': ('SINAL FRACO', C['text2'], C['text']),
    'evitar': ('EVITAR', C['red'], C['text']),
}


def _leitura_nivel(nivel: dict, eh_resistencia: bool) -> str:
    """Leitura gerada A PARTIR dos 3 blocos de força desta zona específica
    (2026-07-13) — substitui o texto fixo por tendência+lado que antes
    produzia o MESMO parágrafo pra zonas com toques/idade bem diferentes
    (achado real, ver especificacao_forca_zonas_price_action.md, seção 1 —
    o texto antigo nunca usava `toques` no corpo)."""
    comb, aprox, rej = nivel.get('combustivel_score'), nivel.get('aproximacao_score'), nivel.get('rejeicao_score')
    partes = []
    if not pd.isna(comb):
        partes.append(f'Combustível {_rotulo_bloco(comb)} ({comb:.0f}) — ' + (
            'zona ainda guarda potencial de percurso, pouco desgastada.' if comb >= 55 else
            'zona já consumiu boa parte da liquidez represada (muitos toques/idade).' if comb < 45 else
            'combustível mediano.'))
    if not pd.isna(aprox):
        partes.append(f'Aproximação {_rotulo_bloco(aprox)} ({aprox:.0f}) — ' + (
            'a perna até aqui mostrou convicção (estrutura limpa, sem sinais de exaustão).' if aprox >= 55 else
            'a perna até aqui mostrou sinais de esforço crescente sem resultado — possível exaustão.' if aprox < 45 else
            'aproximação neutra.'))
    if not pd.isna(rej):
        partes.append(f'Rejeição {_rotulo_bloco(rej)} ({rej:.0f}) — ' + (
            'reação decisiva nos toques mais recentes.' if rej >= 55 else
            'reação fraca ou indecisa nos toques mais recentes.' if rej < 45 else
            'reação mediana.'))
    if not partes:
        return 'Dados insuficientes pra decompor a força desta zona ainda.'

    acao = nivel.get('acao') or {}
    postura = acao.get('postura')
    if postura in _POSTURA_TXT:
        partes.append(_POSTURA_TXT[postura])
    if postura in ('favorece_entrada', 'aguardar_confirmacao'):
        mag = _MAGNITUDE_TXT.get(acao.get('magnitude_esperada'))
        if mag:
            partes.append(mag)
    return ' '.join(partes)


def _bloco_scores_html(nivel: dict) -> html.Div:
    """Camada 'dados brutos' dos 3 blocos — números explícitos, não
    escondidos dentro do texto de interpretação (seção 6.1/8 da spec: nada
    de black box)."""
    itens = [('Combustível', nivel.get('combustivel_score')),
             ('Aproximação', nivel.get('aproximacao_score')),
             ('Rejeição', nivel.get('rejeicao_score'))]
    spans = [html.Span(f'{nome} {v:.0f}', style={
        'fontSize': '10px', 'color': C['text2'], 'fontFamily': FONTS['mono'],
        'border': f'1px solid {C["border"]}', 'borderRadius': '4px',
        'padding': '1px 6px', 'marginRight': '4px',
    }) for nome, v in itens if not pd.isna(v)]
    return html.Div(spans, style={'marginBottom': '6px'}) if spans else html.Div()


def _acao_html(nivel: dict, eh_resistencia: bool) -> html.Div:
    """Camada 'ação' (seção 6.3) — postura sempre via regra explícita
    (montar_acao), nunca texto livre; invalidação só aparece quando a
    postura favorece entrada."""
    acao = nivel.get('acao') or {}
    postura = acao.get('postura')
    if postura not in _POSTURA_LABEL:
        return html.Div()
    label, cor_bg, cor_texto = _POSTURA_LABEL[postura]
    partes = [html.Span(label, style={
        'fontSize': '9px', 'fontWeight': '700', 'color': cor_texto,
        'background': cor_bg, 'borderRadius': '4px', 'padding': '2px 6px',
        'marginRight': '6px', 'letterSpacing': '0.05em',
    })]
    mag = acao.get('magnitude_esperada')
    if mag:
        partes.append(html.Span(f'magnitude {mag}', style={
            'fontSize': '10px', 'color': C['text2'], 'marginRight': '8px',
        }))
    alvo = acao.get('alvo_pontos')
    stop = acao.get('stop_pontos')
    if alvo is not None and stop is not None:
        partes.append(html.Span(f'alvo ~{alvo:,.0f}pts · stop ~{stop:,.0f}pts', style={
            'fontSize': '10px', 'color': C['text2'], 'marginRight': '8px', 'fontFamily': FONTS['mono'],
        }))
    inval = acao.get('invalidacao')
    if inval is not None and not pd.isna(inval):
        direcao_txt = 'acima de' if eh_resistencia else 'abaixo de'
        partes.append(html.Span(f'invalida se romper {direcao_txt} {_fmt(inval, 0)}', style={
            'fontSize': '10px', 'color': C['text2'],
        }))
    return html.Div(partes, style={'marginBottom': '6px'})


def construir_price_action(df: pd.DataFrame, m: dict, expandidos: dict = None) -> html.Div:
    """
    Bloco de preço atual + sinais táticos independentes do detector
    automático de zonas (desativado — Modo Aprendizagem.txt, 2026-07-20).
    Fundido na mesma seção que os Estudos de Traçados (ver
    construir_estudos_tracados) — o "PRICE ACTION" de hoje é: preço atual/
    tendência + pivô imediato/em formação aqui, seguido da análise completa
    de cada linha desenhada manualmente.

    Pivô imediato (último fundo/topo CONFIRMADO, ainda do lado certo do
    preço — calcular_pivo_imediato em features.py) e pivô em formação
    (candidato ainda sem confirmação — detectar_pivo_em_formacao) são
    primitivas independentes do antigo ranking de zonas — continuam
    computadas e exibidas mesmo com o detector de zonas desligado.
    """
    expandidos = expandidos or {}
    preco = m.get('preco', np.nan)
    preco_fmt = _fmt(preco, 0)

    tend   = m.get('tendencia') or '—'
    fase   = m.get('fase') or '—'
    saude  = m.get('saude') or '—'
    candle = str(m.get('tipo_candle', '—')).replace('_', ' ').title()


    def _card_pivo_imediato(pivo: dict, eh_resistencia: bool) -> html.Div:
        """Card discreto (borda tracejada) pro pivô imediato — ver docstring da função pai."""
        cor = C['red'] if eh_resistencia else C['green']
        label = 'Topo em formação' if eh_resistencia else 'Fundo em formação'
        return html.Div([
            html.Span(f'{label}: ', style={
                'fontSize': '11px', 'color': C['text2'], 'fontFamily': FONTS['display'],
            }),
            html.Span(_fmt(pivo['preco'], 0), style={
                'fontSize': '14px', 'fontWeight': '700',
                'fontFamily': FONTS['mono'], 'color': cor,
            }),
            html.Span(f'  · {pivo["idade"]} candles atrás · 1º toque, sem reteste', style={
                'fontSize': '11px', 'color': C['text2'], 'fontFamily': FONTS['display'],
            }),
        ], style={
            'background': C['bg'], 'border': f'1px dashed {C["border"]}',
            'borderRadius': '8px', 'padding': '8px 12px', 'marginBottom': '10px',
        })

    def _card_pivo_em_formacao(pivo: dict, eh_resistencia: bool) -> html.Div:
        """Card discreto (borda pontilhada) pro candidato a topo/fundo AINDA
        sem confirmação — distinto de `_card_pivo_imediato` (que só mostra
        pivôs JÁ confirmados). Responde "esse preço extremo (ex.: mínima do
        dia) já é um fundo validado, ou ainda está esperando confirmação?"
        (achado real 2026-07-13 — ver features.detectar_pivo_em_formacao)."""
        cor = C['red'] if eh_resistencia else C['green']
        label = 'Topo em formação' if eh_resistencia else 'Fundo em formação'
        confirmadas, faltam = pivo['confirmadas'], pivo['faltam']
        oposto = 'menores' if eh_resistencia else 'maiores'
        return html.Div([
            html.Span(f'{label}: ', style={
                'fontSize': '11px', 'color': C['text2'], 'fontFamily': FONTS['display'],
            }),
            html.Span(_fmt(pivo['preco'], 0), style={
                'fontSize': '14px', 'fontWeight': '700',
                'fontFamily': FONTS['mono'], 'color': cor,
            }),
            html.Span('  AINDA SEM CONFIRMAÇÃO', style={
                'fontSize': '10px', 'fontWeight': '700', 'color': C['bg'],
                'background': C['yellow'], 'borderRadius': '4px', 'padding': '1px 6px',
                'letterSpacing': '0.05em', 'marginLeft': '4px',
            }),
            html.Div(
                f'{confirmadas} de {confirmadas + faltam} candles de confirmação — faltam {faltam} '
                f'candle{"s" if faltam != 1 else ""} com máxima e mínima {oposto} pra virar '
                f'{"topo" if eh_resistencia else "fundo"} validado.',
                style={'fontSize': '11px', 'color': C['text2'], 'fontFamily': FONTS['display'], 'marginTop': '4px'},
            ),
        ], style={
            'background': C['bg'], 'border': f'1px dotted {C["border"]}',
            'borderRadius': '8px', 'padding': '8px 12px', 'marginBottom': '10px',
        })

    # Bloco de preço atual
    cor_candle = C['green'] if 'alta' in str(m.get('tipo_candle','')) or 'comprador' in str(m.get('tipo_candle','')) else C['red']
    bloco_preco = html.Div([
        html.Div([
            html.Div('PREÇO ATUAL', style={
                'fontSize': '10px', 'color': C['text2'],
                'fontFamily': FONTS['display'], 'letterSpacing': '0.08em',
                'marginBottom': '4px',
            }),
            html.Div(preco_fmt, style={
                'fontSize': '32px', 'fontWeight': '700',
                'fontFamily': FONTS['mono'], 'color': C['text'],
            }),
            html.Div(candle, style={
                'fontSize': '13px', 'color': cor_candle,
                'fontFamily': FONTS['display'], 'marginTop': '4px',
            }),
        ], style={'flex': '1'}),
        html.Div([
            html.Div('TENDÊNCIA', style={
                'fontSize': '10px', 'color': C['text2'],
                'fontFamily': FONTS['display'], 'letterSpacing': '0.08em',
                'marginBottom': '4px',
            }),
            html.Div(tend.upper(), style={
                'fontSize': '20px', 'fontWeight': '700',
                'fontFamily': FONTS['mono'],
                'color': C['green'] if tend=='alta' else (C['red'] if tend=='baixa' else C['yellow']),
            }),
            html.Div(f'{fase} · {saude}', style={
                'fontSize': '12px', 'color': C['text2'],
                'fontFamily': FONTS['display'], 'marginTop': '2px',
            }),
        ], style={'flex': '1'}),
    ], style={
        'display': 'flex', 'gap': '24px',
        'background': C['panel'], 'border': f'1px solid {C["border"]}',
        'boxShadow': 'var(--shadow-card)',
        'borderRadius': '12px', 'padding': '20px',
        'marginBottom': '12px',
    })

    # Zonas de S/R AUTOMÁTICAS removidas (Modo Aprendizagem.txt, 2026-07-20)
    # — o motor não sugere mais S/R sozinho, a análise completa agora mora
    # nos cards de Estudos (ver construir_estudos_tracados/
    # metricas.analisar_tracados), fundidos nesta mesma seção. Aqui sobram
    # só os 2 sinais táticos independentes do detector de zonas: pivô
    # imediato (último topo/fundo já CONFIRMADO) e pivô em formação (ainda
    # sem confirmação) — nenhum dos dois depende de zona auto-detectada.
    cards_resistencia = []
    cards_suporte = []

    pivo_acima = m.get('pivo_imediato_acima')
    if pivo_acima:
        cards_resistencia.append(_card_pivo_imediato(pivo_acima, eh_resistencia=True))
    pivo_abaixo = m.get('pivo_imediato_abaixo')
    if pivo_abaixo:
        cards_suporte.append(_card_pivo_imediato(pivo_abaixo, eh_resistencia=False))

    pivo_formacao_acima = m.get('pivo_em_formacao_acima')
    if pivo_formacao_acima:
        cards_resistencia.append(_card_pivo_em_formacao(pivo_formacao_acima, eh_resistencia=True))
    pivo_formacao_abaixo = m.get('pivo_em_formacao_abaixo')
    if pivo_formacao_abaixo:
        cards_suporte.append(_card_pivo_em_formacao(pivo_formacao_abaixo, eh_resistencia=False))

    if not cards_resistencia:
        cards_resistencia = [html.Div('Nenhum pivô tático acima do preço no momento.',
                                       style={'color': C['text2'], 'fontSize': '12px'})]
    if not cards_suporte:
        cards_suporte = [html.Div('Nenhum pivô tático abaixo do preço no momento.',
                                   style={'color': C['text2'], 'fontSize': '12px'})]

    return html.Div([
        bloco_preco,
        html.Div([
            html.Div(cards_resistencia),
            html.Div(cards_suporte),
        ], style={
            'display': 'grid',
            'gridTemplateColumns': '1fr 1fr',
            'gap': '12px',
        }),
    ])


def analisar_big_players_resumo(df: pd.DataFrame) -> dict:
    """
    Big Players Footprints — condensado num único resumo (Fase 4, Modo
    Aprendizagem, 2026-07-21) pro velocímetro do hero, ao lado de
    Correlacionados. Antes era uma seção própria (~200 linhas, 4 cards) —
    Jesué: "não teve nenhuma relevância pra mim até agora. Vamos
    desativar por enquanto. Resuma... em um indicador só". Mesma lógica
    de análise de sempre (lado dominante/absorção/volume anômalo/ação),
    só devolvendo um dict em vez de renderizar 4 cards — o velocímetro
    mostra o % do lado dominante, os outros 3 fatos + a ação ficam no
    balão explicativo ao clicar (`_balao_big_players`, mesmo padrão de
    clique dos outros gauges).
    """
    vazio = {'tem_dado': False, 'mensagem': 'Sem dados.'}
    if df is None or df.empty:
        return vazio

    # Trabalha com os últimos N candles (snapshot de análise, 5 pregões)
    snap = df.tail(cfg.JANELA_SNAPSHOT_CURTO_PRAZO).copy().reset_index(drop=True)
    tem_fluxo = (snap['fluxo_disponivel'] == True) if 'fluxo_disponivel' in snap.columns else pd.Series([False]*len(snap))
    snap_fluxo = snap[tem_fluxo].reset_index(drop=True)

    if snap_fluxo.empty:
        return {
            'tem_dado': False,
            'mensagem': (
                'Fluxo de tick não disponível neste período. A análise de big players '
                'requer dado de tick — disponível apenas para os contratos mais recentes.'
            ),
        }

    # -------------------------------------------------------------------------
    # ANÁLISE 1 — LADO DOMINANTE (últimos 20 candles com fluxo)
    # -------------------------------------------------------------------------
    ultimos_fluxo = snap_fluxo.tail(20)
    vol_c = pd.to_numeric(ultimos_fluxo.get('vol_comprador', pd.Series([0]*len(ultimos_fluxo))), errors='coerce').fillna(0)
    vol_v = pd.to_numeric(ultimos_fluxo.get('vol_vendedor',  pd.Series([0]*len(ultimos_fluxo))), errors='coerce').fillna(0)
    delta_col = pd.to_numeric(ultimos_fluxo.get('delta', pd.Series([0]*len(ultimos_fluxo))), errors='coerce').fillna(0)

    total_c = vol_c.sum()
    total_v = vol_v.sum()
    total   = total_c + total_v
    delta_acum = delta_col.sum()

    if total > 0:
        pct_c = round(total_c / total * 100)
        pct_v = 100 - pct_c
    else:
        pct_c = pct_v = 50

    # Detecta se há divergência: candles de alta com delta negativo (absorção de venda)
    # ou candles de baixa com delta positivo (absorção de compra)
    dir_col = pd.to_numeric(ultimos_fluxo.get('direcao_candle', pd.Series([0]*len(ultimos_fluxo))), errors='coerce').fillna(0)
    divergencias = ((dir_col == 1) & (delta_col < 0)) | ((dir_col == -1) & (delta_col > 0))
    n_diverg = divergencias.sum()
    pct_diverg = round(n_diverg / len(ultimos_fluxo) * 100) if len(ultimos_fluxo) > 0 else 0

    if pct_c > 60:
        lado_dominante = f'Compradores {pct_c}%'
        conclusao_lado = 'Agressão compradora dominante — demanda real acima da oferta'
        cor_lado = 'green'
    elif pct_v > 60:
        lado_dominante = f'Vendedores {pct_v}%'
        conclusao_lado = 'Agressão vendedora dominante — pressão de venda real'
        cor_lado = 'red'
    else:
        lado_dominante = f'C {pct_c}% · V {pct_v}%'
        conclusao_lado = 'Mercado equilibrado — sem dominância clara de um lado'
        cor_lado = 'yellow'

    detalhe_lado = (f'Delta acum: {int(delta_acum):+,} · {len(ultimos_fluxo)} candles com tick · '
                    f'{pct_diverg}% de divergência candle/delta')

    # -------------------------------------------------------------------------
    # ANÁLISE 2 — ABSORÇÃO (sinal de big player comprando/vendendo contra o movimento)
    # -------------------------------------------------------------------------
    # Absorção: candles de baixa mas compradores agredindo (big player comprando na queda)
    # ou candles de alta mas vendedores agredindo (big player vendendo na alta)
    absorcao_compra  = ((dir_col == -1) & (delta_col > 0)).sum()  # compra em queda
    absorcao_venda   = ((dir_col == 1)  & (delta_col < 0)).sum()  # venda em alta
    total_candles_f  = len(ultimos_fluxo)

    if absorcao_compra > total_candles_f * 0.3:
        absorcao_texto = f'Absorção compradora detectada ({absorcao_compra} candles) — big player comprando na queda'
    elif absorcao_venda > total_candles_f * 0.3:
        absorcao_texto = f'Absorção vendedora detectada ({absorcao_venda} candles) — distribuição em alta'
    else:
        absorcao_texto = f'Sem sinal claro de absorção institucional (compra: {absorcao_compra} · venda: {absorcao_venda} candles)'

    # -------------------------------------------------------------------------
    # ANÁLISE 3 — CANDLES DE MAIOR VOLUME (possíveis negócios grandes)
    # -------------------------------------------------------------------------
    # Usa snap_fluxo completo (500 candles) para detectar outliers de volume
    # vol_c e vol_v acima vieram de ultimos_fluxo (20 candles) — recalcula aqui
    vol_c_full = pd.to_numeric(snap_fluxo.get('vol_comprador', pd.Series([0]*len(snap_fluxo))), errors='coerce').fillna(0)
    vol_v_full = pd.to_numeric(snap_fluxo.get('vol_vendedor',  pd.Series([0]*len(snap_fluxo))), errors='coerce').fillna(0)
    vol_total_col = (vol_c_full + vol_v_full)

    if not vol_total_col.empty and vol_total_col.sum() > 0:
        media_vol = vol_total_col.mean()
        std_vol   = vol_total_col.std()
        mascara   = (vol_total_col > (media_vol + 2 * std_vol)).values  # numpy array evita IndexingError
        outliers  = snap_fluxo[mascara].tail(3)

        if not outliers.empty:
            ultimo_outlier = outliers.iloc[-1]
            preco_ol = ultimo_outlier.get('close', '—')
            delta_ol = int(pd.to_numeric(ultimo_outlier.get('delta', 0), errors='coerce') or 0)
            dt_ol    = pd.to_datetime(ultimo_outlier.get('datetime', '')).strftime('%H:%M') if pd.notna(ultimo_outlier.get('datetime')) else '—'
            lado_ol  = 'comprador' if delta_ol > 0 else 'vendedor'

            volume_texto = (f'Volume anômalo detectado às {dt_ol} em {_fmt(preco_ol, 0)} — delta {delta_ol:+,} '
                            f'(lado {lado_ol} dominou), {int(mascara.sum())} eventos acima de 2σ')
        else:
            volume_texto = f'Volume distribuído normalmente — sem eventos institucionais óbvios (média {_fmt(media_vol)})'
    else:
        volume_texto = 'Sem dado de tick suficiente para detectar anomalias de volume'

    # -------------------------------------------------------------------------
    # ANÁLISE 4 — O QUE FAZER COM ESSA INFORMAÇÃO
    # -------------------------------------------------------------------------
    # Leitura consolidada acionável
    if pct_c > 60 and absorcao_compra > total_candles_f * 0.2:
        acao = 'Compradores dominam e absorvem a venda — cenário favorável para longs em S'
        cor_acao = 'green'
    elif pct_v > 60 and absorcao_venda > total_candles_f * 0.2:
        acao = 'Vendedores dominam e distribuem em alta — cenário favorável para shorts em R'
        cor_acao = 'red'
    elif n_diverg > total_candles_f * 0.4:
        acao = f'Alta divergência candle/delta ({pct_diverg}%) — mercado em transição, aguardar definição'
        cor_acao = 'yellow'
    else:
        acao = 'Fluxo sem sinal direcional claro — aguardar setup com confluência de S/R e tendência'
        cor_acao = 'text2'

    return {
        'tem_dado': True,
        'pct_c': pct_c, 'pct_v': pct_v,
        'lado_dominante': lado_dominante, 'conclusao_lado': conclusao_lado, 'cor_lado': cor_lado,
        'detalhe_lado': detalhe_lado,
        'absorcao_texto': absorcao_texto,
        'volume_texto': volume_texto,
        'acao_texto': acao, 'cor_acao': cor_acao,
    }


_ROTULOS_RASTREADOR = {
    'maior': 'Tendência Maior (5 pregões)',
    'media': 'Tendência Média (2 pregões)',
    'menor': 'Tendência Menor (1 pregão)',
    'micro': 'Tendência Micro',
    'atual': 'Tendência Atual (21 candles)',
}


_COR_TENDENCIA_TIER = {'alta': 'green', 'baixa': 'red', 'lateral': 'text2'}
_LABEL_TENDENCIA_TIER = {'alta': '▲ ALTA', 'baixa': '▼ BAIXA', 'lateral': '— LATERAL'}


def construir_rastreador_tendencia(analises: dict) -> html.Div:
    """Rastreador de Tendência (Modo Aprendizagem.txt, 2026-07-20) — MME21
    é 1 bloco ÚNICO (`_card_mme21`, mesma leitura de sempre: toques/força/
    prob_bounce, janela própria `cfg.JANELA_RESPEITO_MME21`), ao lado de 5
    cards leves de tendência por escala (Maior/Média/Menor/Micro/Atual —
    `_card_tier_tendencia`, só o rótulo alta/baixa/lateral).

    Corrige achado real de Jesué 2026-07-21: a versão anterior rodava o
    motor de TOQUES da MME21 em CADA tier (janela=565/226/113/56),
    produzindo textos do tipo "MME21 testada e respeitada nos últimos 565
    candles" — uma afirmação sem sentido pra uma média rápida (equivaleria
    a dizer que ela ficou 5 pregões inteiros sem ser cruzada, o que quase
    nunca é verdade). A spec original já pedia os dois conceitos
    SEPARADOS: "Traga a MME21 pra essa seção da MESMA FORMA que já mostra"
    (1 bloco, como sempre foi) + "Tendência Atual" (mais um rótulo de
    direção, não outra rodada do motor de toques).

    `analises` vem de `metricas.analisar_rastreador_tendencia` — formato
    `{'tiers': {nivel: {...}}, 'mme21': {...}}`."""
    if not analises:
        return html.Div('Sem dado suficiente pra calcular a MME21 ainda.',
                         style={'color': C['text2'], 'fontFamily': FONTS['body'], 'padding': '20px'})
    tiers = analises.get('tiers') or {}
    mme21 = analises.get('mme21')
    cards = [
        _card_tier_tendencia(_ROTULOS_RASTREADOR[nivel], tiers[nivel])
        for nivel in ('maior', 'media', 'menor', 'micro', 'atual')
        if nivel in tiers
    ]
    return html.Div([
        html.Div(cards, style={
            'display': 'grid', 'gridTemplateColumns': 'repeat(auto-fit, minmax(160px, 1fr))', 'gap': '10px',
            'marginBottom': '12px',
        }),
        _card_mme21(mme21),
    ])


def _card_tier_tendencia(rotulo: str, tier: dict) -> html.Div:
    """1 card leve de tendência (Maior/Média/Menor/Micro/Atual) — SÓ o
    rótulo alta/baixa/lateral daquela escala (`metricas.
    _classificar_tendencia_serie`), sem repetir a análise de toques/força
    da MME21 (isso é `_card_mme21`, único). Ver docstring de
    `construir_rastreador_tendencia` pro raciocínio completo."""
    _rotulo_nivel = html.Div(rotulo, style={
        'fontSize': '11px', 'color': C['gold'], 'fontFamily': FONTS['display'],
        'fontWeight': '600', 'textTransform': 'uppercase', 'letterSpacing': '0.06em',
        'marginBottom': '8px',
    })
    tend = (tier or {}).get('tendencia')
    if not tend:
        corpo = html.Div('Sem dado suficiente ainda.', style={'color': C['text2'], 'fontFamily': FONTS['body'], 'fontSize': '12px'})
    else:
        corpo = html.Div(_LABEL_TENDENCIA_TIER.get(tend, tend.upper()), style={
            'fontSize': '15px', 'fontWeight': '700', 'color': C[_COR_TENDENCIA_TIER.get(tend, 'text2')],
            'letterSpacing': '0.04em',
        })
    return html.Div([_rotulo_nivel, corpo], style={
        'background': C['panel'], 'border': f'1px solid {C["border"]}',
        'boxShadow': 'var(--shadow-card)', 'borderRadius': '10px', 'padding': '14px',
    })


def _card_mme21(analise: dict) -> html.Div:
    """Card ÚNICO da MME21 — toques/força/probabilidade/leitura na sua
    própria janela natural (`cfg.JANELA_RESPEITO_MME21`), doutrina de
    sempre (nunca operar em mercado lateral). Igual ao card que existia
    antes desta seção virar um Rastreador de 5 tiers (2026-07-20) — só
    voltou a ser 1 bloco, como a spec original pedia."""
    _rotulo = html.Div('MME21 (EMA 21)', style={
        'fontSize': '11px', 'color': C['gold'], 'fontFamily': FONTS['display'],
        'fontWeight': '600', 'textTransform': 'uppercase', 'letterSpacing': '0.06em',
        'marginBottom': '8px',
    })

    if not analise or analise.get('linha_atual') is None:
        return html.Div([_rotulo, html.Div(
            'Sem dado suficiente pra calcular a MME21 ainda.',
            style={'color': C['text2'], 'fontFamily': FONTS['body']},
        )], style={
            'background': C['panel'], 'border': f'1px solid {C["border"]}',
            'borderRadius': '10px', 'padding': '16px',
        })

    linha_atual = analise['linha_atual']
    numero = html.Div(f'{linha_atual:,.0f}', style={
        'fontSize': '26px', 'fontWeight': '700', 'fontFamily': FONTS['mono'], 'color': C['text'],
        'marginBottom': '10px',
    })

    if not analise.get('operavel', True):
        return html.Div([
            _rotulo, numero,
            html.Div('⚠ MERCADO LATERAL', style={
                'fontSize': '13px', 'fontWeight': '700', 'color': C['bg'],
                'background': C['yellow'], 'padding': '6px 10px', 'borderRadius': '6px',
                'marginBottom': '10px', 'letterSpacing': '0.05em', 'display': 'inline-block',
            }),
            html.Div(analise.get('mensagem') or 'MME21 não deve ser operada em mercado lateral.', style={
                'fontSize': '13px', 'color': C['text'], 'fontFamily': FONTS['body'],
                'lineHeight': '1.5',
            }),
        ], style={
            'background': C['panel'], 'border': f'1px solid {C["border"]}',
            'boxShadow': 'var(--shadow-card)',
            'borderRadius': '10px', 'padding': '16px',
        })

    tipo = analise.get('tipo', 'suporte')
    eh_suporte = (tipo == 'suporte')
    cor = C['green'] if eh_suporte else C['red']
    prob = analise.get('prob_bounce', 50.0)
    toques = analise.get('toques', 0)
    janela = analise.get('janela', cfg.JANELA_RESPEITO_MME21)
    forte = analise.get('forte', False)
    violado = analise.get('violado', False)

    badges = []
    if forte:
        badges.append(html.Span('MME21 FORTE', style={
            'fontSize': '10px', 'fontWeight': '700', 'color': C['bg'],
            'background': C['gold'], 'borderRadius': '4px',
            'padding': '2px 8px', 'marginRight': '6px', 'letterSpacing': '0.05em',
        }))
    if violado:
        badges.append(html.Span('VIOLADA', style={
            'fontSize': '10px', 'fontWeight': '700', 'color': '#fff',
            'background': C['red'], 'borderRadius': '4px',
            'padding': '2px 8px', 'marginRight': '6px', 'letterSpacing': '0.05em',
        }))

    if violado:
        leitura = (f'MME21 rompida — perdeu o papel de {"suporte" if eh_suporte else "resistência"} '
                   f'nos últimos {janela} candles. Não é referência confiável enquanto durar o rompimento.')
    elif toques == 0:
        leitura = (f'Preço não testou a MME21 nos últimos {janela} candles — sem toques recentes '
                   f'pra confirmar se ela seria respeitada.')
    elif forte:
        leitura = (f'MME21 testada e respeitada nos últimos {janela} candles — considerar como '
                   f'{"suporte" if eh_suporte else "resistência"} dinâmica em dia de tendência, '
                   f'nunca em mercado lateral.')
    else:
        leitura = (f'MME21 tocada {toques}x nos últimos {janela} candles, ainda sem confirmação forte — '
                   f'acompanhar antes de considerar como referência operacional.')

    return html.Div([
        _rotulo, numero,
        html.Div(tipo.upper(), style={
            'fontSize': '12px', 'color': cor, 'fontFamily': FONTS['display'],
            'fontWeight': '700', 'letterSpacing': '0.05em', 'marginBottom': '8px',
        }),
        html.Div(badges, style={'marginBottom': '8px'}) if badges else html.Div(),
        html.Div(f'{toques} toque{"s" if toques != 1 else ""} nos últimos {janela} candles', style={
            'fontSize': '13px', 'color': C['text2'], 'fontFamily': FONTS['display'], 'marginBottom': '6px',
        }),
        html.Div(f'Probabilidade: {prob:.0f}% bounce · {100-prob:.0f}% rompimento', style={
            'fontSize': '13px', 'fontWeight': '600', 'color': cor,
            'fontFamily': FONTS['display'], 'marginBottom': '8px',
        }),
        html.Div(leitura, style={
            'fontSize': '13px', 'color': C['text'], 'fontFamily': FONTS['body'],
            'lineHeight': '1.5', 'borderLeft': f'3px solid {cor}', 'paddingLeft': '10px',
        }),
    ], style={
        'background': C['panel'], 'border': f'1px solid {C["border"]}',
        'boxShadow': 'var(--shadow-card)',
        'borderRadius': '10px', 'padding': '16px',
    })


def _linha_toque_html(d: dict) -> html.Div:
    """Uma linha da timeline expandida de toques — data/hora, qualidade do
    pivô (forte/fraco) e rejeição, formato compacto."""
    return html.Div(
        f"{_fmt_ts(d['timestamp'])} — toque {d['tipo_pivo']} em {d['preco']:,.0f} "
        f"(rejeição {d['rejeicao']*100:.0f}%)",
        style={'fontSize': '11px', 'color': C['text2'], 'fontFamily': FONTS['mono'],
               'padding': '1px 0'},
    )


def _card_estudo_tracado(analise: dict, expandido: bool = False) -> html.Div:
    """Um card por traçado manual — mesma linguagem de
    `construir_rastreador_tendencia`, mais um botão de apagar. Badge FORTE
    e leitura vêm do REGIME ATUAL (`metricas.montar_regime`), não mais do
    agregado do histórico inteiro — achado real (2026-07-13): níveis
    dentro de uma consolidação longa vinham sempre "FORTE" contando toques
    fracos (1 candle de confirmação) espalhados por meses. Mostra também a
    narrativa de bipolaridade quando o traçado coincide com uma zona do
    motor automático que já reverteu de papel (`cruzamento_automatico`)."""
    tipo = analise.get('tipo', 'suporte')
    eh_suporte = (tipo == 'suporte')
    cor = C['green'] if eh_suporte else C['red']
    prob = analise.get('prob_bounce', 50.0)
    violado = analise.get('violado', False)
    janela = analise.get('janela', 0)

    regime = analise.get('regime') or {
        'tem_divisao': False, 'fronteira_timestamp': None,
        'regime_atual': {'toques': analise.get('toques', 0), 'toques_fortes': 0,
                          'forte': analise.get('forte', False), 'detalhes': []},
        'regime_antigo': None,
    }
    atual = regime['regime_atual']
    antigo = regime['regime_antigo']
    forte = atual['forte']
    toques_total = atual['toques'] + (antigo['toques'] if antigo else 0)

    badges = []
    if forte:
        badges.append(html.Span('FORTE', style={
            'fontSize': '10px', 'fontWeight': '700', 'color': C['bg'],
            'background': C['gold'], 'borderRadius': '4px',
            'padding': '2px 8px', 'marginRight': '6px', 'letterSpacing': '0.05em',
        }))
    if violado:
        badges.append(html.Span('VIOLADO', style={
            'fontSize': '10px', 'fontWeight': '700', 'color': '#fff',
            'background': C['red'], 'borderRadius': '4px',
            'padding': '2px 8px', 'marginRight': '6px', 'letterSpacing': '0.05em',
        }))

    cruzamento = analise.get('cruzamento_automatico')
    bip = cruzamento.get('bipolar') if cruzamento else None
    narrativa_bipolar = None
    # Diferente de _card_nivel_sr (Price Action): ali o gate em 'espelho'
    # evita duplicar a badge "ZONA ESPELHO" já mostrada no card da zona
    # ANTIGA. Aqui não existe card da zona antiga — o traçado é um preço
    # isolado — então QUALQUER bipolaridade encontrada (origem ou espelho)
    # já é a informação nova que falta mostrar. `localizar_zona_automatica`
    # hoje só casa pelo zona_min/zona_max da zona ORIGINAL (mesma faixa da
    # espelho, herdada), então `papel_no_cruzamento` vem sempre 'origem'
    # pra um traçado — exigir 'espelho' aqui nunca disparava (bug real,
    # corrigido 2026-07-13).
    if bip:
        badges.append(html.Span('REVERSÃO DE PAPEL', style={
            'fontSize': '10px', 'fontWeight': '700', 'color': '#fff',
            'background': C['blue'], 'borderRadius': '4px',
            'padding': '2px 8px', 'marginRight': '6px', 'letterSpacing': '0.05em',
        }))
        papel_antigo = 'resistência' if eh_suporte else 'suporte'
        narrativa_bipolar = (
            f'Esta região era {papel_antigo} até romper em {_fmt_ts(bip["timestamp_rompimento"])} '
            f'— reteste confirmado às {_fmt_ts(bip["timestamp_reteste"])}, hoje atua como {tipo}.'
        )

    if violado:
        leitura = (f'Nível rompido — perdeu o papel de {"suporte" if eh_suporte else "resistência"} '
                   f'no histórico analisado.')
    elif regime['tem_divisao']:
        fronteira_fmt = _fmt_ts(regime['fronteira_timestamp'])
        if forte:
            leitura = (f'Testado {atual["toques"]}x e respeitado no regime atual (desde {fronteira_fmt}) '
                       f'— mesma doutrina do S/R automático ({"suporte" if eh_suporte else "resistência"} estrutural).')
        elif atual['toques'] == 0:
            if antigo and antigo['toques']:
                leitura = (f'Sem toque ainda no regime atual (desde {fronteira_fmt}). Havia '
                           f'{antigo["toques"]} toques num regime anterior, mas isso não conta pra '
                           f'doutrina de nível estrutural — o comportamento do mercado mudou aqui.')
            else:
                leitura = f'Sem toque registrado desde {fronteira_fmt} — sem histórico recente pra confirmar.'
        else:
            leitura = (f'Tocado {atual["toques"]}x no regime atual (desde {fronteira_fmt}), ainda sem '
                       f'confirmação forte — acompanhar antes de considerar operacional.')
    elif atual['toques'] == 0:
        leitura = 'Ainda sem toque registrado nesse nível — sem histórico pra confirmar se seria respeitado.'
    elif forte:
        leitura = (f'Testado {atual["toques"]}x e respeitado — mesma doutrina do S/R automático '
                   f'({"suporte" if eh_suporte else "resistência"} estrutural).')
    else:
        leitura = f'Tocado {atual["toques"]}x, ainda sem confirmação forte — acompanhar antes de considerar operacional.'

    corpo_timeline = []
    if toques_total:
        corpo_timeline.append(html.Button(
            f"{'▾' if expandido else '▸'} Ver histórico de toques ({toques_total})",
            id={'type': 'timeline-toggle', 'index': analise['id_tracado']}, n_clicks=0,
            style={'background': 'transparent', 'border': 'none', 'color': C['text2'],
                   'fontSize': '11px', 'cursor': 'pointer', 'padding': '4px 0', 'textAlign': 'left'},
        ))
        if expandido:
            if atual['detalhes']:
                corpo_timeline.append(html.Div('REGIME ATUAL', style={
                    'fontSize': '9px', 'fontWeight': '700', 'color': C['text2'],
                    'letterSpacing': '0.06em', 'marginTop': '6px',
                }))
                corpo_timeline += [_linha_toque_html(d) for d in atual['detalhes']]
            if antigo and antigo['detalhes']:
                corpo_timeline.append(html.Div('REGIME ANTERIOR', style={
                    'fontSize': '9px', 'fontWeight': '700', 'color': C['text2'],
                    'letterSpacing': '0.06em', 'marginTop': '8px',
                }))
                corpo_timeline += [_linha_toque_html(d) for d in antigo['detalhes']]

    return html.Div([
        html.Div([
            html.Div([
                html.Div(f"{analise['linha_atual']:,.0f}", style={
                    'fontSize': '22px', 'fontWeight': '700', 'fontFamily': FONTS['mono'], 'color': cor,
                }),
                html.Div(tipo.upper(), style={
                    'fontSize': '11px', 'color': cor, 'fontFamily': FONTS['display'],
                    'fontWeight': '700', 'letterSpacing': '0.05em',
                }),
            ], style={'flex': '1'}),
            html.Button('🗑', id={'type': 'del-tracado', 'index': analise['id_tracado']},
                        n_clicks=0, title='Remover traçado', style={
                            'background': 'transparent', 'border': f'1px solid {C["border"]}',
                            'borderRadius': '6px', 'color': C['text2'], 'cursor': 'pointer',
                            'padding': '4px 10px', 'fontSize': '13px',
                        }),
        ], style={'display': 'flex', 'alignItems': 'flex-start', 'marginBottom': '10px'}),
        html.Div(badges, style={'marginBottom': '8px'}) if badges else html.Div(),
        html.Div(f'{toques_total} toque{"s" if toques_total != 1 else ""} no histórico analisado ({janela} candles)', style={
            'fontSize': '13px', 'color': C['text2'], 'fontFamily': FONTS['display'], 'marginBottom': '6px',
        }),
        html.Div(f'Probabilidade: {prob:.0f}% bounce · {100-prob:.0f}% rompimento', style={
            'fontSize': '13px', 'fontWeight': '600', 'color': cor,
            'fontFamily': FONTS['display'], 'marginBottom': '8px',
        }),
        _bloco_scores_html(analise),
        html.Div(narrativa_bipolar, style={
            'fontSize': '12px', 'color': C['blue'], 'fontFamily': FONTS['body'],
            'lineHeight': '1.4', 'marginBottom': '8px',
        }) if narrativa_bipolar else html.Div(),
        html.Div(leitura, style={
            'fontSize': '13px', 'color': C['text'], 'fontFamily': FONTS['body'],
            'lineHeight': '1.5', 'borderLeft': f'3px solid {cor}', 'paddingLeft': '10px',
            'marginBottom': '8px',
        }),
        _acao_html(analise, not eh_suporte),
        html.Div(corpo_timeline, style={'marginTop': '10px'}) if corpo_timeline else html.Div(),
    ], style={
        'background': C['panel'], 'border': f'1px solid {C["border"]}',
        'boxShadow': 'var(--shadow-card)',
        'borderRadius': '10px', 'padding': '16px', 'marginBottom': '12px',
    })


def construir_estudos_tracados(analises: list, expandidos: dict = None) -> html.Div:
    """Seção "Estudos" da aba Traçados — um card por traçado manual (ver
    `_card_estudo_tracado`). Mesma informação da aba Price Action, mas
    capturando os traçados do gráfico em vez dos níveis automáticos do
    motor de S/R."""
    if not analises:
        return html.Div(
            'Nenhum traçado ainda — use a ferramenta "Nível S/R" na barra acima '
            'para marcar um nível no gráfico.',
            style={'padding': '20px', 'textAlign': 'center', 'color': C['text2'], 'fontSize': '13px'},
        )
    expandidos = expandidos or {}
    return html.Div([_card_estudo_tracado(a, expandidos.get(a['id_tracado'], False)) for a in analises])


_OPCOES_ESPESSURA_TRACADO = [{'label': f'{n}px', 'value': n} for n in (1, 2, 3, 4)]
_OPCOES_ESTILO_TRACADO = [
    {'label': 'Sólida', 'value': 'solid'},
    {'label': 'Tracejada', 'value': 'dashed'},
    {'label': 'Pontilhada', 'value': 'dotted'},
]
# Paleta curada em vez de um seletor de cor nativo — Dash 4.3 não tem
# `html.Input` (não existe no pacote `dash.html` desta versão) e o `type`
# de `dcc.Input` não aceita 'color' (só text/number/password/email/range/
# search/tel/url/hidden, checado em runtime). Swatches reaproveitam as
# cores já usadas no resto do projeto (S/R automático + destaque padrão),
# então cada opção já tem um significado reconhecível, não é decoração.
_CORES_TRACADO = [
    ('#C8A96E', 'Dourado (padrão)'), ('#2ECC8A', 'Verde'), ('#E05C5C', 'Vermelho'),
    ('#4A90D9', 'Azul'), ('#8888AA', 'Cinza'), ('#FFFFFF', 'Branco'),
]


def _swatch_cor_tracado(hexcor: str, titulo: str, selecionada: bool) -> html.Button:
    return html.Button('', id={'type': 'tracado-cor-swatch', 'cor': hexcor}, n_clicks=0,
                        title=titulo, style={
                            'width': '28px', 'height': '28px', 'borderRadius': '6px',
                            'background': hexcor, 'cursor': 'pointer', 'padding': '0',
                            'border': f'2px solid {C["gold"] if selecionada else C["border"]}',
                            'boxShadow': f'0 0 0 2px {C["panel"]}, 0 0 0 3px {C["gold"]}' if selecionada else 'none',
                        })


def _swatches_cor_tracado(cor_atual: str) -> list:
    return [_swatch_cor_tracado(hexcor, titulo, hexcor == cor_atual) for hexcor, titulo in _CORES_TRACADO]


def _modal_editar_tracado(id_tracado: str, ativo: str) -> html.Div | None:
    """Popup de edição de um traçado — aberto por duplo clique na linha
    (ver assets/lwchart.js::wireFerramentaDesenho). Preço/cor/espessura/
    estilo, os 4 campos editáveis de um traçado horizontal (schema em
    tracados.py). Mesmo padrão de overlay (backdrop escurecido + painel
    centralizado) já usado em diario_app_classico.py::_overlay."""
    ativo_real = ativo or 'WIN'
    df = tracados.listar_tracados(ativo_real)
    linha = df[df['id_tracado'] == id_tracado]
    if linha.empty:
        return None
    t = linha.iloc[0]
    cor_atual = t['cor'] if pd.notna(t['cor']) else C['gold']

    def _campo(rotulo, componente):
        return html.Div([
            html.Label(rotulo, style={
                'fontSize': '11px', 'color': C['text2'], 'fontFamily': FONTS['display'],
                'textTransform': 'uppercase', 'letterSpacing': '0.05em',
                'display': 'block', 'marginBottom': '6px',
            }),
            componente,
        ], style={'marginBottom': '16px'})

    estilo_campo = {
        'width': '100%', 'background': C['bg'], 'color': C['text'],
        'border': f'1px solid {C["border"]}', 'borderRadius': '6px',
        'padding': '8px 10px', 'fontFamily': FONTS['mono'], 'fontSize': '14px',
        'boxSizing': 'border-box',
    }

    conteudo = html.Div([
        html.Div('Editar Traçado', style={
            'fontSize': '15px', 'fontWeight': '700', 'fontFamily': FONTS['display'],
            'color': C['text'], 'marginBottom': '16px',
        }),
        _campo('Preço', dcc.Input(
            # round() — `coordinateToPrice()` no clique original devolve
            # até 10+ casas decimais de ruído de ponto flutuante (nunca um
            # preço "redondo"); mostrar isso cru no campo é só lixo visual
            # e piora o risco de erro de digitação. `step='any'` evita o
            # navegador marcar o campo como inválido (e devolver '' pro
            # Dash) em qualquer valor decimal, independente de locale.
            id='tracado-editar-preco', type='number', value=round(float(t['preco']), 2),
            step='any', style=estilo_campo,
        )),
        dcc.Store(id='tracado-editar-cor', data=cor_atual),
        _campo('Cor', html.Div(
            _swatches_cor_tracado(cor_atual),
            id='tracado-editar-cor-swatches',
            style={'display': 'flex', 'gap': '8px'},
        )),
        _campo('Espessura', dcc.Dropdown(
            id='tracado-editar-espessura', options=_OPCOES_ESPESSURA_TRACADO,
            value=int(t['espessura']), clearable=False, searchable=False,
            style={'fontFamily': FONTS['body'], 'fontSize': '13px'},
        )),
        _campo('Tipo de linha', dcc.Dropdown(
            id='tracado-editar-estilo', options=_OPCOES_ESTILO_TRACADO,
            value=t['estilo'], clearable=False, searchable=False,
            style={'fontFamily': FONTS['body'], 'fontSize': '13px'},
        )),
        html.Div([
            html.Button('Excluir', id='btn-tracado-editar-excluir', n_clicks=0, style={
                'background': 'transparent', 'color': C['red'],
                'border': f'1px solid {C["red"]}', 'borderRadius': '6px',
                'padding': '8px 16px', 'cursor': 'pointer', 'fontFamily': FONTS['display'],
                'fontSize': '13px', 'fontWeight': '600',
            }),
            html.Div(style={'flex': '1'}),
            html.Button('Cancelar', id='btn-tracado-editar-cancelar', n_clicks=0, style={
                'background': 'transparent', 'color': C['text2'],
                'border': f'1px solid {C["border"]}', 'borderRadius': '6px',
                'padding': '8px 16px', 'cursor': 'pointer', 'fontFamily': FONTS['display'],
                'fontSize': '13px', 'marginRight': '8px',
            }),
            html.Button('Salvar', id='btn-tracado-editar-salvar', n_clicks=0, style={
                'background': C['gold'], 'color': C['bg'],
                'border': 'none', 'borderRadius': '6px',
                'padding': '8px 20px', 'cursor': 'pointer', 'fontFamily': FONTS['display'],
                'fontSize': '13px', 'fontWeight': '700',
            }),
        ], style={'display': 'flex', 'alignItems': 'center', 'marginTop': '4px'}),
    ], style={
        'background': C['panel'], 'border': f'1px solid {C["border"]}',
        'borderRadius': '12px', 'padding': '24px', 'width': '320px',
        'boxShadow': '0 8px 40px rgba(0,0,0,0.6)',
    })

    return html.Div([
        html.Div(id='tracado-editar-backdrop', n_clicks=0, style={
            'position': 'fixed', 'top': 0, 'left': 0, 'right': 0, 'bottom': 0,
            'background': 'rgba(0,0,0,0.7)', 'zIndex': 9998,
        }),
        html.Div(conteudo, style={
            'position': 'fixed', 'top': '50%', 'left': '50%',
            'transform': 'translate(-50%, -50%)', 'zIndex': 9999,
        }),
    ])


def _placeholder_aba(nome: str) -> html.Div:
    return html.Div(f'{nome} — em construção.', style={
        'padding': '40px', 'textAlign': 'center', 'color': C['text2'],
        'fontFamily': FONTS['display'], 'fontSize': '13px',
    })


def _texto_status_modo_aprendizagem(ativo: bool) -> html.Div:
    """Texto de status do switch de Modo Aprendizagem (Configurações) —
    função à parte pra ser reaproveitada tanto na renderização inicial do
    layout quanto no callback que troca o estado (2026-07-21, Fase 3.1)."""
    if ativo:
        texto = ('Modo Aprendizagem LIGADO — o sistema cruza seu operacional real '
                 '(Diário) com a análise dos traçados. Ajustes finos são aplicados '
                 'automaticamente; ajustes estruturais viram sugestão pra implementar depois.')
        cor = C['gold']
    else:
        texto = ('Modo Padrão — o sistema só observa e registra a comparação, '
                 'sem aplicar nenhum ajuste automático.')
        cor = C['text2']
    return html.Div(texto, style={
        'fontSize': '12px', 'color': cor, 'fontFamily': FONTS['body'],
        'lineHeight': '1.5', 'marginTop': '10px', 'maxWidth': '400px',
    })


def _texto_log_recalibracao() -> html.Div:
    """Resultado da última rodada de `aprendizagem.rodar_recalibracao` —
    lê só o log já salvo (não dispara uma rodada nova sozinho). Usado na
    carga inicial da tela E depois de clicar 'Rodar recalibração'."""
    log = persistencia.carregar_log_recalibracao()
    if log is None or log.empty:
        return html.Div('Nenhuma recalibração rodou ainda.', style={
            'fontSize': '12px', 'color': C['text2'], 'fontFamily': FONTS['body'],
        })
    ultima = log.iloc[-1]
    if ultima['status'] == 'dados_insuficientes':
        texto = (f"Última rodada ({ultima['timestamp'][:16].replace('T', ' ')}): "
                  f"dados insuficientes ainda ({int(ultima['n_amostras'])} amostras) — "
                  f"precisa de mais trades com nível S/R referenciado + traçado ativo no "
                  f"instante do save.")
        return html.Div(texto, style={'fontSize': '12px', 'color': C['text2'], 'fontFamily': FONTS['body'], 'lineHeight': '1.5'})
    sugestoes = json.loads(ultima['sugestoes_json']) if ultima.get('sugestoes_json') else []
    partes = [html.Div(
        f"Última rodada ({ultima['timestamp'][:16].replace('T', ' ')}): "
        f"{int(ultima['n_amostras'])} amostras analisadas.",
        style={'fontSize': '12px', 'color': C['text'], 'fontFamily': FONTS['body'], 'marginBottom': '6px'},
    )]
    if not sugestoes:
        partes.append(html.Div('Nenhuma inversão de postura detectada.', style={
            'fontSize': '12px', 'color': C['green'], 'fontFamily': FONTS['body'],
        }))
    else:
        for s in sugestoes:
            partes.append(html.Div(f"⚠ {s['detalhe']}", style={
                'fontSize': '12px', 'color': C['gold'], 'fontFamily': FONTS['body'],
                'lineHeight': '1.5', 'marginBottom': '4px',
            }))
    return html.Div(partes)


def _painel_sugestoes_estruturais() -> html.Div:
    """Lista de sugestões estruturais pendentes (Fase 3.4) — painel só de
    leitura, sem botão de ação (implementação real é manual, numa sessão
    futura de Claude Code — ver aprendizagem.registrar_sugestao_
    estrutural)."""
    df = aprendizagem.listar_sugestoes_estruturais(status='pendente')
    if df.empty:
        return html.Div('Nenhuma sugestão estrutural pendente.', style={
            'fontSize': '12px', 'color': C['text2'], 'fontFamily': FONTS['body'],
        })
    cards = []
    for _, row in df.iterrows():
        cards.append(html.Div([
            html.Div(row['titulo'], style={
                'fontSize': '13px', 'fontWeight': '700', 'color': C['text'],
                'fontFamily': FONTS['display'], 'marginBottom': '4px',
            }),
            html.Div(row['detalhe'], style={
                'fontSize': '12px', 'color': C['text2'], 'fontFamily': FONTS['body'], 'lineHeight': '1.5',
            }),
            html.Div(f"registrada em {pd.Timestamp(row['criado_em']).strftime('%Y-%m-%d %H:%M')} · origem: {row['origem']}", style={
                'fontSize': '10px', 'color': C['text2'], 'fontFamily': FONTS['mono'], 'marginTop': '6px',
            }),
        ], style={
            'borderLeft': f'3px solid {C["gold"]}', 'paddingLeft': '10px', 'marginBottom': '14px',
        }))
    return html.Div(cards)


# =============================================================================
# LAYOUT PRINCIPAL
# =============================================================================

app = dash.Dash(
    __name__,
    title='Super Quant — Anticovardia',
    update_title=None,
    suppress_callback_exceptions=True,
)

app.layout = html.Div([

    # Google Fonts
    html.Link(rel='stylesheet',
              href='https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;500&display=swap'),

    # Store para mensagem pendente (para preloader)
    dcc.Store(id='store-mensagem-pendente', data=None),

    # Store para candle atual (atualizado a 1s)
    dcc.Store(id='store-candle-atual', data={}),

    # Store do ativo selecionado
    dcc.Store(id='store-ativo', data='WIN'),
    dcc.Store(id='store-metricas', data={}),
    # Qual velocímetro do hero está com o balão explicativo aberto — setado
    # via clique client-side (assets/hero_gauges.js), nunca por n_clicks do
    # Dash (ver comentário em construir_hero sobre por quê).
    dcc.Store(id='store-gauge-selecionado', data='forca'),

    # Candles pro gráfico Lightweight Charts do Hero — 2 stores, 2
    # cadências (mesmo padrão do SQ'98, ver dashboard_98.py): o histórico
    # completo (300 candles) só troca de ativo ou no tick LENTO de 30s;
    # reserializar 300 candles + o cliente reconstruir o gráfico inteiro
    # via setData() a cada 1s seria caro de propósito nenhum, já que só o
    # último candle muda tick a tick. O candle em formação atualiza no
    # tick RÁPIDO de 1s, sozinho, aplicado no cliente via `.update()` (1
    # candle) — bem mais barato que `setData()` (array inteiro).
    dcc.Store(id='store-candles-grafico', data=[]),
    dcc.Store(id='store-candle-grafico-live', data=None),

    # Níveis de S/R (mesmos do Price Action) pro desenho de linhas
    # horizontais no gráfico — ver `niveis_sr_para_grafico` (metricas.py,
    # compartilhado com dashboard_98.py), atualizado no mesmo tick de 1s
    # de `atualizar_dashboard`. O botão de olho só alterna esse booleano —
    # quem some/redesenha as linhas é assets/lwchart.js.
    dcc.Store(id='store-niveis-sr', data=[]),
    dcc.Store(id='store-sr-visivel', data=True),

    # MME21 (EMA21) — linha no gráfico, mesma arquitetura de 2 stores dos
    # candles (histórico completo no tick de 30s/troca de ativo via
    # `.setData()`; ponto vivo no tick de 1s via `.update()`) — ver
    # mme21_para_grafico/mme21_ponto_live em metricas.py.
    dcc.Store(id='store-mme21-serie', data={'ativo': None, 'serie': []}),
    dcc.Store(id='store-mme21-live', data={'ativo': None, 'ponto': None}),
    dcc.Store(id='store-mme21-visivel', data=True),

    # Traçados manuais (aba "Traçados", ver tracados.py) — 2 direções, pra
    # nunca a mão do usuário competir com um redraw do servidor no meio de
    # um arraste (ver comentário completo em assets/lwchart.js):
    # store-tracado-criar/store-tracado-mover (cliente -> Python, via
    # dash_clientside.set_props) só persistem + recalculam Estudos, NUNCA
    # redesenham o gráfico; store-tracados-servidor (Python -> cliente) é
    # quem dispara o redesenho (carga inicial + depois de criar/apagar/editar).
    dcc.Store(id='store-tracado-criar'),
    dcc.Store(id='store-tracado-mover'),
    dcc.Store(id='store-tracados-servidor', data=[]),

    # Estado de expandir/colapsar a timeline de toques (Price Action E
    # Estudos dos Traçados) — dict {chave_card: bool}, chave estável
    # (id_tracado ou 'acima_1'/'abaixo_2' etc.) pra sobreviver ao
    # re-render completo que cada tick já faz.
    dcc.Store(id='store-timeline-expandida', data={}),

    # Popup de edição (duplo clique numa linha, ver assets/lwchart.js) —
    # guarda só o id_tracado sendo editado; None = popup fechado. Renderiza
    # dentro de 'tracado-overlay-editar' (fora da árvore das abas, mesmo
    # padrão de diario-overlay-detalhe em diario_app_classico.py).
    dcc.Store(id='store-tracado-editar'),

    # Store da aba ativa do cabeçalho (QUANT LAB/BACKTESTING/DIÁRIO/CONFIGURAÇÕES)
    dcc.Store(id='store-aba-ativa', data='lab'),

    # Tema claro/escuro (2026-07-17) — `storage_type='local'` persiste no
    # localStorage do navegador, sobrevive a reload/restart do servidor.
    # `store-tema-dummy` é o Output do clientside_callback que aplica o
    # tema (data-theme + gauges + postMessage pro iframe do Diário) — tem
    # que ser um Output PRÓPRIO, não `scroll-dummy` (todo uso existente de
    # scroll-dummy é allow_duplicate=True + prevent_initial_call=True; aqui
    # precisamos do oposto — aplicar o tema restaurado do localStorage JÁ
    # na carga inicial, não só depois do 1º clique da sessão).
    dcc.Store(id='store-tema', data='light', storage_type='local'),
    dcc.Store(id='store-tema-dummy'),

    # Interval rápido — 1s: atualiza preço, candle atual, fluxo
    dcc.Interval(id='interval-live', interval=1_000, n_intervals=0),
    # Interval lento — 30s: reconstrói features completas e blocos pesados
    dcc.Interval(id='interval-slow', interval=30_000, n_intervals=0),
    # Sincronização de posições abertas — cadência própria (ver comentário
    # junto de `sincronizar_posicoes`).
    dcc.Interval(id='interval-posicao-sync', interval=3_000, n_intervals=0),
    # Reconciliação MT5×diário — 5min, roda em background e silencioso
    # (feedback diário.txt, 2026-07-11: deixou de ser aba manual). Ver
    # `_reconciliar_background`/`resumo_reconciliacao`.
    dcc.Interval(id='interval-reconciliacao', interval=300_000, n_intervals=0),

    # =========================================================================
    # CABEÇALHO — navegação em 4 abas
    # =========================================================================
    html.Div([
        html.Div([
            html.Span('SUPER QUANT', style={
                'fontSize': '15px', 'fontWeight': '700',
                'fontFamily': FONTS['display'], 'color': C['gold'],
                'letterSpacing': '0.1em',
            }),
            html.Span(' · ANTICOVARDIA', style={
                'fontSize': '11px', 'color': C['text2'],
                'fontFamily': FONTS['display'],
            }),
        ]),
        html.Div([
            *[html.Button(label, id=f'tab-{key}', n_clicks=0,
                          style=_estilo_aba(key, 'lab'))
              for label, key in [
                ('QUANT LAB', 'lab'),
                ('BACKTESTING', 'back'),
                ('DIÁRIO', 'diary'),
                ('CONFIGURAÇÕES', 'config'),
            ]],
        ], style={'display': 'flex', 'gap': '4px'}),

        # Status de conexão fixo no cabeçalho
        html.Div([
            # Seletor WIN / WDO — movido do hero pro cabeçalho em 2026-07-15
            # (fica visível em qualquer aba, não só no Quant Lab). Estático,
            # nunca recriado (só o `style` de cada botão muda, via callback
            # dedicado `atualizar_estilo_ativo_selecionado`) — ver docstring
            # de `construir_hero` sobre por que isso é obrigatório.
            html.Div([
                html.Button('WIN', id='btn-win', n_clicks=0,
                            className='btn-ativo',
                            style=_estilo_botao_ativo('WIN', 'WIN')),
                html.Button('WDO', id='btn-wdo', n_clicks=0,
                            className='btn-ativo',
                            style=_estilo_botao_ativo('WDO', 'WIN')),
            ], style={'display': 'flex', 'gap': '6px', 'justifyContent': 'flex-end', 'marginBottom': '6px'}),
            html.Span(id='status-conexao', children='Inicializando...',
                      style={'fontSize': '11px', 'color': C['text2'],
                             'fontFamily': FONTS['display']}),
            html.Span(' · ', style={'color': C['border']}),
            html.Span(id='status-contrato', children='—',
                      style={'fontSize': '11px', 'fontFamily': FONTS['mono'],
                             'color': C['gold']}),
            html.Br(),
            html.Span(id='status-atualizacao', children='',
                      style={'fontSize': '10px', 'color': C['text2'],
                             'fontFamily': FONTS['display']}),
            html.Br(),
            # Janela de notícia (Copom/FOMC/NFP) — calendario_economico.py.
            # Registrado em 2026-07-02: não opera notícia, mas esses
            # horários distorcem a leitura de S/R (ver CHANGELOG.md).
            html.Span(id='status-calendario', children='',
                      style={'fontSize': '10px', 'fontFamily': FONTS['display']}),
        ], style={'textAlign': 'right'}),

    ], style={
        'display': 'flex', 'justifyContent': 'space-between',
        'alignItems': 'center', 'padding': '12px 28px',
        'borderBottom': f'1px solid {C["border"]}',
        'background': C['panel'], 'position': 'sticky',
        'top': '0', 'zIndex': '100',
    }),

    # =========================================================================
    # CONTEÚDO PRINCIPAL
    # =========================================================================
    html.Div(id='conteudo-principal', children=[

      html.Div(id='painel-quantlab', children=[

        # HERO
        html.Div([
            html.Div([
                # Velocímetros — dinâmico, reconstruído a cada 1s
                html.Div(id='hero-conteudo', children=[
                    html.Div('Carregando base de dados...', style={
                        'color': C['text2'], 'fontFamily': FONTS['body'],
                        'fontSize': '14px', 'padding': '20px',
                    })
                ]),
            ], style={
                'display': 'flex', 'alignItems': 'center',
                'gap': '16px', 'flexWrap': 'wrap',
            }),
            # Balão explicativo — estático (fora de hero-conteudo, nunca
            # recriado pelo tick de 1s), só muda via clique num gauge
            # (assets/hero_gauges.js + store-gauge-selecionado).
            html.Div(_balao_explicacao_gauge('forca'), id='balao-gauge-explicacao'),
        ], style={
            # #0E0E18 era hardcoded (não fazia parte do dicionário C) — nunca
            # acompanhava o tema, ficando escuro até no modo claro
            # (2026-07-17, achado real no screenshot de verificação).
            # var(--bg) é semanticamente equivalente (quase-preto no escuro,
            # cinza claro no claro) e já theme-aware de graça.
            'background': f'linear-gradient(135deg, {C["panel"]} 0%, {C["bg"]} 100%)',
            'border': f'1px solid {C["border"]}',
            'borderRadius': '14px', 'padding': '24px',
            'marginBottom': '24px',
        }),

        # MARKET VIEW
        html.Div(id='market-view-conteudo'),

        # RASTREADOR DE TENDÊNCIA — 4 leituras da MME21 (Maior/Média/Menor/
        # Micro) + Tendência Atual (Modo Aprendizagem.txt, 2026-07-20; ver
        # construir_rastreador_tendencia/analisar_rastreador_tendencia em
        # metricas.py). Jesué não opera a MME21 como estratégia principal —
        # só complementa o price action puro em dias de tendência — por
        # isso fica em seção própria, logo abaixo de Market View.
        html.Div([
            secao('RASTREADOR DE TENDÊNCIA'),
            html.Div(id='rastreador-conteudo'),
        ], style={'marginTop': '24px'}),

        # PRICE ACTION + TRAÇADOS — uma seção só (Modo Aprendizagem.txt,
        # 2026-07-20): "TRAÇADOS E PRICE ACTION viram uma coisa só no mesmo
        # lugar, no QUANT LAB". O gráfico editável de Traçados (antes numa
        # aba própria, `painel-tracados`) É o gráfico da seção agora — o
        # antigo `lw-chart-price-action` (candle sem ferramenta de desenho)
        # foi removido por ser redundante (mesmo `window.SQ_CHART`, mesmos
        # stores de candles/MME21, só sem a barra de desenho). O motor não
        # sugere mais S/R sozinho — a análise completa (blocos de força)
        # agora só aparece pra linhas desenhadas manualmente, na seção
        # ESTUDOS abaixo. `price-action-conteudo` preserva só os 2 sinais
        # táticos independentes do detector de zonas (pivô imediato/em
        # formação, ver construir_price_action).
        html.Div([
            secao('PRICE ACTION'),
            html.Div([
                html.Button('🖱 Cursor', id='btn-tracado-cursor',
                            className='tracado-tool-btn tracado-tool-ativa',
                            **{'data-tracado-tool': 'cursor'}),
                html.Button('— Nível S/R', id='btn-tracado-horizontal',
                            className='tracado-tool-btn',
                            **{'data-tracado-tool': 'horizontal'}),
                html.Button('↗ LTA/LTB', disabled=True, title='Em breve', className='tracado-tool-btn'),
                html.Button('▱ Canal', disabled=True, title='Em breve', className='tracado-tool-btn'),
                html.Button('△ Figura', disabled=True, title='Em breve', className='tracado-tool-btn'),
                # Criar linha digitando o preço direto — sem precisar
                # clicar no gráfico (Modo Aprendizagem.txt, 2026-07-20:
                # "adicione um campo para... inserir a linha digitando o
                # preço diretamente"). Reaproveita tracados.criar_tracado
                # (mesmo CRUD do clique no gráfico), só um novo ponto de
                # entrada — ver criar_tracado_por_preco_callback.
                dcc.Input(
                    id='tracado-criar-preco', type='number', placeholder='Preço...',
                    step='any', style={
                        'width': '110px', 'background': C['bg'], 'border': f'1px solid {C["border"]}',
                        'borderRadius': '6px', 'padding': '6px 10px', 'color': C['text'],
                        'fontFamily': FONTS['mono'], 'fontSize': '13px', 'marginLeft': '8px',
                    },
                ),
                html.Button('+ Criar linha', id='btn-tracado-criar-preco', style={
                    'background': C['gold'], 'color': C['bg'], 'border': 'none',
                    'borderRadius': '6px', 'padding': '6px 12px', 'cursor': 'pointer',
                    'fontSize': '12px', 'fontWeight': '700',
                }),
                html.Span(id='tracado-criar-preco-status', style={
                    'fontSize': '11px', 'color': C['text2'], 'marginLeft': '8px', 'alignSelf': 'center',
                }),
            ], style={'display': 'flex', 'gap': '8px', 'marginBottom': '12px', 'flexWrap': 'wrap', 'alignItems': 'center'}),
            html.Div([
                html.Div(id='lw-chart-tracados', style={'width': '100%', 'height': '100%'}),
                _botao_reset_zoom('btn-reset-zoom-tracados'),
                _botao_toggle_sr('btn-sr-toggle-tracados'),
                _botao_toggle_mme21('btn-mme21-toggle-tracados'),
            ], style={
                'height': '420px', 'marginBottom': '20px', 'position': 'relative',
                'background': C['bg'], 'border': f'1px solid {C["border"]}',
                'borderRadius': '10px',
            }),
            html.Div(id='price-action-conteudo'),
            html.Div([
                secao('ESTUDOS'),
                # Recomputar a leitura quantitativa (toques/força/probabilidade)
                # depois de arrastar um traçado tem um vai-e-vem real com o
                # servidor (trava de arquivo + reler o histórico inteiro) —
                # sem isso, a tela fica parada nesse meio tempo e parece
                # travada. dcc.Loading detecta sozinho quando o callback que
                # tem 'estudos-conteudo' como Output está rodando (Input
                # store-tracado-mover, ver atualizar_estudos) e mostra o
                # spinner por cima até o novo conteúdo chegar.
                dcc.Loading(
                    id='loading-estudos', type='dot', color=C['gold'],
                    children=html.Div(id='estudos-conteudo'),
                ),
            ], style={'marginTop': '20px'}),
        ], style={'marginTop': '24px'}),

      ]),

      # PAINEL DIÁRIO — oculto por padrão, só visível quando store-aba-ativa
      # == 'diary'. Conteúdo é um <iframe> pra um processo Dash separado
      # (diario_app_classico.py, porta 8053, lançado automaticamente no
      # __main__ abaixo) — nenhum componente do Diário conta mais pro custo
      # de reconciliação do tick de 1s deste dashboard, não importa quão
      # pesado o Diário fique (ver
      # `C:\Users\jesue\.claude\plans\t-tulo-super-quant-eager-knuth.md`).
      html.Div(id='painel-diario', style={'display': 'none'}, children=[
          secao('DIÁRIO DE TRADE'),
          html.Iframe(
              src='http://127.0.0.1:8053/',
              style={'width': '100%', 'height': '80vh', 'border': 'none'},
          ),
      ]),

      # Placeholders — Backtesting/Configurações não têm conteúdo real ainda,
      # só evitam a aba parecer quebrada ao clicar.
      html.Div(id='painel-back', style={'display': 'none'}, children=[
          _placeholder_aba('Backtesting'),
      ]),
      html.Div(id='painel-config', style={'display': 'none'}, children=[
          secao('CONFIGURAÇÕES'),
          html.Div([
              html.Div('Tema', style={
                  'fontSize': '10px', 'color': C['text2'], 'textTransform': 'uppercase',
                  'letterSpacing': '0.08em', 'fontFamily': FONTS['display'], 'marginBottom': '10px',
              }),
              html.Div([
                  html.Span('Escuro', style={'fontSize': '12px', 'color': C['text2'], 'fontFamily': FONTS['display']}),
                  html.Div(html.Div(className='tema-toggle-knob'), id='switch-tema', n_clicks=0,
                           className='tema-toggle', title='Alternar tema claro/escuro'),
                  html.Span('Claro', style={'fontSize': '12px', 'color': C['text2'], 'fontFamily': FONTS['display']}),
              ], style={'display': 'flex', 'alignItems': 'center', 'gap': '10px'}),
          ], style={
              'background': C['panel'], 'border': f'1px solid {C["border"]}',
              'boxShadow': 'var(--shadow-card)',
              'borderRadius': '10px', 'padding': '16px 20px', 'maxWidth': '320px',
              'marginBottom': '16px',
          }),
          # Modo Aprendizagem (Fase 3.1, 2026-07-21) — switch SERVIDOR de
          # verdade (config_operacional.py), não cosmético. O callback
          # Python só escreve `className` do próprio elemento (nunca
          # `children`) pra não resetar n_clicks (mesmo cuidado de
          # .tracado-tool-btn/.diario-estrela, ver assets/style.css).
          html.Div([
              html.Div('Modo Aprendizagem', style={
                  'fontSize': '10px', 'color': C['text2'], 'textTransform': 'uppercase',
                  'letterSpacing': '0.08em', 'fontFamily': FONTS['display'], 'marginBottom': '10px',
              }),
              html.Div([
                  html.Span('Padrão', style={'fontSize': '12px', 'color': C['text2'], 'fontFamily': FONTS['display']}),
                  html.Div(
                      html.Div(className='modo-aprendizagem-toggle-knob'),
                      id='switch-modo-aprendizagem', n_clicks=0,
                      className='modo-aprendizagem-toggle' + (
                          ' modo-aprendizagem-toggle--ativo' if config_operacional.modo_aprendizagem_ativo() else ''),
                      title='Ligar/desligar Modo Aprendizagem',
                  ),
                  html.Span('Aprendizagem', style={'fontSize': '12px', 'color': C['text2'], 'fontFamily': FONTS['display']}),
              ], style={'display': 'flex', 'alignItems': 'center', 'gap': '10px'}),
              html.Div(
                  id='modo-aprendizagem-status',
                  children=_texto_status_modo_aprendizagem(config_operacional.modo_aprendizagem_ativo()),
              ),
          ], style={
              'background': C['panel'], 'border': f'1px solid {C["border"]}',
              'boxShadow': 'var(--shadow-card)',
              'borderRadius': '10px', 'padding': '16px 20px', 'maxWidth': '420px',
              'marginBottom': '16px',
          }),
          # Recalibração (Fase 3.3, 2026-07-21) — botão manual (sem agenda
          # automática ainda, ver aprendizagem.py) que roda avaliar_
          # calibracao_postura + grava log auditável. NUNCA escreve
          # constante nenhuma — só calcula e mostra o que sugeriria.
          html.Div([
              html.Div('Recalibração (ajuste fino)', style={
                  'fontSize': '10px', 'color': C['text2'], 'textTransform': 'uppercase',
                  'letterSpacing': '0.08em', 'fontFamily': FONTS['display'], 'marginBottom': '10px',
              }),
              html.Button('Rodar recalibração', id='btn-rodar-recalibracao', n_clicks=0, style={
                  'background': C['panel'], 'color': C['text'], 'border': f'1px solid {C["border"]}',
                  'borderRadius': '6px', 'padding': '6px 14px', 'cursor': 'pointer', 'fontSize': '12px',
                  'marginBottom': '10px',
              }),
              html.Div(id='recalibracao-resultado', children=_texto_log_recalibracao()),
          ], style={
              'background': C['panel'], 'border': f'1px solid {C["border"]}',
              'boxShadow': 'var(--shadow-card)',
              'borderRadius': '10px', 'padding': '16px 20px', 'maxWidth': '420px',
              'marginBottom': '16px',
          }),
          # Sugestões estruturais (Fase 3.4, 2026-07-21) — painel SÓ DE
          # LEITURA (sem botão/edição nesta entrega): achados que não são
          # "mover um número dentro de limite" (ver aprendizagem.
          # registrar_sugestao_estrutural) — implementação real acontece
          # comigo (Claude Code) numa sessão futura, nunca automática.
          html.Div([
              html.Div('Sugestões Estruturais Pendentes', style={
                  'fontSize': '10px', 'color': C['text2'], 'textTransform': 'uppercase',
                  'letterSpacing': '0.08em', 'fontFamily': FONTS['display'], 'marginBottom': '10px',
              }),
              html.Div(id='sugestoes-estruturais-conteudo', children=_painel_sugestoes_estruturais()),
          ], style={
              'background': C['panel'], 'border': f'1px solid {C["border"]}',
              'boxShadow': 'var(--shadow-card)',
              'borderRadius': '10px', 'padding': '16px 20px', 'maxWidth': '520px',
          }),
      ]),

    ], style={
        'padding': '24px 28px',
        'maxWidth': '1600px',
        'margin': '0 auto',
    }),

    # =========================================================================
    # CHAT FLUTUANTE — Mentor IA (ícone no canto inferior direito)
    # =========================================================================

    # Store para controlar se o chat está aberto
    dcc.Store(id='store-chat-aberto', data=False),

    # Botão flutuante
    html.Div([
        html.Button('✦', id='btn-chat-toggle', n_clicks=0, style={
            'width': '56px', 'height': '56px',
            'borderRadius': '50%',
            'background': f'linear-gradient(135deg, {C["gold"]}, #A07840)',
            'border': 'none', 'cursor': 'pointer',
            'fontSize': '22px', 'color': C['bg'],
            'fontWeight': '700', 'boxShadow': '0 4px 20px rgba(200,169,110,0.4)',
            'transition': 'transform 0.2s',
        }),
    ], style={
        'position': 'fixed', 'bottom': '28px', 'right': '28px',
        'zIndex': '1000',
    }),


    # Modal do chat — layout com alturas explícitas para cada zona
    html.Div([

        # Header — altura fixa 52px
        html.Div([
            html.Div([
                html.Span('✦ ', style={'color': C['gold']}),
                html.Span('MENTOR IA', style={
                    'fontSize': '13px', 'fontWeight': '700',
                    'fontFamily': FONTS['display'], 'color': C['text'],
                    'letterSpacing': '0.08em',
                }),
            ]),
            html.Button('×', id='btn-chat-fechar', n_clicks=0, style={
                'background': 'transparent', 'border': 'none',
                'color': C['text2'], 'fontSize': '20px',
                'cursor': 'pointer', 'padding': '0 8px', 'lineHeight': '1',
            }),
        ], style={
            'display': 'flex', 'justifyContent': 'space-between',
            'alignItems': 'center',
            'padding': '0 16px',
            'height': '52px',
            'borderBottom': f'1px solid {C["border"]}',
            'boxSizing': 'border-box',
            'flexShrink': '0',
        }),

        # Histórico — flex: 1 com minHeight: 0 é a forma correta de fazer overflow em flex
        html.Div(id='chat-historico', children=[
            html.Div([
                html.Span('Olá, Jesué. Estou monitorando o mercado.',
                          style={'color': C['text2'], 'fontSize': '13px',
                                 'fontFamily': FONTS['body']}),
            ], style={'padding': '8px 4px'}),
        ], style={
            'flex': '1',
            'minHeight': '0',      # CRÍTICO: sem isso o flex não respeita o overflow
            'overflowY': 'auto',
            'padding': '8px 12px',
            'display': 'flex',
            'flexDirection': 'column',
            'gap': '8px',
        }),

        # Dummy para scroll + loading
        html.Div(id='scroll-dummy', style={'display': 'none'}),
        dcc.Loading(
            id='chat-loading', type='dot', color=C['gold'],
            style={'position': 'absolute', 'bottom': '80px',
                   'left': '50%', 'transform': 'translateX(-50%)',
                   'zIndex': '10'},
            children=html.Div(id='chat-loading-target',
                               style={'height': '0', 'width': '0'}),
        ),

        # Seletor de modelo (2026-07-08) — Gemini + OpenRouter (automático
        # ou modelo específico, grátis/pago). "Automático" tenta vários
        # modelos grátis da OpenRouter em ordem até um responder, evitando
        # ficar preso no "limite excedido" de um único modelo/provedor.
        # Discreto de propósito — pequeno, cor apagada — não é o foco do
        # chat. Ver assets/style.css pro tema escuro (classes reais do
        # Dash 4.3, não mais o react-select antigo).
        html.Div([
            dcc.Dropdown(
                id='chat-modelo',
                options=ai_analyst.opcoes_modelo(),
                value='openrouter-auto',
                clearable=False, searchable=False,
                style={'fontFamily': FONTS['body'], 'fontSize': '10px', 'width': '200px'},
            ),
        ], style={
            'padding': '2px 12px', 'height': '20px',
            'display': 'flex', 'justifyContent': 'flex-start',
            'borderTop': f'1px solid {C["border"]}',
            'boxSizing': 'border-box', 'flexShrink': '0',
        }),

        # Input + botão — 2026-07-08: era de fato curto demais pra ler o
        # que estava sendo digitado, mas a correção certa é MAIS LINHAS
        # visíveis (textarea), não fonte maior — fonte volta ao tamanho
        # original. Botão usa alignSelf:stretch pra acompanhar a altura do
        # textarea automaticamente (nunca mais fica maior/menor que ele).
        html.Div([
            dcc.Textarea(
                id='chat-input',
                placeholder='Preço ou pergunta...',
                rows=3,
                style={
                    'flex': '1',
                    'background': C['bg'],
                    'border': f'1px solid {C["border"]}',
                    'borderRight': 'none',
                    'borderRadius': '10px 0 0 10px',
                    'padding': '10px 14px',
                    'color': C['text'],
                    'fontFamily': FONTS['body'],
                    'fontSize': '13px',
                    'lineHeight': '1.4',
                    'outline': 'none',
                    'resize': 'none',
                }
            ),
            html.Button('▶', id='btn-chat-enviar', n_clicks=0, style={
                'background': C['gold'], 'color': C['bg'],
                'border': 'none',
                'borderRadius': '0 10px 10px 0',
                'padding': '0 18px',
                'cursor': 'pointer',
                'fontWeight': '700', 'fontSize': '14px',
                'flexShrink': '0',
                'alignSelf': 'stretch',
                'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center',
            }),
        ], style={
            'display': 'flex',
            'alignItems': 'stretch',
            'padding': '10px 12px',
            'borderTop': f'1px solid {C["border"]}',
            'boxSizing': 'border-box',
            'flexShrink': '0',
        }),

        # Status — altura fixa 28px
        html.Div(id='chat-status', children='',
                 style={
                     'fontSize': '10px', 'color': C['text2'],
                     'fontFamily': FONTS['mono'],
                     'padding': '4px 16px',
                     'height': '28px',
                     'textAlign': 'right',
                     'boxSizing': 'border-box',
                     'flexShrink': '0',
                 }),

    ], id='chat-modal-container', style={
        'display': 'none',
        'position': 'fixed', 'bottom': '96px', 'right': '28px',
        'width': '420px', 'height': '560px',  # +24px (seletor discreto) +~12px (textarea 3 linhas vs. input 1 linha) sobre o histórico
        'background': C['panel'],
        'border': f'1px solid {C["border"]}',
        'borderRadius': '16px', 'zIndex': '999',
        'boxShadow': '0 8px 40px rgba(0,0,0,0.6)',
        'flexDirection': 'column',
        'overflow': 'hidden',
    }),

    # Rodapé
    html.Div([
        html.Span('SUPER QUANT · ANTICOVARDIA', style={
            'fontSize': '10px', 'color': C['text2'],
            'fontFamily': FONTS['display'], 'letterSpacing': '0.08em',
        }),
    ], style={
        'textAlign': 'center', 'padding': '16px',
        'borderTop': f'1px solid {C["border"]}',
        'marginTop': '32px',
    }),

    # Popup de edição de traçado (duplo clique numa linha) — fora da árvore
    # das abas, mesmo padrão de diario-overlay-detalhe em
    # diario_app_classico.py: um placeholder vazio, populado por callback
    # quando store-tracado-editar tem um id_tracado.
    html.Div(id='tracado-overlay-editar'),

], style={
    'background': C['bg'],
    'minHeight': '100vh',
    'color': C['text'],
    'fontFamily': FONTS['body'],
})


# =============================================================================
# CALLBACKS
# =============================================================================

# Scroll automático do chat usando elemento dummy sem conflito com outros callbacks
app.clientside_callback(
    """
    function(children) {
        setTimeout(function() {
            var el = document.getElementById('chat-historico');
            if (el) { el.scrollTop = el.scrollHeight; }
        }, 200);
        return '';
    }
    """,
    Output('scroll-dummy', 'children'),
    Input('chat-historico', 'children'),
    prevent_initial_call=True,
)

# Pulso visual imediato ao trocar de ativo (WIN/WDO/WIN×WDO) — feedback de
# "o clique registrou" enquanto o conteúdo recarrega, sem esperar o
# round-trip do callback principal. Ver assets/style.css. Dispara só em
# store-ativo (nunca no tick de 1s), então não pisca a cada segundo.
app.clientside_callback(
    """
    function(ativo) {
        var el = document.getElementById('conteudo-principal');
        if (el) {
            el.classList.remove('trocando-ativo');
            void el.offsetWidth;  // força reflow p/ reiniciar a animação em cliques seguidos
            el.classList.add('trocando-ativo');
        }
        return '';
    }
    """,
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('store-ativo', 'data'),
    prevent_initial_call=True,
)

# Alimenta os 2 gráficos Lightweight Charts (Price Action + Traçados) — só
# repassa os dados pro objeto que assets/lwchart.js expõe em
# window.SQ_CHART; a criação/estilo dos charts em si é toda lá, não aqui.
# Bug 2026-07-13 (1): só `c.series` (Price Action) recebia candles aqui — o
# gráfico da aba Traçados nunca ganhava setData(), ficava sempre vazio
# (só as linhas de S/R/MME21 apareciam, sem nenhum candle de fundo) —
# mesmo padrão de `assets_98/lwchart.js` (que já alimenta as 2 séries).
# Bug 2026-07-13 (2): setData() a cada 30s resetava o zoom/pan manual do
# usuário sozinho (comportamento da própria Lightweight Charts). `ativo`
# vai como State pra `atualizarCandlesCompletos` (assets/lwchart.js) saber
# se é troca de ativo (reseta o zoom de propósito) ou só o refresh
# periódico (preserva o zoom atual).
app.clientside_callback(
    """
    function(candles, ativo) {
        var c = window.SQ_CHART;
        if (c && c.atualizarCandlesCompletos) c.atualizarCandlesCompletos(candles, ativo);
        return '';
    }
    """,
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('store-candles-grafico', 'data'),
    State('store-ativo', 'data'),
    prevent_initial_call=True,
)

# Limpa as linhas de S/R IMEDIATAMENTE na troca de ativo, sem esperar o
# próximo tick de 1s recalcular store-niveis-sr — evita a janela onde uma
# linha da escala antiga (WIN ~170-190 mil vs WDO ~5 mil) distorce o
# autoscale do eixo enquanto os candles novos ainda não chegaram/renderizam
# (ver comentário de limparTudo em assets/lwchart.js).
app.clientside_callback(
    """
    function(ativo) {
        var c = window.SQ_CHART;
        if (c && c.limparTudo) c.limparTudo();
        return '';
    }
    """,
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('store-ativo', 'data'),
    prevent_initial_call=True,
)

# Candle "vivo" a cada 1s — `.update()` (1 candle só) em vez de `setData()`
# (array inteiro): é o método que a própria Lightweight Charts documenta
# pra atualização em tempo real, muito mais barato que reconstruir o
# gráfico inteiro a cada tick. Atualiza as 2 séries (mesmo motivo do
# setData acima).
app.clientside_callback(
    """
    function(candle) {
        var c = window.SQ_CHART;
        if (c && candle) {
            if (c.series) c.series.update(candle);
            if (c.tracadosSeries) c.tracadosSeries.update(candle);
        }
        return '';
    }
    """,
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('store-candle-grafico-live', 'data'),
    prevent_initial_call=True,
)

# Ícone de olho — só inverte o booleano compartilhado; quem decide o que
# fazer com ele é o callback de desenho logo abaixo.
app.clientside_callback(
    """
    function(n, visivel) {
        return !visivel;
    }
    """,
    Output('store-sr-visivel', 'data'),
    Input('btn-sr-toggle-tracados', 'n_clicks'),
    State('store-sr-visivel', 'data'),
    prevent_initial_call=True,
)

# Desenha/atualiza as linhas de S/R no gráfico — dispara tanto quando os
# níveis mudam (tick de 1s) quanto quando o olho é clicado.
# `desenharNiveisSR` (assets/lwchart.js) sempre limpa as linhas antigas
# antes de redesenhar — nunca acumula. `store-ativo` vai como State (não
# Input, não deve disparar redesenho sozinho) só pra `desenharNiveisSR`
# comparar contra a tag `ativo` do payload e descartar respostas atrasadas
# de um ativo já trocado (ver comentário no Output de `store-niveis-sr`
# em `atualizar_dashboard`).
app.clientside_callback(
    """
    function(niveis, visivel, ativoAtual) {
        var c = window.SQ_CHART;
        if (c && c.desenharNiveisSR) c.desenharNiveisSR(niveis, visivel, ativoAtual);
        return '';
    }
    """,
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('store-niveis-sr', 'data'),
    Input('store-sr-visivel', 'data'),
    State('store-ativo', 'data'),
    prevent_initial_call=True,
)

# MME21 — histórico completo (setData, cadência de 30s/troca de ativo).
app.clientside_callback(
    """
    function(payload, ativoAtual) {
        var c = window.SQ_CHART;
        if (c && c.atualizarMME21Serie) c.atualizarMME21Serie(payload, ativoAtual);
        return '';
    }
    """,
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('store-mme21-serie', 'data'),
    State('store-ativo', 'data'),
    prevent_initial_call=True,
)

# MME21 — ponto vivo (update, tick de 1s).
app.clientside_callback(
    """
    function(payload, ativoAtual) {
        var c = window.SQ_CHART;
        if (c && c.atualizarMME21Live) c.atualizarMME21Live(payload, ativoAtual);
        return '';
    }
    """,
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('store-mme21-live', 'data'),
    State('store-ativo', 'data'),
    prevent_initial_call=True,
)

# Ícone de olho da MME21.
app.clientside_callback(
    """
    function(n, visivel) {
        return !visivel;
    }
    """,
    Output('store-mme21-visivel', 'data'),
    Input('btn-mme21-toggle-tracados', 'n_clicks'),
    State('store-mme21-visivel', 'data'),
    prevent_initial_call=True,
)
app.clientside_callback(
    """
    function(visivel) {
        var c = window.SQ_CHART;
        if (c && c.toggleMME21) c.toggleMME21(visivel);
        return '';
    }
    """,
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('store-mme21-visivel', 'data'),
    prevent_initial_call=True,
)

# Tema claro/escuro (2026-07-17) — clique no switch inverte o tema, mesmo
# idioma do toggle MME21 acima.
app.clientside_callback(
    """
    function(n, temaAtual) {
        return (temaAtual === 'light') ? 'dark' : 'light';
    }
    """,
    Output('store-tema', 'data'),
    Input('switch-tema', 'n_clicks'),
    State('store-tema', 'data'),
    prevent_initial_call=True,
)

# Aplica o tema: data-theme no <html>, retema os gauges/gráfico via
# window.SQ_CHART, repassa pro iframe do Diário via postMessage. Output
# PRÓPRIO (store-tema-dummy, não scroll-dummy) e SEM prevent_initial_call —
# precisa rodar já na carga inicial pra aplicar o tema restaurado do
# localStorage antes do usuário ver a página (ver comentário no
# dcc.Store(id='store-tema-dummy') no layout).
app.clientside_callback(
    """
    function(tema) {
        window.SQ_TEMA = window.SQ_TEMA || {};
        window.SQ_TEMA.atual = tema || 'light';
        var switchEl = document.getElementById('switch-tema');
        if (switchEl) switchEl.classList.toggle('tema-toggle--claro', window.SQ_TEMA.atual === 'light');
        if (window.SQ_TEMA.aplicarLocal) window.SQ_TEMA.aplicarLocal(window.SQ_TEMA.atual);
        var iframe = document.querySelector('#painel-diario iframe');
        if (iframe) iframe.contentWindow.postMessage({tipo: 'sq-tema', tema: window.SQ_TEMA.atual}, '*');
        return '';
    }
    """,
    Output('store-tema-dummy', 'data'),
    Input('store-tema', 'data'),
)


# Modo Aprendizagem (Fase 3.1, 2026-07-21) — DIFERENTE do tema: precisa de
# round-trip de servidor de verdade (escreve em config_operacional.py, lido
# por processos separados). Output é só `className` do próprio switch +
# `children` de um Div SEPARADO de status — nunca `children` do switch em
# si, senão n_clicks reseta a cada clique (mesma lição de
# .tracado-tool-btn/.diario-estrela).
@app.callback(
    Output('switch-modo-aprendizagem', 'className'),
    Output('modo-aprendizagem-status', 'children'),
    Input('switch-modo-aprendizagem', 'n_clicks'),
    prevent_initial_call=True,
)
def alternar_modo_aprendizagem(n_clicks):
    atual = config_operacional.modo_aprendizagem_ativo()
    config_operacional.definir_modo_aprendizagem(not atual)
    ativo_final = config_operacional.modo_aprendizagem_ativo()  # relê — fonte da verdade, mesmo se a trava tiver recusado a escrita
    classe = 'modo-aprendizagem-toggle' + (' modo-aprendizagem-toggle--ativo' if ativo_final else '')
    return classe, _texto_status_modo_aprendizagem(ativo_final)


@app.callback(
    Output('recalibracao-resultado', 'children'),
    Input('btn-rodar-recalibracao', 'n_clicks'),
    prevent_initial_call=True,
)
def rodar_recalibracao_callback(n_clicks):
    """Botão 'Rodar recalibração' (Fase 3.3) — chama `aprendizagem.
    rodar_recalibracao()` de verdade (síncrono, é rápido e de uso raro,
    mesmo padrão do botão 'Reconciliar agora'). Nunca escreve constante
    nenhuma, só recalcula o log e mostra o resultado."""
    try:
        aprendizagem.rodar_recalibracao()
    except Exception as e:
        return html.Div(f'⚠ Erro ao recalibrar: {str(e)[:150]}', style={'fontSize': '12px', 'color': C['red']})
    return _texto_log_recalibracao()

@app.callback(
    Output('store-ativo', 'data'),
    Input('btn-win', 'n_clicks'),
    Input('btn-wdo', 'n_clicks'),
    State('store-ativo', 'data'),
    prevent_initial_call=True,
)
def selecionar_ativo(n_win, n_wdo, ativo_atual):
    ctx = callback_context
    if not ctx.triggered:
        return ativo_atual
    botao = ctx.triggered[0]['prop_id'].split('.')[0]
    mapa = {'btn-win': 'WIN', 'btn-wdo': 'WDO'}
    return mapa.get(botao, ativo_atual)


@app.callback(
    Output('btn-win', 'style'),
    Output('btn-wdo', 'style'),
    Input('store-ativo', 'data'),
)
def atualizar_estilo_ativo_selecionado(ativo):
    """Só troca a cor do botão ativo — dispara apenas quando `store-ativo`
    muda (clique real), nunca a cada tick de 1s. Mantém os `html.Button`
    estáticos intactos (nunca recriados), preservando `n_clicks` no browser."""
    return (
        _estilo_botao_ativo('WIN', ativo),
        _estilo_botao_ativo('WDO', ativo),
    )


@app.callback(
    Output('balao-gauge-explicacao', 'children'),
    Input('store-gauge-selecionado', 'data'),
    State('store-ativo', 'data'),
)
def renderizar_balao_gauge(gauge_selecionado, ativo):
    """Dispara só quando o clique client-side (assets/hero_gauges.js) muda
    `store-gauge-selecionado` — nunca no tick de 1s (ver comentário em
    construir_hero/_balao_explicacao_gauge sobre por quê o balão mora fora
    de hero-conteudo). 'big_players' (Fase 4, 2026-07-21) é o único
    dinâmico — recalcula fresco no clique (lê `estado['bases']`, já em
    memória, sem I/O novo) em vez do texto estático dos outros gauges."""
    gauge_selecionado = gauge_selecionado or 'forca'
    if gauge_selecionado == 'big_players':
        df_ativo = estado['bases'].get(ativo or 'WIN')
        resumo = analisar_big_players_resumo(df_ativo)
        return _balao_big_players(resumo)
    return _balao_explicacao_gauge(gauge_selecionado)


@app.callback(
    Output('store-candle-atual', 'data'),
    Input('interval-live', 'n_intervals'),
    State('store-ativo', 'data'),
)
def atualizar_candle_atual(n, ativo):
    """Callback rápido (1s): busca só o candle atual do MT5 para atualização em tempo real."""
    with lock:
        bases = estado['bases']

    df = bases.get(ativo)
    if df is None or df.empty:
        return {}

    ultimo = df.iloc[-1]
    atr = float(ultimo.get('atr', 0) or 0)

    # Tenta buscar tick atual do MT5 para preço em tempo real
    preco_atual = float(ultimo['close'])
    try:
        if MT5_OK:
            ticker = estado.get('contrato_win') if ativo == 'WIN' else estado.get('contrato_wdo')
            if ticker and ticker != '—':
                tick = mt5.symbol_info_tick(ticker)
                if tick:
                    preco_atual = tick.last or tick.bid or preco_atual
    except Exception:
        pass

    # `or 0` não pega NaN (NaN é truthy em Python) — se o candle mais
    # recente ainda não tiver essa feature calculada, int(NaN) levantaria
    # ValueError a cada tick. Checagem explícita de NaN antes de converter.
    direcao_raw = ultimo.get('direcao_candle', 0)
    direcao = int(direcao_raw) if pd.notna(direcao_raw) else 0

    return {
        'preco': preco_atual,
        'tipo_candle': str(ultimo.get('tipo_candle', '—')),
        'direcao': direcao,
        'atr': atr,
        'datetime': str(ultimo.get('datetime', '')),
    }


# Atualização incremental em background a cada 30s (não bloqueia a UI)
@app.callback(
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('interval-slow', 'n_intervals'),
    prevent_initial_call=True,
)
def atualizar_incremental_background(n):
    """
    Dispara atualização incremental do MT5 a cada 30s em background.
    Usa thread separada para não bloquear a UI durante a busca de dados.
    """
    if not estado['atualizando']:
        threading.Thread(target=carregar_bases, daemon=True).start()
    return ''


def _sincronizar_posicoes_background():
    """Roda em thread separada (mesmo padrão de carregar_bases) — busca
    posições abertas E ordens pendentes no MT5, sincroniza com
    posicoes_abertas.parquet/ordens_pendentes.parquet (ambas consumidas pela
    aba "Posição" do Diário, não mais exibidas aqui), guardando o resultado
    de posições em estado['posicoes_abertas'] (em memória, mesmo padrão de
    estado['bases']). Falha do MT5 vira 'sem dado agora' (None), nunca
    propaga — mesmo espírito de atualizar_candle_atual/symbol_info_tick."""
    with lock:
        estado['sincronizando_posicoes'] = True
    try:
        try:
            df_pos = mt5_loader.buscar_posicoes_abertas() if MT5_OK else None
        except Exception:
            df_pos = None
        try:
            resultado = diario_trade.sincronizar_posicoes_abertas(df_pos)
        except Exception:
            # Bug real 2026-07-21: essa chamada não era protegida — uma
            # exceção aqui (ex: MT5 IPC falhando na detecção de fechamento,
            # ver diario_trade.sincronizar_posicoes_abertas) abortava a
            # função inteira ANTES de persistir df_pos, congelando
            # posicoes_abertas.parquet num snapshot velho (posição nova
            # some da tela, exposição residual calculada errada) sem
            # nenhum aviso. Loga em arquivo (nunca console) e segue —
            # o próximo ciclo de 3s tenta de novo.
            log_sync_posicoes.exception('Falha em sincronizar_posicoes_abertas')
            resultado = None
        if resultado is not None:
            with lock:
                estado['posicoes_abertas'] = resultado

        try:
            df_ordens = mt5_loader.buscar_ordens_pendentes() if MT5_OK else None
        except Exception:
            df_ordens = None
        diario_trade.sincronizar_ordens_pendentes(df_ordens)
    finally:
        with lock:
            estado['sincronizando_posicoes'] = False


# Sincronização de posições abertas numa cadência PRÓPRIA (nem o tick de 1s
# nem o de 30s) — precisa ser mais responsiva que o refresh de candle (o
# usuário quer ver o rascunho aparecer no Diário logo depois de abrir uma
# posição), mas escrever em disco a cada 1s repetiria o bug de performance
# já corrigido no gráfico de candles (nunca I/O pesado no tick rápido).
@app.callback(
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('interval-posicao-sync', 'n_intervals'),
    prevent_initial_call=True,
)
def sincronizar_posicoes(n):
    """Dispara a sincronização de posições abertas em background (thread
    separada, não bloqueia a UI), pulando se o ciclo anterior ainda não
    terminou."""
    if not estado['sincronizando_posicoes']:
        threading.Thread(target=_sincronizar_posicoes_background, daemon=True).start()
    return ''


def _reconciliar_background():
    """Roda em thread separada (mesmo padrão de _sincronizar_posicoes_
    background) — ver dashboard_98.py::_reconciliar_background pro mesmo
    raciocínio completo."""
    with lock:
        estado['reconciliando'] = True
    try:
        diario_trade.reconciliar_com_mt5(dias_atras=30)
    except Exception:
        pass
    finally:
        with lock:
            estado['reconciliando'] = False


@app.callback(
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('interval-reconciliacao', 'n_intervals'),
    prevent_initial_call=True,
)
def reconciliar_background(n):
    if not estado['reconciliando']:
        threading.Thread(target=_reconciliar_background, daemon=True).start()
    return ''


@app.callback(
    Output('store-candles-grafico', 'data'),
    Input('interval-slow', 'n_intervals'),
    Input('store-ativo', 'data'),
)
def atualizar_candles_grafico_completo(n_slow, ativo):
    """Histórico completo (300 candles) pro Lightweight Charts — só troca de
    ativo ou o tick de 30s disparam isso, nunca o de 1s (ver comentário
    junto de `store-candle-grafico-live`/`atualizar_dashboard` pro porquê:
    recalcular e reserializar 300 candles a cada segundo, mais o cliente
    reconstruindo o gráfico inteiro via `setData()` a cada tick, seria caro
    de propósito nenhum)."""
    with lock:
        bases = estado['bases']
    df_ativo = bases.get(ativo)
    return candles_para_grafico(df_ativo) if df_ativo is not None else []


# -----------------------------------------------------------------------------
# TRAÇADOS — aba "Traçados" (ver tracados.py). `mover` só persiste + deixa
# o recompute de Estudos reagir (Input compartilhado abaixo) — o cliente já
# moveu a linha sozinho antes mesmo do round-trip terminar (ver
# assets/lwchart.js), então não precisa disparar store-tracados-servidor de
# novo. `criar` é diferente: o clique não desenha nada no cliente sozinho
# (só existe depois que o servidor persiste), então esse Output extra é o
# que faz a linha aparecer imediatamente em vez de só no próximo tick de
# 30s (bug relatado 2026-07-13) — round-trip local, na prática instantâneo.
# -----------------------------------------------------------------------------

@app.callback(
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Output('store-tracados-servidor', 'data', allow_duplicate=True),
    Input('store-tracado-criar', 'data'),
    State('store-ativo', 'data'),
    prevent_initial_call=True,
)
def criar_tracado_callback(payload, ativo):
    if not payload or not ativo:
        return no_update, no_update
    ativo_real = ativo
    tracados.criar_tracado(ativo_real, 'horizontal', payload['preco'])
    return '', tracados.listar_tracados(ativo_real).to_dict('records')


@app.callback(
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Output('store-tracados-servidor', 'data', allow_duplicate=True),
    Output('tracado-criar-preco', 'value'),
    Output('tracado-criar-preco-status', 'children'),
    Input('btn-tracado-criar-preco', 'n_clicks'),
    State('tracado-criar-preco', 'value'),
    State('store-ativo', 'data'),
    prevent_initial_call=True,
)
def criar_tracado_por_preco_callback(_n_clicks, preco, ativo):
    """Cria um traçado horizontal digitando o preço direto — sem precisar
    clicar no gráfico (Modo Aprendizagem.txt, 2026-07-20). Mesmo CRUD de
    `criar_tracado_callback` (clique no gráfico), só a origem do preço
    muda. `dcc.Input(type='number')` já entrega float/None do navegador,
    mas aceita string com vírgula decimal por segurança (mesmo tratamento
    de `salvar_edicao_tracado`, pt-BR)."""
    if not ativo:
        return no_update, no_update, no_update, no_update
    if preco is None or (isinstance(preco, str) and not preco.strip()):
        return no_update, no_update, no_update, '⚠ Informe um preço.'
    try:
        preco_float = float(str(preco).replace(',', '.'))
    except ValueError:
        return no_update, no_update, no_update, '⚠ Preço inválido.'
    tracados.criar_tracado(ativo, 'horizontal', preco_float)
    return '', tracados.listar_tracados(ativo).to_dict('records'), None, f'✔ Linha criada em {preco_float:,.0f}.'


@app.callback(
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('store-tracado-mover', 'data'),
    prevent_initial_call=True,
)
def mover_tracado_callback(payload):
    if not payload:
        return no_update
    tracados.atualizar_preco_tracado(payload['id_tracado'], payload['preco'])
    return ''


@app.callback(
    Output('store-tracados-servidor', 'data'),
    Input('interval-slow', 'n_intervals'),
    Input('store-ativo', 'data'),
)
def carregar_tracados_servidor(n_slow, ativo):
    """Carga autoritativa da lista de traçados — dispara na mesma cadência
    de `atualizar_candles_grafico_completo` (30s/troca de ativo), nunca no
    tick de 1s. Alimenta tanto o redesenho do gráfico (clientside_callback
    abaixo) quanto o recompute de Estudos."""
    ativo_real = ativo or 'WIN'
    return tracados.listar_tracados(ativo_real).to_dict('records')


@app.callback(
    Output('store-tracados-servidor', 'data', allow_duplicate=True),
    Input({'type': 'del-tracado', 'index': ALL}, 'n_clicks'),
    State('store-ativo', 'data'),
    prevent_initial_call=True,
)
def apagar_tracado_callback(n_clicks_lista, ativo):
    """Mesmo padrão de `clicar_dia_calendario` (diario_app_classico.py) —
    pattern-matching, guarda contra o disparo fantasma de quando os N
    botões são recriados (n_clicks de todos em 0 nesse caso, `any()` falso)."""
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update
    prop_id = ctx.triggered[0]['prop_id'].rsplit('.', 1)[0]
    componente_id = json.loads(prop_id)
    tracados.remover_tracado(componente_id['index'])
    ativo_real = ativo or 'WIN'
    return tracados.listar_tracados(ativo_real).to_dict('records')


@app.callback(
    Output('store-timeline-expandida', 'data'),
    Input({'type': 'timeline-toggle', 'index': ALL}, 'n_clicks'),
    State('store-timeline-expandida', 'data'),
    prevent_initial_call=True,
)
def alternar_timeline_expandida(n_clicks_lista, expandidos):
    """Botão "Ver histórico de toques" — Price Action E Estudos dos
    Traçados usam o mesmo tipo de id (`timeline-toggle`), chave estável
    por card. Mesmo padrão/guarda de `apagar_tracado_callback`."""
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update
    prop_id = ctx.triggered[0]['prop_id'].rsplit('.', 1)[0]
    chave = json.loads(prop_id)['index']
    expandidos = dict(expandidos or {})
    expandidos[chave] = not expandidos.get(chave, False)
    return expandidos


@app.callback(
    Output('estudos-conteudo', 'children'),
    Input('interval-slow', 'n_intervals'),
    Input('store-tracado-criar', 'data'),
    Input('store-tracado-mover', 'data'),
    Input('store-tracados-servidor', 'data'),
    Input('store-timeline-expandida', 'data'),
    State('store-ativo', 'data'),
)
def atualizar_estudos(n_slow, _criar, _mover, _servidor, expandidos, ativo):
    """Recomputa a leitura quantitativa de cada traçado — nunca no tick de
    1s (os toques podem mudar passivamente conforme novos candles se
    formam, daí o Input em interval-slow também, não só nas ações do
    usuário). `store-timeline-expandida` também é Input aqui (diferente do
    tick de 1s da Price Action, que usa State) — Estudos só re-renderiza a
    cada 30s/ação; sem isso, o clique de expandir demoraria até 30s pra
    aparecer."""
    ativo_real = ativo or 'WIN'
    with lock:
        bases = estado['bases']
    df_ativo = bases.get(ativo_real)
    if df_ativo is None:
        return no_update
    tracs = tracados.listar_tracados(ativo_real)
    analises = analisar_tracados(df_ativo, tracs)
    return construir_estudos_tracados(analises, expandidos or {})


# -----------------------------------------------------------------------------
# POPUP DE EDIÇÃO DO TRAÇADO — duplo clique na linha (ver
# assets/lwchart.js::wireFerramentaDesenho, store-tracado-editar). Mesmo
# padrão de store+overlay+callback de `diario-trade-detalhe-id` em
# diario_app_classico.py.
# -----------------------------------------------------------------------------

@app.callback(
    Output('tracado-overlay-editar', 'children'),
    Input('store-tracado-editar', 'data'),
    State('store-ativo', 'data'),
)
def renderizar_modal_editar_tracado(id_tracado, ativo):
    if not id_tracado:
        return None
    return _modal_editar_tracado(id_tracado, ativo)


@app.callback(
    Output('store-tracado-editar', 'data', allow_duplicate=True),
    Input('btn-tracado-editar-cancelar', 'n_clicks'),
    Input('tracado-editar-backdrop', 'n_clicks'),
    prevent_initial_call=True,
)
def fechar_modal_editar_tracado(n1, n2):
    return None


@app.callback(
    Output('tracado-editar-cor', 'data'),
    Input({'type': 'tracado-cor-swatch', 'cor': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def selecionar_cor_tracado(n_clicks_lista):
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update
    componente_id = json.loads(ctx.triggered[0]['prop_id'].rsplit('.', 1)[0])
    return componente_id['cor']


@app.callback(
    Output('tracado-editar-cor-swatches', 'children'),
    Input('tracado-editar-cor', 'data'),
)
def destacar_cor_selecionada_tracado(cor_atual):
    return _swatches_cor_tracado(cor_atual)


@app.callback(
    Output('store-tracado-editar', 'data', allow_duplicate=True),
    Output('store-tracados-servidor', 'data', allow_duplicate=True),
    Input('btn-tracado-editar-salvar', 'n_clicks'),
    State('store-tracado-editar', 'data'),
    State('tracado-editar-preco', 'value'),
    State('tracado-editar-cor', 'data'),
    State('tracado-editar-espessura', 'value'),
    State('tracado-editar-estilo', 'value'),
    State('store-ativo', 'data'),
    prevent_initial_call=True,
)
def salvar_edicao_tracado(n_clicks, id_tracado, preco, cor, espessura, estilo, ativo):
    if not id_tracado or preco is None or preco == '':
        return no_update, no_update
    # str(preco).replace(',', '.') — campo numérico nativo, em máquina com
    # configuração regional pt-BR, pode devolver o valor com vírgula
    # decimal em vez de ponto (bug relatado 2026-07-14: 'não salva' era
    # esse ValueError silencioso derrubando o callback inteiro, sem
    # feedback nenhum pro usuário). try/except em vez de deixar estourar —
    # um valor ilegível não deve travar o popup aberto pra sempre.
    try:
        preco_float = float(str(preco).replace(',', '.'))
        espessura_int = int(espessura)
    except (TypeError, ValueError):
        return no_update, no_update
    tracados.atualizar_tracado(id_tracado, preco_float, cor, espessura_int, estilo)
    ativo_real = ativo or 'WIN'
    return None, tracados.listar_tracados(ativo_real).to_dict('records')


@app.callback(
    Output('store-tracado-editar', 'data', allow_duplicate=True),
    Output('store-tracados-servidor', 'data', allow_duplicate=True),
    Input('btn-tracado-editar-excluir', 'n_clicks'),
    State('store-tracado-editar', 'data'),
    State('store-ativo', 'data'),
    prevent_initial_call=True,
)
def excluir_tracado_do_modal(n_clicks, id_tracado, ativo):
    if not id_tracado:
        return no_update, no_update
    tracados.remover_tracado(id_tracado)
    ativo_real = ativo or 'WIN'
    return None, tracados.listar_tracados(ativo_real).to_dict('records')


# Redesenho AUTORITATIVO dos traçados a partir do servidor (carga inicial +
# depois de apagar) — ver desenharTracados em assets/lwchart.js e o
# comentário nos stores acima pro porquê de criar/mover NUNCA disparar isso.
app.clientside_callback(
    """
    function(lista) {
        var c = window.SQ_CHART;
        if (c && c.desenharTracados) c.desenharTracados(lista || []);
        return '';
    }
    """,
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('store-tracados-servidor', 'data'),
    prevent_initial_call=True,
)


@app.callback(
    Output('store-mme21-serie', 'data'),
    Input('interval-slow', 'n_intervals'),
    Input('store-ativo', 'data'),
)
def atualizar_mme21_serie_completo(n_slow, ativo):
    """Histórico completo da MME21 (300 pontos) — mesma cadência rara de
    `atualizar_candles_grafico_completo` (30s/troca de ativo), mesmo motivo
    (setData() reconstrói a linha inteira, caro demais pra rodar a 1s).
    Tag de `ativo` no payload — mesmo motivo do `store-niveis-sr` (ver
    comentário lá): descarta respostas atrasadas de um ativo já trocado."""
    with lock:
        bases = estado['bases']
    df_ativo = bases.get(ativo)
    serie = mme21_para_grafico(df_ativo) if df_ativo is not None else []
    return {'ativo': ativo, 'serie': serie}


@app.callback(
    Output('hero-conteudo', 'children'),
    Output('market-view-conteudo', 'children'),
    Output('price-action-conteudo', 'children'),
    Output('status-conexao', 'children'),
    Output('status-contrato', 'children'),
    Output('status-atualizacao', 'children'),
    Output('status-calendario', 'children'),
    Output('status-calendario', 'style'),
    Output('store-candle-grafico-live', 'data'),
    Output('store-niveis-sr', 'data'),
    Output('store-mme21-live', 'data'),
    Output('rastreador-conteudo', 'children'),
    Input('interval-live', 'n_intervals'),
    Input('store-ativo', 'data'),
    Input('store-candle-atual', 'data'),
    State('store-timeline-expandida', 'data'),
    State('store-tema', 'data'),
)
def atualizar_dashboard(n_intervals, ativo, candle_atual, timeline_expandida, tema_atual):
    """
    Callback principal — dispara a cada 1s via interval-live.
    A leitura das métricas é leve (só lê o DataFrame já em memória).
    A reconstrução de HTML completa ocorre a cada tick, mas é rápida
    porque não há chamada de rede ou cálculo pesado aqui.
    """
    with lock:
        bases       = estado['bases']
        atualizando = estado['atualizando']
        ultima      = estado['ultima_atualizacao']
        erro        = estado['erro']
        c_win       = estado['contrato_win']
        c_wdo       = estado['contrato_wdo']
        cotacoes_correlacionadas = estado['cotacoes_correlacionadas']
        fechamentos_di = estado['fechamentos_di']

    df_win   = bases.get('WIN')
    df_wdo   = bases.get('WDO')
    df_ativo = df_win if ativo == 'WIN' else df_wdo

    m_win = extrair_metricas(df_win) if df_win is not None else {}
    m_wdo = extrair_metricas(df_wdo) if df_wdo is not None else {}
    m     = m_win if ativo == 'WIN' else m_wdo
    indice_mg = indice_medo_ganancia.calcular_indice_medo_ganancia(df_win, df_wdo)
    correlacao = correlacao_ativos.calcular_correlacao_di(df_win, df_wdo, fechamentos_di)

    # Injeta preço em tempo real do store rápido (atualizado a 1s)
    if candle_atual and candle_atual.get('preco'):
        m['preco'] = candle_atual['preco']
        m['tipo_candle'] = candle_atual.get('tipo_candle', m.get('tipo_candle', '—'))

    # Status
    agora_dt = datetime.now()
    agora = agora_dt.strftime('%H:%M:%S')
    if atualizando:
        status = '● Atualizando...'
    elif erro:
        status = f'⚠ {erro}'
    elif df_ativo is None:
        status = '⚠ Base não encontrada'
    else:
        status = f'● {len(df_ativo):,} candles'

    contrato_str = f'{c_win} · {c_wdo}'
    upd_str = f'Última atualização: {agora}'

    # Janela de notícia (Copom/FOMC/NFP) — não opera notícia, mas esses
    # horários distorcem a leitura de S/R (ver calendario_economico.py).
    cal_str, cal_style = '', {'fontSize': '10px', 'fontFamily': FONTS['display'], 'color': C['text2']}
    evento_ativo = cal_eco.janela_de_noticia_ativa(agora_dt, margem_minutos=30)
    if evento_ativo:
        cal_str = f"⚠ JANELA DE NOTÍCIA: {evento_ativo['nome']}"
        cal_style = {**cal_style, 'color': C['red'], 'fontWeight': '700'}
    else:
        proximos = cal_eco.proximos_eventos(agora_dt, janela_horas_antes=0, janela_horas_depois=48)
        if proximos:
            prox = proximos[0]
            delta = prox['datetime_brt'].replace(tzinfo=None) - agora_dt
            horas = delta.total_seconds() / 3600
            cal_str = f"{prox['nome']} em {horas:.0f}h ({prox['datetime_brt'].strftime('%d/%m %H:%M')})"

    if df_ativo is None:
        placeholder = html.Div(
            'Base de dados não encontrada. Acesse CONFIGURAÇÕES para fazer o setup inicial.',
            style={'color': C['text2'], 'fontFamily': FONTS['body'], 'padding': '20px'}
        )
        return (placeholder, placeholder, placeholder,
                status, contrato_str, upd_str, cal_str, cal_style, None,
                {'ativo': ativo, 'niveis': []}, {'ativo': ativo, 'ponto': None}, placeholder)

    big_players  = analisar_big_players_resumo(df_ativo)
    hero         = construir_hero(m_win, m_wdo, ativo, indice_mg, correlacao, cotacoes_correlacionadas,
                                   tema_atual or 'light', big_players)
    market_view  = construir_market_view(m)
    price_action = construir_price_action(df_ativo, m, timeline_expandida or {})
    # Marcado com o `ativo` que gerou esses níveis — o clientside_callback
    # de desenho (ver assets/lwchart.js::desenharNiveisSR) descarta a
    # resposta se esse rótulo não bater com o `store-ativo` atual do
    # navegador. Sem isso, uma resposta deste callback disparada pelo
    # tick de 1s ANTES da troca de ativo, mas que chega ao navegador DEPOIS
    # da resposta disparada pela troca em si (`threaded=True`, ordem de
    # chegada não é garantida), reintroduzia linhas de S/R da escala do
    # ativo antigo (WIN ~170-190 mil vs WDO ~5 mil) por cima dos candles
    # novos — bug relatado em 2026-07-10.
    niveis_sr    = {'ativo': ativo, 'niveis': niveis_sr_para_grafico(m)}

    # Só o candle EM FORMAÇÃO (1 item, não os 300 — ver
    # `atualizar_candles_grafico_completo` pro histórico completo, que roda
    # numa cadência bem mais rara) — preço ao vivo já injetado em `m['preco']`
    # acima, aplicado aqui pra ele "respirar" junto com o tick de 1s, sem
    # esperar o candle M5 fechar. Cliente aplica via `.update()` (1 candle),
    # nunca `setData()` (array inteiro) — ver assets/lwchart.js.
    preco_live = candle_atual.get('preco') if candle_atual else None
    candle_live = candles_para_grafico(df_ativo, n=1)
    candle_live = candle_live[0] if candle_live else None
    if candle_live and preco_live:
        candle_live['close'] = preco_live
        candle_live['high'] = max(candle_live['high'], preco_live)
        candle_live['low'] = min(candle_live['low'], preco_live)

    # MME21 — mesmo cuidado de tag de `ativo` do restante (ver niveis_sr acima).
    mme21_live = {'ativo': ativo, 'ponto': mme21_ponto_live(df_ativo, preco_live)}
    rastreador_analises = analisar_rastreador_tendencia(df_ativo)
    rastreador = construir_rastreador_tendencia(rastreador_analises)

    return (hero, market_view, price_action,
            status, contrato_str, upd_str, cal_str, cal_style, candle_live, niveis_sr,
            mme21_live, rastreador)


@app.callback(
    Output('chat-modal-container', 'style'),
    Input('btn-chat-toggle', 'n_clicks'),
    Input('btn-chat-fechar', 'n_clicks'),
    State('chat-modal-container', 'style'),
    prevent_initial_call=True,
)
def toggle_chat(n_toggle, n_fechar, estilo_atual):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    botao = ctx.triggered[0]['prop_id'].split('.')[0]

    estilo_aberto = {
        'display': 'flex', 'position': 'fixed',
        'bottom': '96px', 'right': '28px',
        'width': '420px', 'height': '520px',
        'background': C['panel'], 'border': f'1px solid {C["border"]}',
        'borderRadius': '16px', 'zIndex': '999',
        'boxShadow': '0 8px 40px rgba(0,0,0,0.6)',
        'flexDirection': 'column', 'overflow': 'hidden',
    }
    estilo_fechado = {**estilo_aberto, 'display': 'none'}

    esta_aberto = estilo_atual.get('display') != 'none'

    if botao == 'btn-chat-fechar':
        return estilo_fechado
    elif botao == 'btn-chat-toggle':
        return estilo_fechado if esta_aberto else estilo_aberto

    return no_update


@app.callback(
    Output('chat-historico', 'children'),
    Output('chat-status', 'children'),
    Output('chat-input', 'value'),
    Output('store-mensagem-pendente', 'data'),
    Output('chat-loading-target', 'children'),
    Input('btn-chat-enviar', 'n_clicks'),
    State('chat-input', 'value'),
    State('chat-historico', 'children'),
    State('store-ativo', 'data'),
    State('chat-modelo', 'value'),
    prevent_initial_call=True,
)
def enviar_mensagem_chat(n_clicks, texto, historico_atual, ativo, modelo):
    if not texto or not texto.strip():
        return no_update, no_update, no_update, no_update, no_update

    texto = texto.strip()

    # Mensagem do usuário
    msg_usuario = html.Div([
        html.Div(texto, style={
            'background': C['border'], 'borderRadius': '10px 10px 2px 10px',
            'padding': '10px 14px', 'fontSize': '13px',
            'fontFamily': FONTS['body'], 'color': C['text'],
            'maxWidth': '85%', 'alignSelf': 'flex-end',
        }),
    ], style={'display': 'flex', 'justifyContent': 'flex-end'})

    # Preloader enquanto a IA processa
    preloader = html.Div([
        html.Div([
            html.Span('✦ ', style={'color': C['gold'], 'fontSize': '11px'}),
            html.Span('...', style={
                'color': C['text2'], 'fontStyle': 'italic',
                'animation': 'pulse 1s infinite',
            }),
        ], style={
            'background': 'var(--bolha-neutra)', 'borderRadius': '10px 10px 10px 2px',
            'padding': '10px 14px', 'fontSize': '13px',
            'fontFamily': FONTS['body'], 'color': C['text'],
            'maxWidth': '90%', 'borderLeft': f'2px solid {C["gold"]}',
        }),
    ], id='msg-preloader', style={'display': 'flex', 'justifyContent': 'flex-start'})

    historico_com_preloader = (historico_atual or []) + [msg_usuario, preloader]

    # Chama a IA (bloqueante — resposta real vem aqui mesmo)
    with lock:
        bases = estado['bases']
    df = bases.get(ativo)
    m = extrair_metricas(df) if df is not None else {}

    try:
        resposta_bruta = ai_analyst.analisar_com_pergunta_raw(m, texto, ativo, modelo)

        if isinstance(resposta_bruta, dict) and resposta_bruta.get('erro_429'):
            # Rate limit — calcula timer de espera
            segundos_espera = resposta_bruta.get('retry_after', 60)
            modelo_limitado = resposta_bruta.get('modelo_usado', '')
            sufixo_modelo = f' ({modelo_limitado})' if modelo_limitado else ''
            msg_ia = html.Div([
                html.Div([
                    html.Span('⏱ ', style={'color': C['yellow']}),
                    html.Span(f'Limite de requisições atingido{sufixo_modelo}. Aguarde {segundos_espera}s antes de enviar novamente.',
                              style={'color': C['yellow']}),
                ], style={
                    'background': 'var(--bolha-atencao)', 'borderRadius': '10px 10px 10px 2px',
                    'padding': '10px 14px', 'fontSize': '13px',
                    'fontFamily': FONTS['body'],
                    'maxWidth': '90%', 'borderLeft': f'2px solid {C["yellow"]}',
                }),
            ], style={'display': 'flex', 'justifyContent': 'flex-start'})

            tokens_str = ''
            status_str = f'⏱ Aguarde {segundos_espera}s · Limite da API atingido'

        elif isinstance(resposta_bruta, dict) and resposta_bruta.get('erro'):
            msg_ia = html.Div([
                html.Div([
                    html.Span(f'⚠ {resposta_bruta["erro"]}',
                              style={'color': C['red']}),
                ], style={
                    'background': 'var(--bolha-erro)', 'borderRadius': '10px 10px 10px 2px',
                    'padding': '10px 14px', 'fontSize': '13px',
                    'fontFamily': FONTS['body'],
                    'maxWidth': '90%', 'borderLeft': f'2px solid {C["red"]}',
                }),
            ], style={'display': 'flex', 'justifyContent': 'flex-start'})
            tokens_str = ''
            status_str = '⚠ Erro na API'

        else:
            resposta = resposta_bruta.get('texto', '')
            tokens_usados = resposta_bruta.get('tokens_usados', 0)
            tokens_max = cfg.GEMINI_MAX_TOKENS
            pct = round(tokens_usados / tokens_max * 100) if tokens_max > 0 else 0

            msg_ia = html.Div([
                html.Div([
                    html.Span('✦ ', style={'color': C['gold'], 'fontSize': '11px'}),
                    html.Span(resposta, style={'whiteSpace': 'pre-wrap', 'lineHeight': '1.6'}),
                ], style={
                    'background': 'var(--bolha-neutra)', 'borderRadius': '10px 10px 10px 2px',
                    'padding': '10px 14px', 'fontSize': '13px',
                    'fontFamily': FONTS['body'], 'color': C['text'],
                    'maxWidth': '90%', 'borderLeft': f'2px solid {C["gold"]}',
                }),
            ], style={'display': 'flex', 'justifyContent': 'flex-start'})

            ts = datetime.now().strftime('%H:%M:%S')
            modelo_usado = resposta_bruta.get('modelo_usado', '')
            status_str = f'{ts} · {tokens_usados} tokens ({pct}% do limite) · {modelo_usado}'

    except Exception as e:
        msg_ia = html.Div([
            html.Div(f'⚠ Erro inesperado: {str(e)[:60]}',
                     style={'color': C['red'], 'fontSize': '13px',
                            'fontFamily': FONTS['body']}),
        ], style={'display': 'flex'})
        status_str = '⚠ Erro inesperado'

    novo_historico = (historico_atual or []) + [msg_usuario, msg_ia]

    return novo_historico, status_str, '', None, ''


# -----------------------------------------------------------------------------
# CABEÇALHO — troca de aba real (QUANT LAB/BACKTESTING/DIÁRIO/CONFIGURAÇÕES)
# -----------------------------------------------------------------------------
# Primeira vez que essas 4 abas viram funcionais de verdade — antes eram só
# decoração (sem callback nenhum, `conteudo-principal` sempre mostrava o
# mesmo conteúdo). Mesma disciplina do seletor de ativo: nunca recriar os
# botões, só trocar `style`/`display` (evita o bug histórico de reset de
# `n_clicks` documentado no CHANGELOG.md de 2026-07-04).

_ABAS_CABECALHO = ['lab', 'back', 'diary', 'config']


@app.callback(
    Output('store-aba-ativa', 'data'),
    [Input(f'tab-{chave}', 'n_clicks') for chave in _ABAS_CABECALHO],
    prevent_initial_call=True,
)
def selecionar_aba(*_n_clicks):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    botao_id = ctx.triggered[0]['prop_id'].split('.')[0]
    return botao_id.replace('tab-', '')


@app.callback(
    [Output(f'tab-{chave}', 'style') for chave in _ABAS_CABECALHO],
    Input('store-aba-ativa', 'data'),
)
def atualizar_estilo_aba_ativa(aba_ativa):
    return [_estilo_aba(chave, aba_ativa) for chave in _ABAS_CABECALHO]


@app.callback(
    Output('painel-quantlab', 'style'),
    Output('painel-diario', 'style'),
    Output('painel-back', 'style'),
    Output('painel-config', 'style'),
    Input('store-aba-ativa', 'data'),
)
def atualizar_painel_principal(aba_ativa):
    def _estilo(chave):
        return {} if chave == aba_ativa else {'display': 'none'}
    return (_estilo('lab'), _estilo('diary'),
            _estilo('back'), _estilo('config'))


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    # Diário de Trade roda em processo próprio (embutido via <iframe> no
    # painel 'painel-diario' acima) — lança automaticamente se ainda não
    # estiver de pé. Idempotente: não duplica se já tiver subido.
    gp.garantir_processo_filho(8053, 'diario_app_classico.py', str(Path(__file__).parent))

    print("=" * 60)
    print("  SUPER QUANT — Anticovardia")
    print("  Abrindo em: http://127.0.0.1:8050")
    print("=" * 60)
    app.run(debug=False, host='127.0.0.1', port=8050, threaded=True)
