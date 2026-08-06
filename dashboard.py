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

from dash import _dash_renderer
_dash_renderer._set_react_version('18.2.0')
import dash
from dash import dcc, html, Input, Output, State, callback_context, no_update, ALL
import dash_mantine_components as dmc
import dash_ag_grid as dag
from dash_iconify import DashIconify
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
import threading
import json
import calendar
import logging
from datetime import date, datetime, timedelta

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
import diario_trade as dt
import aprendizagem
import tracados
import indice_medo_ganancia
import correlacao_ativos
import calendario_economico as cal_eco
import ai_analyst
import metricas
import backtesting_tick as backtest
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

                        # Detecta contratos vigentes para exibição no status.
                        # 2026-08-02, bug real encontrado após relato de "SQ
                        # muito lento/travado": estas 4 chamadas (MT5 direto
                        # + rede pro DI) ficavam DENTRO do `with lock:` —
                        # `detectar_contrato_mais_recente` faz várias
                        # chamadas `mt5.copy_rates_from_pos` em loop, e a
                        # IPC do MT5 é documentadamente instável (ver
                        # CLAUDE.md). Se qualquer uma travasse, o `lock`
                        # (compartilhado por TODO o resto do app pra ler
                        # `estado`) ficava preso indefinidamente, congelando
                        # a navegação inteira do painel pai — o processo
                        # filho do Diário (porta 8053) continuava respondendo
                        # normal, já que é outro processo/lock, confirmando o
                        # diagnóstico. Corrigido no mesmo padrão já usado em
                        # `_sincronizar_posicoes_background`: chamadas
                        # bloqueantes rodam FORA do lock, só a escrita no
                        # `estado` (rápida, nunca trava) roda dentro.
                        bases_atuais = estado['bases']
                        try:
                            t = mt5_loader.detectar_contrato_mais_recente('WIN')
                        except Exception:
                            t = None
                        try:
                            w = mt5_loader.detectar_contrato_mais_recente('WDO')
                        except Exception:
                            w = None
                        # Ativos correlacionados (DI1FUT/WINFUT/WDOFUT) pro
                        # painel do hero — mesma cadência de 30s, nunca no
                        # tick de 1s (evita chamadas MT5 extras por segundo).
                        # Ver correlacao_ativos.py.
                        try:
                            cotacoes_correlacionadas = correlacao_ativos.obter_cotacoes_correlacionadas(
                                bases_atuais.get('WIN'), bases_atuais.get('WDO'))
                        except Exception:
                            cotacoes_correlacionadas = None
                        try:
                            fechamentos_di = correlacao_ativos.obter_fechamentos_di()
                        except Exception:
                            fechamentos_di = None

                        with lock:
                            estado['contrato_win'] = t or '—'
                            estado['contrato_wdo'] = w or '—'
                            estado['cotacoes_correlacionadas'] = cotacoes_correlacionadas
                            estado['fechamentos_di'] = fechamentos_di

                        # Cabeçalho de conexão em Posição (Playbook,
                        # 2026-08-03) — capturado aqui (única thread que
                        # fala com o MT5 de verdade) e persistido pro
                        # processo FILHO (diario_app_classico.py) ler,
                        # mesmo padrão de `posicoes_abertas.parquet`.
                        try:
                            conectividade = mt5_loader.checar_conectividade()
                            conta = mt5_loader.obter_info_conta()
                            persistencia.salvar_status_conexao({
                                'conectado': conectividade.get('status') == 'ok',
                                'status': conectividade.get('status'),
                                'latencia_ms': conectividade.get('latencia_ms'),
                                'login': conta.get('login') if conta else None,
                                'servidor': conta.get('servidor') if conta else None,
                                'saldo': conta.get('saldo') if conta else None,
                                'patrimonio': conta.get('patrimonio') if conta else None,
                                'margem_livre': conta.get('margem_livre') if conta else None,
                                'moeda': conta.get('moeda') if conta else None,
                                'atualizado_em': datetime.now(),
                            })
                        except Exception:
                            pass

                        mt5_loader.desconectar()
                    except Exception as e:
                        with lock:
                            estado['erro'] = f"MT5: {str(e)[:60]}"
                        try:
                            persistencia.salvar_status_conexao({
                                'conectado': False, 'status': 'erro_conexao', 'latencia_ms': None,
                                'login': None, 'servidor': None, 'saldo': None, 'patrimonio': None,
                                'margem_livre': None, 'moeda': None, 'atualizado_em': datetime.now(),
                            })
                        except Exception:
                            pass

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
            f"DI1FUT×WDOFUT: {correlacao['corr_di_wdo']:+.2f} · DI1FUT×WINFUT: {correlacao['corr_di_win']:+.2f} · "
            f"WDOFUT×WINFUT: {correlacao['corr_wdo_win']:+.2f} "
            f"(Pearson, últimos {correlacao['janela_dias']} pregões) — tese (CORRELAÇÕES.md, professor Igor): "
            f"DI acompanha o WDO, é inverso ao WIN, e dólar×índice também é inverso; quanto mais perto disso, mais "
            f"claro o direcionamento. Exceção real: rally de commodities (minério/petróleo) pode subir o índice "
            f"mesmo com juros/dólar em alta, por causa do peso de Vale/Petro. Primeiro palpite, não calibrado.",
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
    return html.Button(_icone('tabler:eye', tamanho=14), id=id_botao, type='button', n_clicks=0,
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
    return html.Button(_icone('tabler:eye', tamanho=14), id=id_botao, type='button', n_clicks=0,
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
    # Cadastro editável (`diario_trade.listar_janelas`, substitui
    # `cfg.JANELAS_PREGAO` fixo, 2026-08-04); sem filtro de ativo aqui — este
    # card não recebe o ativo selecionado, mesmo comportamento de antes.
    for _, linha in dt.listar_janelas().iterrows():
        h_i, m_i = map(int, linha['hora_inicio'].split(':'))
        h_f, m_f = map(int, linha['hora_fim'].split(':'))
        if dtime(h_i, m_i) <= t_atual < dtime(h_f, m_f):
            janela_nome = linha['slug'].upper()
            proxima_janela = f"Vai até {linha['hora_fim']}"
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
            html.Div(_texto_com_icone('tabler:alert-triangle', 'MERCADO LATERAL', tamanho=14), style={
                'fontSize': '13px', 'fontWeight': '700', 'color': C['bg'],
                'background': C['yellow'], 'padding': '6px 10px', 'borderRadius': '6px',
                'marginBottom': '10px', 'letterSpacing': '0.05em', 'display': 'inline-flex',
                'alignItems': 'center', 'gap': '4px',
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
            html.Button(_icone('tabler:trash', tamanho=14), id={'type': 'del-tracado', 'index': analise['id_tracado']},
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
            partes.append(html.Div(_texto_com_icone('tabler:alert-triangle', s['detalhe'], tamanho=13), style={
                'fontSize': '12px', 'color': C['gold'], 'fontFamily': FONTS['body'],
                'lineHeight': '1.5', 'marginBottom': '4px', 'display': 'flex', 'alignItems': 'center', 'gap': '4px',
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

# Menu lateral achatado (rollout TradeZella, 2026-07-31 — "faça um menu
# lateral como é no TradeZella... não estamos fazendo melhorias, estamos
# mudando TOTALMENTE a experiência"). Achatado por pedido explícito do
# Menu lateral de DUAS colunas, estilo TradeZella de verdade (2026-07-31,
# pedido explícito do Jesué junto de um print de referência: "Faça um menu
# retrátil. Veja que na lateral esquerda tem botões que abrem menus
# diferentes" — a versão anterior, achatada numa lista só, não tinha essa
# trilha de ícones). `_GRUPOS_MENU` é a fonte única de verdade: cada grupo
# vira 1 ícone fixo na TRILHA (sempre visível, à esquerda); o PAINEL DE
# RÓTULOS ao lado mostra só os itens do grupo ATIVO (os outros ficam com
# `display:none`, nunca desmontados — mesma disciplina de sempre) e pode
# recolher inteiro via `btn-menu-colapsar` (☰), deixando só a trilha de
# ícones.
#
# Nesta mesma leva, 3 decisões de nomenclatura que o Jesué tinha deixado
# "pra depois" (rollout original) e confirmou agora: Histórico exibe como
# "Diário" (chave interna continua `historico`, zero módulo tocado por
# causa disso); Bem-Estar virou "Coaching"; Padrões saiu do nível
# principal e entrou DENTRO de Coaching — é o primeiro grupo com mais de
# 1 seção, e é exatamente o que motivou ter um painel de rótulos por grupo
# em vez de manter tudo achatado. "Novo Trade" saiu do menu por completo:
# virou botão "+ Novo Trade" dentro do Diário, abrindo em pop-up (ver
# `diario_app_classico.py::_construir_diario_historico`), não mais uma
# seção própria.
#
# Ícones em `tabler:*` (via `DashIconify`/`dash-iconify`, pacote NOVO
# instalado nesta leva — correção do Jesué: "Esses emojis do menu não
# estão no estilo tradezella" — TradeZella usa ícones de linha finos e
# monocromáticos, não emoji colorido de fonte do sistema). `dash-iconify`
# empacota o SVG de cada ícone localmente (verificado via inspeção de
# rede num teste isolado — sem chamada a nenhuma API externa em runtime),
# então não introduz dependência de rede.
_GRUPOS_MENU = [
    # (chave_grupo, rótulo_grupo, ícone tabler, [seções do grupo] — None = grupo é a própria seção)
    ('dashboard', 'Dashboard', 'tabler:layout-dashboard', None),
    ('historico', 'Diário', 'tabler:notebook', None),
    ('estrategias', 'Estratégias', 'tabler:puzzle', None),
    ('posicao', 'Posição', 'tabler:map-pin', None),
    ('coaching', 'Coaching', 'tabler:heart',
     [('bem_estar', 'Bem-Estar', 'tabler:heart'), ('padroes', 'Padrões', 'tabler:chart-dots')]),
    ('lab', 'Quant Lab', 'tabler:flask', None),
    ('back', 'Backtesting', 'tabler:player-track-prev', None),
    ('config', 'Configurações', 'tabler:settings', None),
]
_GRUPOS_CHAVES = [g for g, _, _, _ in _GRUPOS_MENU]
_GRUPO_PARA_SECOES = {
    g: ([s for s, _, _ in sub] if sub else [g]) for g, _, _, sub in _GRUPOS_MENU
}
_SECAO_PARA_GRUPO = {s: g for g, secoes in _GRUPO_PARA_SECOES.items() for s in secoes}
# Grupos com árvore de sub-páginas (hoje só Coaching) e a lista achatada
# dessas sub-seções — usados pela fusão trilha+painel (2026-08-04): só
# estes precisam de um `tab-{secao}` próprio, pois grupos diretos usam a
# própria linha (`rail-{grupo}`) como label+alvo de clique, sem duplicar.
_GRUPOS_COM_SUB = [g for g, _, _, sub in _GRUPOS_MENU if sub]
_SUBITENS_TODOS = [s for g, _, _, sub in _GRUPOS_MENU if sub for s, _, _ in sub]
# As 5 seções que continuam renderizadas pelo processo filho — qualquer
# uma delas ativa mostra o MESMO iframe (`painel-diario`), só avisado por
# postMessage (ver `assets/secao_diario.js`) de qual sub-aba mostrar lá
# dentro (mesmo princípio já usado pra tema, `assets/tema.js`). "Novo
# Trade" NÃO entra mais aqui — virou pop-up interno do Diário, nunca é
# `store-aba-ativa` (ver comentário de `_GRUPOS_MENU`).
_ABAS_DIARIO_FILHO = ['historico', 'estrategias', 'posicao', 'padroes', 'bem_estar']


def _estilo_item_menu(ativo: bool) -> dict:
    """Só a cor/fundo reativos ao estado ativo — o resto (padding, gap,
    layout da linha, animação de texto) vive em `.sq-menu-item`/`.sq-menu-
    texto` (`assets/style.css`), pra não recriar a linha inteira a cada
    troca de aba (mesma disciplina de sempre)."""
    return {
        'background': C['gold'] if ativo else 'transparent',
        'color': C['bg'] if ativo else C['text2'],
    }


# Grupos cujo ícone ganha um badge (ponto vermelho, `dmc.Indicator`) com
# pendência real — item 1D do upgrade de 2026-08-04. `atualizar_badges_
# menu` liga/desliga via `disabled`, nunca recria o ícone.
_GRUPOS_COM_BADGE = {'posicao', 'historico', 'coaching'}


def _linha_menu(grupo: str, nome: str, icone: str, sub) -> html.Div:
    """1 linha por grupo do menu lateral — ícone fixo + texto (a MESMA
    linha, nunca duplicada em trilha+painel separados, ver comentário de
    `_GRUPOS_MENU`). Grupos sem sub-seções são o próprio alvo de
    navegação (clique em `rail-{grupo}` já navega, igual sempre foi).
    Grupos com sub-seções (hoje só Coaching) transformam o clique na
    própria linha num toggle de árvore inline (chevron + lista indentada),
    nunca uma navegação direta — sub-clique em `tab-{secao}` é quem
    navega (ver `selecionar_aba`)."""
    icone_el = DashIconify(icon=icone, width=20, className='sq-menu-icone')
    if grupo in _GRUPOS_COM_BADGE:
        icone_el = dmc.Indicator(icone_el, id=f'indicador-menu-{grupo}', size=8, color='red', disabled=True)
    filhos_linha = [
        icone_el,
        html.Span(nome, className='sq-menu-texto'),
    ]
    if sub:
        filhos_linha.append(
            DashIconify(icon='tabler:chevron-right', width=14,
                        className='sq-menu-chevron', id=f'chevron-{grupo}')
        )
    linha = html.Button(filhos_linha, id=f'rail-{grupo}', n_clicks=0, title=nome,
                         className='sq-menu-item', style=_estilo_item_menu(grupo == 'dashboard'))
    if not sub:
        return html.Div(linha)
    arvore = html.Div(
        html.Div([
            dmc.NavLink(label=nome_sub, leftSection=DashIconify(icon=icone_sub, width=16),
                        id=f'tab-{secao_sub}', n_clicks=0, active=False, variant='light',
                        className='sq-submenu-item')
            for secao_sub, nome_sub, icone_sub in sub
        ], className='sq-arvore-inner'),
        id=f'arvore-{grupo}', className='sq-arvore',
    )
    return html.Div([linha, arvore], className='sq-grupo-com-sub')


_EMAIL_USUARIO = 'anticovardia@gmail.com'
_NOME_USUARIO = 'Jesué'


def _bloco_sessao_usuario() -> html.Div:
    """Rodapé do menu — sessão de usuário logado, 100% visual (item 1C do
    upgrade de 2026-08-04): avatar + nome + e-mail + Anticovardia Score
    (mesmo número já usado em `_bloco_anticovardia_score`, não recalcula
    nada) + tier (`dt.nivel_conta`, "capricho visual" — sem sistema de
    login real ainda, pedido explícito do Jesué). Estático, construído 1x
    no layout (nunca recriado por tick nenhum) — os números aqui só mudam
    se o processo reiniciar, mesmo espírito de blocos "hero" do resto do
    app."""
    resultado_score = dt.calcular_anticovardia_score()
    score = resultado_score.get('score')
    stats = dt.calcular_estatisticas()
    n_trades = stats.get('num_operacoes', 0)
    dias_de_uso = 0
    try:
        primeiro_uso = config_operacional.garantir_primeiro_uso()
        dias_de_uso = max((date.today() - date.fromisoformat(primeiro_uso)).days, 0)
    except Exception:
        pass
    tier = dt.nivel_conta(dias_de_uso, n_trades)
    return html.Div([
        html.Div(style={'borderTop': f'1px solid {C["border"]}', 'margin': '8px 12px'}),
        html.Div([
            dmc.Avatar(src='/assets/perfil_anticovardia.png', radius='xl', size=36,
                       className='sq-menu-icone'),
            html.Div([
                html.Div(_NOME_USUARIO, style={
                    'fontSize': '13px', 'fontWeight': '700', 'color': C['text'], 'whiteSpace': 'nowrap',
                }),
                html.Div(_EMAIL_USUARIO, style={
                    'fontSize': '11px', 'color': C['text2'], 'whiteSpace': 'nowrap',
                }),
                html.Div([
                    DashIconify(icon='tabler:crown', width=13, color=C['gold']),
                    html.Span(tier['rotulo'], style={'fontSize': '11px', 'color': C['gold'], 'fontWeight': '600'}),
                    html.Span('·', style={'color': C['text2']}),
                    html.Span(f"Score {score:.0f}" if score is not None else 'Score —',
                              style={'fontSize': '11px', 'color': C['text2']}),
                ], style={'display': 'flex', 'alignItems': 'center', 'gap': '4px', 'marginTop': '2px', 'whiteSpace': 'nowrap'}),
            ], className='sq-menu-texto', style={'overflow': 'hidden'}),
        ], style={'display': 'flex', 'alignItems': 'center', 'gap': '10px', 'padding': '8px 12px'}),
    ])


# =============================================================================
# DASHBOARD (ex-Estatísticas do Diário) — relocado de diario_app_classico.py
# (rollout TradeZella, 2026-07-31: "Tire a aba Estatísticas de dentro do
# Diário, e torne-o a página inicial do Dashboard"). Todo este bloco (tokens
# TZ_*, cards/gráficos/calendário/Mentor de IA/Confiabilidade por
# Estratégia) é código já validado e testado ao longo do item F — movido
# fisicamente pra cá, sem reescrever a lógica. `_formatar_janela`/
# `_mapa_nomes_estrategias` também existem (duplicados) em
# `diario_app_classico.py`, que ainda os usa pra Histórico/Posição.
# =============================================================================

def _formatar_janela(chave: str | None) -> str:
    """"Abertura (9:00-9:30)" — mesma função de `diario_app_classico.py`,
    duplicada aqui (pequena, sem estado) porque os dois processos agora
    precisam dela independentemente. Nome/horário vêm do cadastro editável
    (`diario_trade.listar_janelas`, substitui `cfg.JANELAS_PREGAO` fixo,
    2026-08-04)."""
    if not chave:
        return '—'
    linhas = dt.listar_janelas(somente_ativas=False)
    linha = linhas[linhas['slug'] == chave]
    if linha.empty:
        return chave
    r = linha.iloc[0]
    return f"{r['nome']} ({r['hora_inicio']}-{r['hora_fim']})"


def _mapa_nomes_estrategias() -> dict:
    """slug -> nome de exibição — mesma função de `diario_app_classico.py`,
    duplicada aqui pelo mesmo motivo de `_formatar_janela`."""
    return {l['slug']: l['nome'] for _, l in dt.listar_estrategias(somente_ativas=False).iterrows()}


TZ_RAIO_CARD = '14px'


TZ_RAIO_CELULA = '8px'


TZ_SOMBRA_CARD = '0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04)'


TZ_PADDING_CARD = '18px 20px'


TZ_GAP_GRID = '16px'


TZ_PASTEL_CALENDARIO = {
    'light': {'green_bg': '#DCFCE7', 'red_bg': '#FEE2E2', 'neutro_bg': '#F1F1F6', 'texto': '#14141C'},
}


def _tz_estilo_card(extra: dict | None = None) -> dict:
    """Estilo de card do redesenho TradeZella — sombra suave em vez de
    borda grossa, cantos bem arredondados, padding generoso. Reaproveita
    `C['panel']` (já existente) como fundo, só troca a moldura."""
    base = {
        'background': C['panel'], 'borderRadius': TZ_RAIO_CARD,
        'boxShadow': TZ_SOMBRA_CARD, 'padding': TZ_PADDING_CARD,
    }
    if extra:
        base.update(extra)
    return base


def _bloco_tradezella(titulo, children) -> dmc.Card:
    """Equivalente a `_bloco_diario`, só que `dmc.Card` real (item F, Fase
    2.6 — não mais `html.Div` com sombra pintada à mão) usado em TODA a
    aba Estatísticas (rollout progressivo: só aqui por enquanto, decisão
    do Jesué). NÃO substitui `_bloco_diario` (as outras abas do Diário
    continuam com ele, sem mudança, até a próxima etapa do rollout).

    `w='100%'` (2026-07-31, feedback do Jesué: "não gosto desses espaços
    vazios") — `dmc.Card` não estica sozinho pra preencher o container
    (diferente de um `html.Div` de bloco comum), deixando espaço morto à
    direita quando usado sozinho numa linha. Quando usado EM PAR (ex:
    "Melhor por Horário"/"Melhor por Direção"), o par inteiro precisa estar
    dentro de um `dmc.SimpleGrid` (não `display:flex` cru) pra cada card
    ocupar sua célula por igual — ver `_bloco_analise_estrategia`."""
    return dmc.Card([
        dmc.Text(titulo, size='sm', fw=600, mb='md'),
        children,
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', className='tz-card', w='100%')


def _card_header_tradezella(titulo: str, valor_str, cor_valor=None, componente=None,
                              animar_numero: bool = False) -> dmc.Card:
    """Card estilo TradeZella (item F, Fase 2.6 — `dmc.Card` real, não
    `html.Div` com sombra pintada à mão): elevação nativa (`shadow`/
    `withBorder`), cantos arredondados via `radius`, hover embutido do
    componente. `componente`, se passado, é um objeto DMC animável de
    verdade (`dmc.RingProgress`/`dmc.ProgressRoot`), nunca mais um
    `dcc.Graph` Plotly estático pra esse papel.

    `animar_numero=True` marca o valor com a classe `tz-numero-animado`
    (`assets/tradezella_ux.js` conta de 0 até o valor real ao montar) — só
    seguro pra `valor_str` de STRING simples (nunca quando `valor_str` é
    um componente composto, tipo o badge+texto do Current Streak: o JS
    reescreve `el.textContent`, o que apagaria filhos não-texto)."""
    # `c=None` quebra o parser de cor do Mantine no navegador (Python
    # `None` vira JSON `null`, e `typeof null === 'object'` em JS — o
    # parser só aceita string ou `undefined`) — só passa `c` quando há
    # cor de verdade, mesmo cuidado em `_card_estatistica` logo abaixo.
    kwargs_valor = {'c': cor_valor} if cor_valor else {}
    if animar_numero:
        kwargs_valor['className'] = 'tz-numero-animado'
    texto = html.Div([
        dmc.Text(titulo, size='sm', c='dimmed'),
        dmc.Text(valor_str, size='xl', fw=700, **kwargs_valor),
    ])
    # Gráfico ao LADO do número, não embaixo (2026-07-31, feedback do
    # Jesué: "não gosto desses espaços vazios") — um `dmc.RingProgress` de
    # 70px embaixo de um texto de ~30px deixava um vão vazio à direita
    # dele; lado a lado (`dmc.Group justify='space-between'`) usa a
    # largura do card por inteiro, sem sobra. Cards sem gráfico (Net P&L/
    # Current Streak) ficam só com `texto`, sem vão nenhum — `align=
    # 'center'` centraliza verticalmente contra a altura do gráfico
    # quando ele existe.
    conteudo = dmc.Group([texto, componente], justify='space-between', align='center', wrap='nowrap') \
        if componente is not None else texto
    # `SimpleGrid` estica todo card da linha pra altura do mais alto (o
    # que tem RingProgress/barra) — sem centralizar verticalmente, um
    # card só-texto (Saldo Líquido/Sequência Atual) sobrava um vão em
    # branco embaixo do conteúdo (2026-07-31, 2ª rodada do mesmo
    # feedback "espaço vazio"). `display:flex` + `justifyContent:center`
    # no Card centraliza o conteúdo na altura real da linha, qualquer
    # que seja ela.
    return dmc.Card(conteudo, withBorder=True, shadow='sm', radius='lg', p='lg',
                     className='tz-card',
                     style={'flex': '1', 'minWidth': '190px', 'height': '100%',
                            'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'center'})


def _cards_header_tradezella(stats: dict, tema: str) -> dmc.SimpleGrid:
    """5 cards ao estilo TradeZella (item F): Net P&L / Trade Win% / Avg
    Win-Loss / Profit Factor / Current Streak — `dmc.SimpleGrid` real (não
    `html.Div` com `display:flex` manual) pra responsividade nativa.
    Current Streak é deliberadamente ALL-TIME (`dt.sequencia_atual()` sem
    filtro): um streak "atual" cortado no limite do mês selecionado não
    faz sentido conceitual (um streak real de 3 trades pode atravessar a
    virada do mês)."""
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])

    saldo_total = stats['saldo_total']
    # Sparkline + seta de variação % (2026-07-31, pedido: "esse mini
    # gráfico, e a setinha indicando a % de aumento ou de queda... quero
    # que adicione isso... no Saldo Líquido") — reaproveita
    # `evolucao_saldo_acumulado` (já expansivo, já calculado por
    # `calcular_estatisticas`), sem precisar de dado novo. `valor_str`
    # composto (não string simples) — perde `animar_numero` (mesma
    # ressalva já documentada em `_card_header_tradezella` pra conteúdo
    # composto, mesmo padrão já usado no Current Streak).
    evolucao_saldo = stats['evolucao_saldo_acumulado']
    pct_saldo, direcao_saldo = _variacao_percentual(evolucao_saldo)
    cor_seta_saldo = 'green' if direcao_saldo == 'up' else ('red' if direcao_saldo == 'down' else 'gray')
    seta_saldo = '▲' if direcao_saldo == 'up' else ('▼' if direcao_saldo == 'down' else '—')
    texto_variacao_saldo = f"{seta_saldo} {abs(pct_saldo):.1f}%" if pct_saldo is not None else seta_saldo
    componente_saldo = None
    if len([v for v in evolucao_saldo if v is not None]) >= 2:
        componente_saldo = dcc.Graph(
            figure=_grafico_sparkline(evolucao_saldo, tema, direcao_saldo),
            config={'displayModeBar': False}, style={'width': '70px', 'height': '48px'})
    moeda = stats.get('moeda', 'BRL')
    card_net_pnl = _card_header_tradezella(
        'Saldo Líquido',
        html.Div([
            dmc.Text(metricas.formatar_moeda(saldo_total, moeda), size='xl', fw=700, className='tz-numero-animado',
                      **({'c': 'green' if saldo_total > 0 else 'red'} if saldo_total != 0 else {})),
            dmc.Text(texto_variacao_saldo, size='xs', fw=600, c=cor_seta_saldo, mt=2),
        ]),
        componente=componente_saldo,
    )

    # Anel de 3 seções (Win/Loss/Breakeven), não só um arco verde de % —
    # bate com o gauge real do TradeZella (`image1.png`: "Trade win %"
    # mostra um anel segmentado vermelho/azul/verde com as 3 contagens
    # embaixo, não um arco único de porcentagem).
    win_pct = stats['taxa_acerto'] * 100
    card_win_pct = _card_header_tradezella(
        'Taxa de Acerto',
        html.Div([
            dmc.Text(f"{win_pct:.1f}%", size='xl', fw=700, className='tz-numero-animado'),
            dmc.Group([
                dmc.Text(str(stats['n_loss']), size='xs', c='red', fw=700),
                dmc.Text(str(stats['n_breakeven']), size='xs', c='dimmed', fw=700),
                dmc.Text(str(stats['n_win']), size='xs', c='green', fw=700),
            ], gap=6, mt=2),
        ]),
        componente=dmc.RingProgress(
            size=70, thickness=7, roundCaps=True,
            sections=[
                {'value': stats['n_loss'] / stats['num_operacoes'] * 100, 'color': 'red'},
                {'value': stats['n_breakeven'] / stats['num_operacoes'] * 100, 'color': 'gray'},
                {'value': stats['n_win'] / stats['num_operacoes'] * 100, 'color': 'green'},
            ] if stats['num_operacoes'] else [],
        ),
    )

    payoff = stats['payoff']
    media_ganho, media_perda_abs = stats['media_ganho_reais'], abs(stats['media_perda_reais'])
    total_win_loss = media_ganho + media_perda_abs
    pct_ganho = (media_ganho / total_win_loss * 100) if total_win_loss else 0
    # Valores em R$ embaixo da barra (verde à esquerda, vermelho à
    # direita) — bate com o card real do TradeZella, que mostra "$951" e
    # "-$352" sob os dois lados da barra, não só o número de payoff.
    barra_avg_win_loss = html.Div([
        dmc.ProgressRoot([
            dmc.ProgressSection(value=pct_ganho, color='green'),
            dmc.ProgressSection(value=100 - pct_ganho, color='red'),
        ], size='lg', radius='xl'),
        dmc.Group([
            dmc.Text(metricas.formatar_moeda(media_ganho, moeda, casas=0), size='xs', c='green', fw=600),
            dmc.Text(metricas.formatar_moeda(-media_perda_abs, moeda, casas=0), size='xs', c='red', fw=600),
        ], justify='space-between', mt=4),
    ], style={'width': '120px'}) if total_win_loss else None
    card_avg_win_loss = _card_header_tradezella(
        'Ganho/Perda Médio', f"{payoff:.2f}" if payoff is not None else '—',
        componente=barra_avg_win_loss,
    )

    profit_factor = stats['profit_factor']
    pf_normalizado = min(profit_factor or 0, 3.0) / 3.0 * 100
    card_profit_factor = _card_header_tradezella(
        'Fator de Lucro', f"{profit_factor:.2f}" if profit_factor is not None else '—',
        componente=dmc.RingProgress(
            size=70, thickness=7, roundCaps=True,
            sections=[{'value': pf_normalizado, 'color': 'green' if (profit_factor or 0) >= 1 else 'red'}],
        ),
    )

    streak = dt.sequencia_atual()
    if streak['tipo'] is None:
        streak_str, cor_streak = '—', None
        badge_streak = None
    else:
        cor_streak = 'green' if streak['tipo'] == 'win' else 'red'
        rotulo_trades = 'trade' if streak['trades'] == 1 else 'trades'
        rotulo_dias = 'dia' if streak['dias'] == 1 else 'dias'
        streak_str = f"{streak['trades']} {rotulo_trades} ({streak['dias']} {rotulo_dias})"
        badge_streak = dmc.ThemeIcon(_icone('tabler:flame' if streak['tipo'] == 'win' else 'tabler:snowflake', tamanho=18),
                                       color=cor_streak, variant='light', radius='xl', size='lg')
    card_streak = _card_header_tradezella(
        'Sequência Atual',
        html.Div([badge_streak, html.Span(streak_str, style={'marginLeft': '8px'})],
                  style={'display': 'flex', 'alignItems': 'center'}) if badge_streak else streak_str,
        cor_valor=cor_streak,
    )

    return dmc.SimpleGrid(
        [card_net_pnl, card_win_pct, card_avg_win_loss, card_profit_factor, card_streak],
        cols={'base': 1, 'sm': 2, 'lg': 5}, spacing='md', mb='md',
    )


def _card_estatistica(titulo: str, valor: str, cor=None) -> dmc.Paper:
    """Card de detalhe (grupos Quantidade/Taxas/Valores) — `dmc.Paper`
    real (item F, Fase 2.6) em vez de `html.Div` com fundo pintado."""
    return dmc.Paper([
        dmc.Text(titulo, size='xs', c='dimmed'),
        dmc.Text(valor, size='md', fw=700, **({'c': cor} if cor else {})),
    ], radius='md', p='sm', withBorder=True, style={'minWidth': '120px', 'flex': '1'})


def _variacao_percentual(valores: list) -> tuple:
    """Variação % entre os 2 últimos pontos NÃO-`None` de uma série
    expansiva (2026-07-31, pedido: "mini gráfico, e a setinha indicando a
    % de aumento ou de queda, geralmente usado nas grades de cotações").
    `None` quando há menos de 2 pontos válidos ou o ponto anterior é ~0
    (percentual sobre uma base perto de zero não tem leitura estável —
    nunca fabrica um número aqui). Direção usa o SINAL do delta bruto
    (`atual - anterior`), não do valor em si — funciona igual pra métricas
    que cruzam zero (Saldo Líquido, Fator de Recuperação)."""
    validos = [v for v in valores if v is not None]
    if len(validos) < 2:
        return None, 'flat'
    anterior, atual = validos[-2], validos[-1]
    delta = atual - anterior
    direcao = 'up' if delta > 1e-9 else ('down' if delta < -1e-9 else 'flat')
    if abs(anterior) < 1e-9:
        return None, direcao
    return delta / abs(anterior) * 100, direcao


def _grafico_sparkline(valores: list, tema: str, direcao: str) -> go.Figure:
    """Mini-gráfico de linha, sem eixos/grade — mesmo padrão visual de
    grade de cotações (2026-07-31). Cor segue a direção (verde=subiu,
    vermelho=desceu, cinza=estável), igual à leitura de
    `_variacao_percentual`."""
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    cor = p['green'] if direcao == 'up' else (p['red'] if direcao == 'down' else p['text2'])
    validos = [v for v in valores if v is not None]
    fig = go.Figure(go.Scatter(
        x=list(range(len(validos))), y=validos, mode='lines',
        line=dict(color=cor, width=2, shape='spline', smoothing=0.3),
        fill='tozeroy', fillcolor=_hex_para_rgba(cor, 0.15),
    ))
    fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        margin=dict(l=0, r=0, t=4, b=4),
        xaxis=dict(visible=False, fixedrange=True), yaxis=dict(visible=False, fixedrange=True),
        showlegend=False,
    )
    return fig


def _card_com_sparkline(titulo: str, valor_str: str, valores_serie: list, tema: str,
                          cor_valor=None) -> dmc.Card:
    """Card com mini-gráfico (sparkline) + variação % com seta, estilo
    "grade de cotações" (2026-07-31, pedido: "esse mini gráfico, e a
    setinha indicando a % de aumento ou de queda... adicione isso em
    alguns blocos"). `valores_serie`: pontos recentes da métrica (pode ter
    `None` nos primeiros dias, ex: Payoff antes do 1º trade perdedor) —
    variação calculada só sobre os pontos válidos. Usado por Payoff/Fator
    de Recuperação — o card de Expectativa tem layout próprio, ver
    `_card_expectativa_destaque` (esta versão com `justify='space-between'`
    e só 2 filhos deixava um vão enorme quando esticada pra `w='100%'`)."""
    pct, direcao = _variacao_percentual(valores_serie)
    cor_seta = 'green' if direcao == 'up' else ('red' if direcao == 'down' else 'gray')
    seta = '▲' if direcao == 'up' else ('▼' if direcao == 'down' else '—')
    texto_variacao = f"{seta} {abs(pct):.1f}%" if pct is not None else seta
    tem_grafico = len([v for v in valores_serie if v is not None]) >= 2

    texto = html.Div([
        dmc.Text(titulo, size='xs', c='dimmed'),
        dmc.Text(valor_str, size='md', fw=700, **({'c': cor_valor} if cor_valor else {})),
        dmc.Text(texto_variacao, size='xs', fw=600, c=cor_seta, mt=2),
    ])
    if tem_grafico:
        grafico = dcc.Graph(
            figure=_grafico_sparkline(valores_serie, tema, direcao),
            config={'displayModeBar': False}, style={'width': '64px', 'height': '44px'},
        )
        conteudo = dmc.Group([grafico, texto], gap='sm', wrap='nowrap', justify='space-between')
    else:
        conteudo = texto
    return dmc.Paper(conteudo, radius='md', p='sm', withBorder=True, style={'minWidth': '160px', 'flex': '1'})


def _mini_stat_expectativa(titulo: str, valor: float, tema: str, moeda: str = 'BRL') -> html.Div:
    """Um dos 3 sub-indicadores (Melhor/Pior/Atual) do card de destaque da
    Expectativa Matemática — ver `_card_expectativa_destaque`."""
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    cor = p['green'] if valor > 0 else (p['red'] if valor < 0 else p['text2'])
    return html.Div([
        dmc.Text(titulo, size='xs', c='dimmed'),
        dmc.Text(metricas.formatar_moeda(valor, moeda), size='md', fw=700, style={'color': cor}),
    ])


def _card_expectativa_destaque(serie_expectativa: list, tema: str, moeda: str = 'BRL') -> dmc.Paper:
    """Card de destaque da Expectativa Matemática (2026-07-31, pedido:
    "ficou um espaço muito grande entre o mini gráfico e os dados...
    Expectativa Matemática - Melhor Desempenho / Pior Desempenho e Atual.
    Se ainda sobrar espaço vazio, coloque um breve feedback") — o
    `_card_com_sparkline` genérico usava `justify='space-between'` com só
    2 filhos (gráfico + texto), o que numa largura de 100% deixava um vão
    enorme no meio (visível no screenshot). Aqui o espaço é preenchido com
    conteúdo de verdade: Melhor/Pior/Atual lado a lado (mesmo padrão de
    Vencedora/Perdedora já usado em "Análise de Estratégia"), e um
    feedback dinâmico (`dt.feedback_expectativa`) embaixo se ainda sobrar
    espaço."""
    validos = [v for v in serie_expectativa if v is not None]
    pct, direcao = _variacao_percentual(serie_expectativa)
    cor_seta = 'green' if direcao == 'up' else ('red' if direcao == 'down' else 'gray')
    seta = '▲' if direcao == 'up' else ('▼' if direcao == 'down' else '—')
    texto_variacao = f"{seta} {abs(pct):.1f}%" if pct is not None else seta

    if not validos:
        return dmc.Paper('Sem trades reais suficientes pra calcular a Expectativa ainda.',
                          radius='md', p='lg', withBorder=True, style={'width': '100%', 'fontSize': '12px'})

    atual, melhor, pior = validos[-1], max(validos), min(validos)
    cor_atual = 'green' if atual > 0 else ('red' if atual < 0 else None)

    # TUDO numa linha só (2026-07-31, feedback com screenshot anotado:
    # "coloque na mesma linha... veja o espaço vazio no meio") — a 1ª
    # versão separava título+número+seta (linha 1, `justify=
    # 'space-between'` só com ELE e o gráfico) de Melhor/Pior/Atual
    # (linha 2 embaixo), deixando um vão enorme no meio da linha 1 (só 2
    # filhos esticados pras pontas). Um único `dmc.Group` com todos os 5
    # itens se distribui e preenche a largura toda sem sobra.
    linha = dmc.Group([
        html.Div([
            dmc.Text('Expectativa Matemática', size='sm', c='dimmed'),
            dmc.Text(metricas.formatar_moeda(atual, moeda), size='xl', fw=700,
                      **({'c': cor_atual} if cor_atual else {})),
            dmc.Text(texto_variacao, size='xs', fw=600, c=cor_seta, mt=2),
        ]),
        _mini_stat_expectativa('Melhor Desempenho', melhor, tema, moeda),
        _mini_stat_expectativa('Pior Desempenho', pior, tema, moeda),
        _mini_stat_expectativa('Atual', atual, tema, moeda),
        dcc.Graph(figure=_grafico_sparkline(serie_expectativa, tema, direcao),
                  config={'displayModeBar': False}, style={'width': '84px', 'height': '60px'})
        if len(validos) >= 2 else None,
    ], justify='space-between', wrap='wrap', align='center')

    feedback = _bloco_o_que_fazer(
        'Melhor/pior valor já alcançado pela expectativa matemática acumulada, e onde ela está agora.',
        dt.feedback_expectativa(serie_expectativa, moeda))

    return dmc.Paper([linha, feedback], radius='md', p='lg', withBorder=True, style={'width': '100%'})


def _bloco_breakdown_diario(titulo: str, dados: dict) -> html.Div:
    linhas = [
        html.Div([
            html.Span(chave.replace('_', ' ').title(), style={'flex': '1', 'fontSize': '12px'}),
            html.Span(str(v['n']), style={'width': '34px', 'textAlign': 'right', 'fontSize': '12px',
                                           'fontFamily': FONTS['body']}),
            html.Span(f"R$ {v['saldo']:+.0f}", style={
                'width': '90px', 'textAlign': 'right', 'fontSize': '12px', 'fontFamily': FONTS['body'],
                'color': C['green'] if v['saldo'] > 0 else (C['red'] if v['saldo'] < 0 else C['text2']),
            }),
        ], style={'display': 'flex', 'padding': '4px 0', 'borderBottom': f'1px solid {C["border"]}'})
        for chave, v in dados.items()
    ] or [html.Div('Sem dados.', style={'fontSize': '12px', 'color': C['text2']})]
    return _bloco_tradezella(titulo, html.Div(linhas))


_ROTULO_DIRECAO_OPERACAO = {'long': 'Compra', 'short': 'Venda'}


def _tabela_melhor_por(titulo: str, dados: dict, rotulos_grupo: dict | None = None) -> html.Div:
    """'Melhor estratégia por X' (janela/direção/ativo/categoria) — ver
    `dt.analise_estrategia`. Cada grupo só aparece se alguma estratégia
    atingiu `dt.ANALISE_ESTRATEGIA_MIN_AMOSTRA` NAQUELE recorte; texto
    explícito quando não há nenhum grupo qualificado, em vez de bloco vazio
    sem explicação."""
    nomes = _mapa_nomes_estrategias()
    rotulos_grupo = rotulos_grupo or {}
    if not dados:
        linhas = [html.Div(f'Amostra insuficiente (mínimo {dt.ANALISE_ESTRATEGIA_MIN_AMOSTRA} trades por grupo).',
                           style={'fontSize': '12px', 'color': C['text2']})]
    else:
        linhas = []
        for chave, info in sorted(dados.items(), key=lambda kv: -kv[1]['saldo']):
            rotulo_chave = rotulos_grupo.get(chave, str(chave).replace('_', ' ').title())
            nome_estrategia = nomes.get(info['estrategia'], str(info['estrategia']).replace('_', ' ').title())
            saldo = info['saldo']
            cor = C['green'] if saldo > 0 else (C['red'] if saldo < 0 else C['text2'])
            linhas.append(html.Div([
                html.Span(rotulo_chave, style={'flex': '1', 'fontSize': '12px', 'color': C['text2']}),
                html.Span(nome_estrategia, style={'flex': '1.3', 'fontSize': '12px', 'fontWeight': '600'}),
                html.Span(f"R$ {saldo:+.0f} ({info['n']})", style={
                    'width': '110px', 'textAlign': 'right', 'fontSize': '12px',
                    'fontFamily': FONTS['body'], 'color': cor,
                }),
            ], style={'display': 'flex', 'padding': '4px 0', 'borderBottom': f'1px solid {C["border"]}'}))
    return _bloco_tradezella(titulo, html.Div(linhas))


def _icone(nome_tabler: str, tamanho: int = 13, cor: str | None = None, **kwargs) -> DashIconify:
    """Ícone padrão (Tabler, via `dash_iconify`) — substitui os glifos
    Unicode/emoji soltos usados antes como ícone de status/botão (2026-08-03,
    pedido do Jesué: "REMOVA TODOS OS EMOJIS... identidade visual mais
    refinada com ícones estilizados", mesmo padrão TradeZella já usado em
    `diario_app_classico.py` desde a Fase 2)."""
    if cor:
        kwargs['color'] = cor
    return DashIconify(icon=nome_tabler, width=tamanho, **kwargs)


def _texto_com_icone(nome_tabler: str, texto: str, cor: str | None = None, tamanho: int = 13):
    """Ícone + texto lado a lado — substitui o padrão antigo de prefixar uma
    string crua com um glifo emoji (`f'⚠ {texto}'`, `f'✔ {texto}'`, etc.).
    Retorna uma LISTA (não um `html.Span`) de propósito: usável direto como
    `children` de qualquer componente (`html.Div(_texto_com_icone(...))`) ou
    dentro de uma lista maior (`html.Span(['prefixo: ', *_texto_com_icone(...)])`)."""
    return [_icone(nome_tabler, tamanho=tamanho, cor=cor), f' {texto}']


def _card_estrategia_destaque(titulo: str, icone_tabler: str, info: dict | None, cor_dmc: str) -> dmc.Card:
    """Estratégia Vencedora/Perdedora, destacada de forma ilustrada (pedido
    explícito do docx: "coloque em destaque de forma ilustrativa — por
    exemplo, um troféu ou medalha"). `dmc.Card` + `dmc.ThemeIcon` real
    (item F, Fase 2.6) — `cor_dmc` é um nome de cor Mantine ('green'/
    'red'), não mais a string CSS var `C[...]` usada antes. `icone_tabler`
    era um emoji (🏆/📉) até 2026-08-03, agora um nome de ícone Tabler."""
    nomes = _mapa_nomes_estrategias()
    if info is None:
        conteudo = dmc.Text(f'Amostra insuficiente ainda (mínimo {dt.ANALISE_ESTRATEGIA_MIN_AMOSTRA} trades).',
                             size='sm', c='dimmed')
    else:
        nome = nomes.get(info['estrategia'], str(info['estrategia']).replace('_', ' ').title())
        saldo = info['saldo']
        cor_saldo = 'green' if saldo > 0 else ('red' if saldo < 0 else 'dimmed')
        conteudo = html.Div([
            dmc.Text(nome, size='lg', fw=700),
            dmc.Text(f"R$ {saldo:+.2f} em {info['n']} operações", size='sm', c=cor_saldo, mt=4),
        ])
    return dmc.Card([
        dmc.Group([
            dmc.ThemeIcon(_icone(icone_tabler, tamanho=18), color=cor_dmc, variant='light', radius='xl', size='lg'),
            dmc.Text(titulo, size='sm', fw=600, c='dimmed'),
        ], mb='sm'),
        conteudo,
    ], withBorder=True, shadow='sm', radius='lg', p='lg', className='tz-card',
       style={'flex': '1', 'minWidth': '220px'})


def _conteudo_mentor_ia(cache: dict | None) -> html.Div:
    """Corpo do bloco Mentor de IA — texto cacheado (se houver) ou
    `dmc.Skeleton` (placeholder cinza pulsante — item F, Fase 2.6, real
    componente de loading em vez de só texto estático) quando ainda não
    foi gerado. Usado tanto no render inicial quanto como retorno da
    callback, pra manter os dois caminhos idênticos."""
    if not cache or not cache.get('texto'):
        return html.Div([
            dmc.Skeleton(height=12, width='90%', mb='xs'),
            dmc.Skeleton(height=12, width='75%', mb='xs'),
            dmc.Skeleton(height=12, width='60%'),
            dmc.Text('Ainda não gerada. Clique em "Gerar análise de hoje".', size='xs', c='dimmed', mt='sm'),
        ])
    gerado_em = cache.get('gerado_em', '')
    try:
        horario = datetime.fromisoformat(gerado_em).strftime('%d/%m %H:%M')
    except ValueError:
        horario = gerado_em
    return html.Div([
        dcc.Markdown(cache['texto'], style={'fontSize': '13px', 'lineHeight': '1.6'}),
        dmc.Text(f"Gerado em {horario} · {cache.get('modelo_usado', '')} · "
                 f"{cache.get('tokens_usados', 0)} tokens", size='xs', c='dimmed', mt='sm'),
    ])


def _bloco_mentor_ia() -> dmc.Card:
    """Mentor de IA — análise retrospectiva diária (item D, "upgrade
    diario.docx", 2026-07-30): curva de aprendizagem, evolução operacional/
    estratégica, próximos passos, tom DIRETO e objetivo. Reusa o MESMO hub
    Gemini/OpenRouter do chat (`ai_analyst._despachar_para_modelo`), nunca
    uma chamada própria. `dmc.Card`/`dmc.Button` reais (item F, Fase 2.6).

    Cadência de 1x/dia é só uma SUGESTÃO da UI (rótulo do botão muda pra
    "Gerar nova análise" depois da 1ª geração do dia) — nunca um bloqueio,
    porque o Jesué pode querer regenerar manualmente. O botão em si nunca é
    recriado por sua própria callback (evitaria resetar n_clicks) — só o
    `children` de `diario-mentor-ia-conteudo` e o rótulo do botão mudam."""
    cache = ai_analyst.carregar_analise_diaria()
    hoje = date.today().isoformat()
    ja_gerou_hoje = bool(cache) and cache.get('data') == hoje
    return dmc.Card([
        dmc.Group([
            dmc.ThemeIcon(_icone('tabler:compass', tamanho=18), color='yellow', variant='light', radius='xl', size='lg'),
            dmc.Text('Mentor de IA — Evolução Operacional', size='sm', fw=600),
            dmc.Button(
                'Gerar nova análise' if ja_gerou_hoje else 'Gerar análise de hoje',
                id='btn-gerar-mentor-ia', n_clicks=0, variant='light', color='gray',
                size='xs', radius='xl', ml='auto',
            ),
        ], mb='md'),
        dcc.Loading(
            id='loading-mentor-ia', type='dot', color=C['gold'],
            children=html.Div(_conteudo_mentor_ia(cache), id='diario-mentor-ia-conteudo'),
        ),
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mt='md', className='tz-card')


_ROTULO_EIXO_ANTICOVARDIA = {
    'win_rate': 'Taxa de Acerto',
    'profit_factor': 'Fator de Lucro',
    'avg_win_loss': 'Ganho/Perda Médio',
    'recovery_factor': 'Fator de Recuperação',
    'max_drawdown': 'Drawdown Máximo',
    'consistencia': 'Consistência',
}


_ORDEM_EIXOS_ANTICOVARDIA = ['win_rate', 'profit_factor', 'avg_win_loss',
                             'recovery_factor', 'max_drawdown', 'consistencia']


def _grafico_radar_anticovardia(eixos: dict, tema: str) -> go.Figure:
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    categorias = [_ROTULO_EIXO_ANTICOVARDIA[e] for e in _ORDEM_EIXOS_ANTICOVARDIA]
    valores = [eixos.get(e, 0.0) for e in _ORDEM_EIXOS_ANTICOVARDIA]
    r_fechado = valores + [valores[0]]
    theta_fechado = categorias + [categorias[0]]

    # A 1ª tentativa de "gradiente" (N cópias do mesmo polígono encolhidas
    # em direção ao centro) foi um erro: como os 6 eixos quase nunca têm o
    # MESMO valor, cada cópia é uma versão IRREGULAR do polígono, e cópias
    # concêntricas de um polígono irregular não formam faixas suaves — as
    # arestas de cada camada cruzam as das outras, criando o efeito
    # "estrela"/crivo de linhas visto no comparativo direto com o
    # TradeZella real (2026-07-31: "não é fluido e clean... cheio de
    # pontos"). O TradeZella usa preenchimento SÓLIDO de tom único, sem
    # gradiente de verdade — Scatterpolar também não suporta `fillgradient`
    # nativo (só o Scatter cartesiano, ver `_grafico_evolucao_saldo`), então
    # replicar o "clean" real é exatamente isto: uma única camada.
    fig = go.Figure(go.Scatterpolar(
        r=r_fechado, theta=theta_fechado, fill='toself',
        fillcolor=_hex_para_rgba(p['gold'], 0.35), line=dict(color=p['gold'], width=2),
        showlegend=False,
    ))
    fig.update_layout(
        # Mesmo cuidado de `_radar_roda_vida`: `autorange=False` + `tickvals`
        # explícitos, senão o Plotly quebra a escala com variância baixa.
        # `gridshape='linear'` (feedback: "deixe o gráfico quadrado/
        # hexagonal ao invés de redondo") troca o grid circular padrão por
        # um polígono reto nos mesmos 6 eixos. `showticklabels=False`
        # (2026-07-31, "não tem números no meio" no real) esconde os
        # números 0/20/40/.../100 que poluíam o centro do gráfico — o grid
        # em si continua visível, só o rótulo numérico some.
        polar=dict(bgcolor='rgba(0,0,0,0)', gridshape='linear',
                   radialaxis=dict(visible=True, range=[0, 100], autorange=False,
                                    tickvals=[0, 20, 40, 60, 80, 100], showticklabels=False,
                                    gridcolor=p['border']),
                   angularaxis=dict(tickfont=dict(size=9, color=p['text']), gridcolor=p['border'])),
        paper_bgcolor='rgba(0,0,0,0)', showlegend=False, margin=dict(l=30, r=30, t=20, b=20),
        height=280, autosize=True,
    )
    return fig


def _barra_anticovardia_score(score: float, tema: str = 'light') -> html.Div:
    """Barra de gradiente 0-100 (vermelho→amarelo→verde) com marcador na
    posição do score — o TradeZella mostra o Zella Score como BARRA
    horizontal degradê, não anel (2026-07-31, feedback: "O Anticovardia
    Score é em barra, e não em círculo"). Sem componente DMC pronto pra
    isso (`RingProgress` é círculo; `ProgressRoot` não faz gradiente
    contínuo nem marcador de posição livre) — construído com CSS puro
    (`linear-gradient` + marcador posicionado via `left: calc(pct% - r)`),
    mesmo padrão de outros widgets sem equivalente pronto no projeto."""
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    pct = max(0.0, min(score, 100.0))
    cor_marcador = p['red'] if score < 33 else (p['yellow'] if score < 66 else p['green'])
    return html.Div([
        html.Div(html.Div(style={
            'position': 'absolute', 'left': f'calc({pct}% - 8px)', 'top': '-4px',
            'width': '16px', 'height': '16px', 'borderRadius': '50%',
            'background': cor_marcador, 'border': f"2px solid {p['panel']}",
            'boxShadow': '0 1px 3px rgba(0,0,0,0.35)',
        }), style={
            'position': 'relative', 'height': '8px', 'borderRadius': '4px',
            'background': f"linear-gradient(to right, {p['red']}, {p['yellow']}, {p['green']})",
        }),
        html.Div([
            html.Span(str(v), style={'fontSize': '10px', 'color': p['text2'], 'fontFamily': FONTS['body']})
            for v in (0, 20, 40, 60, 80, 100)
        ], style={'display': 'flex', 'justifyContent': 'space-between', 'marginTop': '6px'}),
    ], style={'width': '100%', 'padding': '8px 4px 0 4px'})


def _bloco_anticovardia_score(tema: str = 'light') -> dmc.Card:
    """Anticovardia Score — `dmc.Card` real com o radar Plotly (sem
    equivalente DMC pronto, mantido) + a barra degradê
    `_barra_anticovardia_score` pro score final (item F — TradeZella usa
    barra, não anel/círculo, ver docstring da barra)."""
    resultado = dt.calcular_anticovardia_score()
    if resultado['score'] is None:
        return dmc.Card('Sem trades reais suficientes pra calcular o Anticovardia Score ainda.',
                         withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', w='100%',
                         style={'fontSize': '12px', 'color': C['text2']})
    score = resultado['score']
    return dmc.Card([
        dmc.Text('Anticovardia Score', size='sm', fw=600, mb='sm'),
        dcc.Graph(figure=_grafico_radar_anticovardia(resultado['eixos'], tema),
                  config={'displayModeBar': False, 'responsive': True}, style={'width': '100%'}),
        _barra_anticovardia_score(score, tema),
        dmc.Group([
            dmc.Text(f"Seu Anticovardia Score: {score:.1f}", size='sm', fw=700,
                      className='tz-numero-animado'),
        ], justify='flex-start', mt='sm'),
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', className='tz-card', w='100%')


_ROTULO_EIXO_DISCIPLINA = {
    'checklist_pretrade': 'Checklist Pré-Mercado',
    'checklist_estrategia': 'Checklist da Estratégia',
    'risco': 'Gerenciamento de Risco',
    'coaching_checkin': 'Check-in de Coaching',
}


def _bloco_discipline_score(tema: str = 'light') -> dmc.Card:
    """Pontos de Disciplina (Playbook, 2026-08-02) — complemento (NÃO
    substituto) do Anticovardia Score: aquele mede resultado financeiro,
    este mede aderência ao PROCESSO (checklist pré-mercado, checklist da
    estratégia, gerenciamento de risco, check-in de coaching). Mesma fonte
    (`dt.calcular_discipline_score`) usada aqui e em Bem-Estar — visual mais
    simples que o radar do Anticovardia (4 barras `dmc.Progress`, não há
    referência visual do TradeZella pra essa métrica nova)."""
    resultado = dt.calcular_discipline_score()
    if resultado['score'] is None:
        return dmc.Card('Sem trades reais suficientes pra calcular os Pontos de Disciplina ainda.',
                         withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', w='100%',
                         className='tz-card', style={'fontSize': '12px', 'color': C['text2']})
    barras = []
    for chave, rotulo in _ROTULO_EIXO_DISCIPLINA.items():
        valor = resultado['eixos'].get(chave)
        barras.append(dmc.Stack([
            dmc.Group([
                dmc.Text(rotulo, size='xs', c=C['text2']),
                dmc.Text(f"{valor:.0f}" if valor is not None else '—', size='xs', fw=700),
            ], justify='space-between'),
            dmc.Progress(value=valor or 0, color='yellow', size='sm', radius='xl'),
        ], gap=4, mb='xs'))
    return dmc.Card([
        dmc.Text('Pontos de Disciplina', size='sm', fw=600, mb='sm'),
        html.Div(barras),
        dmc.Group([
            dmc.Text(f"{resultado['score']:.1f}", size='sm', fw=700, className='tz-numero-animado'),
        ], justify='flex-start', mt='sm'),
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', className='tz-card', w='100%')


def _bloco_trades_recentes(tema: str = 'light') -> dmc.Card:
    """Trades Recentes (item F — bloco condensado que faltava, 2026-07-31:
    "você não está mostrando o bloco de trades recentes no dashboard,
    como é no TradeZella"). Lista os últimos `dt.trades_recentes` trades
    reais num formato compacto (data/ativo/direção/estratégia/R-múltiplo/
    resultado) — prévia rápida, não substitui o Histórico (que tem
    filtro completo + edição)."""
    linhas = dt.trades_recentes(n=8)
    if not linhas:
        return dmc.Card('Nenhum trade real lançado ainda.', withBorder=True, shadow='sm',
                         radius='lg', p='lg', mb='md', w='100%', className='tz-card',
                         style={'fontSize': '12px', 'color': C['text2']})
    nomes_estrategias = _mapa_nomes_estrategias()

    def _celula(conteudo, largura, alinhar='left'):
        return html.Div(conteudo, style={'width': largura, 'textAlign': alinhar, 'flexShrink': '0'})

    cabecalho = dmc.Group([
        _celula(dmc.Text('Data', size='xs', c='dimmed'), '70px'),
        _celula(dmc.Text('Ativo', size='xs', c='dimmed'), '55px'),
        _celula(dmc.Text('Direção', size='xs', c='dimmed'), '65px'),
        dmc.Text('Estratégia', size='xs', c='dimmed', style={'flex': '1'}),
        _celula(dmc.Text('R-Múltiplo', size='xs', c='dimmed'), '80px', 'right'),
        _celula(dmc.Text('Resultado', size='xs', c='dimmed'), '90px', 'right'),
    ], wrap='nowrap', gap='xs', style={'padding': '0 4px 6px 4px'})

    linhas_ui = [cabecalho]
    for t in linhas:
        saldo = t['saldo_operacao_reais']
        cor_saldo = 'green' if saldo > 0 else ('red' if saldo < 0 else 'dimmed')
        r_mult = t['r_multiple']
        direcao_label = _ROTULO_DIRECAO_OPERACAO.get(t['tipo_operacao'], t['tipo_operacao'])
        cor_direcao = 'blue' if t['tipo_operacao'] == 'long' else 'grape'
        nome_estrategia = nomes_estrategias.get(t['estrategia'], t['estrategia']) if t['estrategia'] else '—'
        linhas_ui.append(dmc.Group([
            _celula(dmc.Text(t['data_op'], size='xs'), '70px'),
            _celula(dmc.Text(t['ativo'], size='xs', fw=600), '55px'),
            _celula(dmc.Badge(direcao_label, color=cor_direcao, variant='light', size='sm'), '65px'),
            dmc.Text(nome_estrategia, size='xs', c='dimmed', truncate=True, style={'flex': '1'}),
            _celula(dmc.Text(f"{r_mult:+.2f}R" if r_mult is not None else '—', size='xs'), '80px', 'right'),
            _celula(dmc.Text(f"R$ {saldo:+.2f}", size='xs', fw=700, c=cor_saldo), '90px', 'right'),
        ], wrap='nowrap', gap='xs', style={'padding': '6px 4px', 'borderTop': f"1px solid {C['border']}"}))

    return dmc.Card([
        dmc.Text('Trades Recentes', size='sm', fw=600, mb='sm'),
        html.Div(linhas_ui),
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', className='tz-card', w='100%')


def _grafico_duracao_performance(tema: str = 'light') -> go.Figure:
    """Scatter Duração × Resultado (item F, fase 5 — "Trade Duration
    Performance" do TradeZella). Um ponto por trade real com os dois
    timestamps preenchidos (`dt.dados_duracao_performance` — boa parte da
    base não tem, amostra parcial por natureza), cor por sinal do saldo
    (verde ganho/vermelho perda), eixo X em minutos, eixo Y em R$."""
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    linhas = dt.dados_duracao_performance()
    fig = go.Figure()
    if linhas:
        duracoes = [t['duracao_minutos'] for t in linhas]
        saldos = [t['saldo_operacao_reais'] for t in linhas]
        cores = [p['green'] if s > 0 else (p['red'] if s < 0 else p['text2']) for s in saldos]
        textos = [f"{t['ativo']} · {_ROTULO_DIRECAO_OPERACAO.get(t['tipo_operacao'], t['tipo_operacao'])} · {t['data_op']}"
                  for t in linhas]
        fig.add_trace(go.Scatter(
            x=duracoes, y=saldos, mode='markers',
            marker=dict(color=cores, size=8, opacity=0.75, line=dict(width=1, color=p['panel'])),
            text=textos, hovertemplate='%{text}<br>Duração: %{x:.0f} min<br>Resultado: R$ %{y:.2f}<extra></extra>',
        ))
        fig.add_hline(y=0, line_width=1, line_color=p['border'])
    fig.update_layout(
        paper_bgcolor=p['panel'], plot_bgcolor=p['panel'],
        font=dict(family=FONTS['body'], color=p['text2'], size=10),
        margin=dict(l=45, r=10, t=10, b=35), height=260,
        xaxis=dict(showgrid=True, gridcolor=p['border'], gridwidth=0.5, title='Duração (minutos)'),
        yaxis=dict(showgrid=True, gridcolor=p['border'], gridwidth=0.5, title='R$', zeroline=False),
    )
    return fig


def _bloco_o_que_fazer(descricao: str, resultado: dict) -> html.Div:
    """"O que fazer com isso" (2026-07-31, pedido: "coloque um feedback
    dinâmico e uma descrição... o nosso famoso 'o que fazer com isso'") —
    mesmo padrão já estabelecido em `dashboard.py::_balao_big_players` pros
    velocímetros do hero: descrição FIXA do que o gráfico mostra +
    veredito DINÂMICO calculado do dado real (`resultado`, de
    `dt.feedback_progress_tracker`/`dt.feedback_duracao_performance`), não
    um texto genérico igual sempre."""
    if not resultado.get('tem_dado'):
        corpo = html.Div(resultado.get('mensagem', 'Sem dado suficiente ainda.'),
                          style={'fontSize': '11px', 'color': C['text2'], 'lineHeight': '1.5'})
    else:
        corpo = html.Div([
            html.Span('O que fazer: ', style={'fontWeight': '700', 'color': C['text']}),
            html.Span(resultado['acao_texto'], style={'color': C.get(resultado.get('cor_acao'), C['text2'])}),
        ], style={'fontSize': '11px', 'lineHeight': '1.5'})
    return html.Div([
        html.Div(descricao, style={'fontSize': '11px', 'color': C['text2'], 'lineHeight': '1.5', 'marginBottom': '6px'}),
        corpo,
    ], style={'marginTop': '10px', 'paddingTop': '10px', 'borderTop': f"1px solid {C['border']}",
              'fontFamily': FONTS['body']})


def _bloco_duracao_performance(tema: str = 'light') -> dmc.Card:
    """Card "Desempenho por Duração da Operação" (item F, fase 5) — só
    aparece com conteúdo real; card informativo quando não há amostra
    (a maioria dos trades antigos não tem os 2 timestamps)."""
    linhas = dt.dados_duracao_performance()
    if not linhas:
        return dmc.Card(
            'Sem trades reais com horário de entrada/saída registrado ainda '
            '(precisa dos dois timestamps preenchidos).',
            withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', w='100%',
            className='tz-card', style={'fontSize': '12px', 'color': C['text2']})
    return dmc.Card([
        dmc.Text('Desempenho por Duração da Operação', size='sm', fw=600, mb='sm'),
        dcc.Graph(figure=_grafico_duracao_performance(tema),
                  config={'displayModeBar': False, 'responsive': True}, style={'width': '100%'}),
        _bloco_o_que_fazer(
            'Cada ponto é um trade real (só os com horário de entrada e saída '
            'registrados): duração em minutos no eixo X, resultado em R$ no eixo Y.',
            dt.feedback_duracao_performance(linhas)),
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', className='tz-card', w='100%')


def _bloco_analise_estrategia(stats_geral: dict, tema: str = 'light') -> html.Div:
    """Painel "Análise de Estratégia" — pedido do docx: "junte os blocos Por
    Ativo, Por Janela, Por Sala/Análise e Por Tags Setup/Erro num painel de
    dashboard estatístico, adicionando melhor estratégia por horário/
    direção/ativo/categoria; destaque a Estratégia Vencedora (troféu/
    medalha) e a Perdedora". O Mentor de IA (item D) também vive aqui dentro
    — pedido explícito do docx: "ainda dentro da Análise de Estratégias
    crie um bloco [do Mentor]". O Anticovardia Score (item F, fase 3,
    redesenho TradeZella) NÃO mora mais aqui dentro — migrou pra coluna
    esquerda do grid assimétrico em `_construir_diario_estatisticas` (Fase
    2.6: composição real de página, não pilha vertical única — ver
    `spec_redesenho_tradezella.md`)."""
    analise = dt.analise_estrategia()
    rotulos_janela = {r['slug']: _formatar_janela(r['slug'])
                       for _, r in dt.listar_janelas(somente_ativas=False).iterrows()}
    # `dmc.SimpleGrid` em vez de `html.Div(display:flex)` cru (2026-07-31,
    # feedback do Jesué: "não gosto desses espaços vazios, organize melhor
    # os elementos") — SimpleGrid divide a linha em células IGUAIS de
    # verdade; um `dmc.Card` dentro de flexbox não estica sozinho e deixava
    # metade da linha em branco quando o card vizinho era mais estreito.
    #
    # Detalhes (Melhor por X + Por Ativo/Janela/Sala/Tags) recolhidos por
    # padrão (2026-07-31, feedback: "deixe Vencedora/Perdedora e Mentor de
    # IA visíveis, o resto contraído, clicando numa seta") — só Vencedora/
    # Perdedora e Mentor de IA ficam sempre visíveis. `dmc.Collapse`
    # testado primeiro e DESCARTADO: reproduzido em isolamento (app Dash
    # mínimo, só um botão + Collapse) que o `opened` setado por callback
    # NUNCA sai de `height:0/display:none` — bug real da combinação DMC/
    # Dash deste projeto, não erro de uso. Troca pro padrão já PROVADO
    # neste arquivo (`alternar_modo_ano`/`btn-diario-cal-config`): conteúdo
    # sempre presente no DOM, só o `style` (`display`) alterna — nenhuma
    # medição de altura envolvida, não pode falhar do mesmo jeito. Botão
    # estático (nunca recriado pela própria callback): o `children` do
    # botão guarda o estado (seta ▼/▲).
    detalhes = html.Div([
        dmc.SimpleGrid([
            _tabela_melhor_por('Melhor por Horário', analise['melhor_por_janela'], rotulos_janela),
            _tabela_melhor_por('Melhor por Direção', analise['melhor_por_direcao'], _ROTULO_DIRECAO_OPERACAO),
        ], cols={'base': 1, 'md': 2}, spacing='md', mb='md'),
        dmc.SimpleGrid([
            _tabela_melhor_por('Melhor por Ativo', analise['melhor_por_ativo']),
            _tabela_melhor_por('Melhor por Categoria', analise['melhor_por_categoria'],
                               dt.ROTULO_CATEGORIA_ESTRATEGIA),
        ], cols={'base': 1, 'md': 2}, spacing='md', mb='md'),
        dmc.SimpleGrid([
            _bloco_breakdown_diario('Por Ativo', stats_geral['por_ativo']),
            _bloco_breakdown_diario('Por Janela', stats_geral['por_janela']),
        ], cols={'base': 1, 'md': 2}, spacing='md', mb='md'),
        dmc.SimpleGrid([
            _bloco_breakdown_diario('Por Sala/Análise', stats_geral['por_sala']),
            _bloco_breakdown_diario('Por Tags Setup/Erro', stats_geral['por_tags_setup_erro']),
        ], cols={'base': 1, 'md': 2}, spacing='md', mb='md'),
    ], id='colapso-analise-estrategia-detalhes', style={'display': 'none'})
    return html.Div([
        dmc.SimpleGrid([
            _card_estrategia_destaque('Estratégia Vencedora', 'tabler:trophy', analise['vencedora'], 'green'),
            _card_estrategia_destaque('Estratégia Perdedora', 'tabler:trending-down', analise['perdedora'], 'red'),
        ], cols={'base': 1, 'md': 2}, spacing='md', mb='md'),
        dmc.Button('Ver detalhes', id='btn-analise-estrategia-detalhes', n_clicks=0,
                   variant='subtle', color='gray', size='xs', mb='sm'),
        detalhes,
        _bloco_mentor_ia(),
    ])


def _hex_para_rgba(hex_cor: str, alpha: float) -> str:
    """Converte '#RRGGBB' pra 'rgba(r,g,b,a)' — Plotly só aceita opacidade
    via rgba(), não hex separado. Usado pelo fill translúcido dos gráficos
    abaixo, que precisam de PALETA_GAUGE (hex literal) por tema."""
    h = hex_cor.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f'rgba({r},{g},{b},{alpha})'


def _grafico_evolucao_saldo(stats: dict, tema: str = 'light', moeda: str = 'BRL') -> go.Figure:
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    fig = go.Figure()
    if stats['evolucao_data']:
        cor_por_sinal = {'pos': p['green'], 'neg': p['red']}
        for sinal, xs_seg, ys_seg in metricas.segmentos_cor_por_sinal(
                stats['evolucao_data'], stats['evolucao_saldo_acumulado']):
            cor = cor_por_sinal[sinal]
            extremo = max(ys_seg) if sinal == 'pos' else min(ys_seg)
            fig.add_trace(go.Scatter(
                x=xs_seg, y=ys_seg, mode='lines', line=dict(color=cor, width=2, shape='spline', smoothing=0.3),
                # Gradiente vertical de verdade (`fillgradient`, Plotly
                # nativo — 2026-07-31, feedback "aplique o gradiente em
                # todos os gráficos do dashboard"): transparente na linha
                # de zero, cor sólida no pico/vale do segmento — igual ao
                # "mountain chart" real do TradeZella (verde sólido no
                # topo esmaecendo pra branco perto do eixo).
                fill='tozeroy', fillcolor=_hex_para_rgba(cor, 0.15),
                fillgradient=dict(type='vertical', start=0, stop=extremo,
                                   colorscale=[[0, _hex_para_rgba(cor, 0.0)], [1, _hex_para_rgba(cor, 0.45)]]),
                showlegend=False, hoverinfo='x+y',
            ))
        fig.add_hline(y=0, line_width=1, line_color=p['border'])
    fig.update_layout(
        paper_bgcolor=p['panel'], plot_bgcolor=p['panel'],
        font=dict(family=FONTS['body'], color=p['text2'], size=10),
        margin=dict(l=45, r=10, t=10, b=30), height=200,
        xaxis=dict(showgrid=False, gridcolor=p['border']),
        yaxis=dict(showgrid=True, gridcolor=p['border'], gridwidth=0.5,
                   title=f'{metricas.simbolo_moeda(moeda)} acumulado', zeroline=False),
        hovermode='x unified',
    )
    return fig


_NOMES_MES = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
              'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']


_DIAS_SEMANA = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']


_OPCOES_STATS_CALENDARIO = [
    ('r_multiple', 'R-Multiple'),
    ('daily_pl', 'Daily P/L'),
    ('pontos', 'Pontos'),
    ('n_trades', 'Nº de Trades'),
    ('winrate', 'Day Winrate'),
]


_STATS_CALENDARIO_PADRAO = ['r_multiple', 'daily_pl', 'n_trades', 'winrate']


def _grade_calendario(por_dia: dict, detalhe_dias: dict, ano: int, mes: int,
                       stats_visiveis: list | None = None, tema: str = 'light', moeda: str = 'BRL') -> html.Div:
    """Grade mensal por dia — `html.Div` customizado (mesma razão de
    `diario_app_98.py::_grade_calendario_98`: sem precedente de Plotly
    heatmap no projeto). `detalhe_dias` (`dt.detalhe_dias_calendario`)
    fornece R-multiple médio/Nº trades/Day Winrate/Pontos por dia — ver
    `stats_visiveis` (lista de chaves de `_OPCOES_STATS_CALENDARIO`)
    controlando quais dessas linhas extras aparecem em cada célula. Uma
    coluna extra por linha de semana soma o saldo + conta os dias operados
    daquela semana (item F, fase 4: "total semanal na lateral").

    Cor de célula depende do TEMA (item F, redesenho visual TradeZella,
    conferido nas 3 referências reais do docx): no tema claro
    (`image1.png`) o TradeZella usa fundo PASTEL + texto escuro; no tema
    escuro (`image3.png`) usa fundo saturado + texto claro (já bem
    parecido com o que este calendário sempre teve). Por isso só o tema
    claro troca de paleta aqui — `TZ_PASTEL_CALENDARIO`."""
    stats_visiveis = stats_visiveis if stats_visiveis is not None else _STATS_CALENDARIO_PADRAO
    pastel = TZ_PASTEL_CALENDARIO.get(tema)
    cal = calendar.Calendar(firstweekday=6)  # domingo primeiro
    semanas = cal.monthdayscalendar(ano, mes)

    cabecalho = html.Div([
        html.Div(d, style={'flex': '1', 'textAlign': 'center', 'fontSize': '10px',
                            'color': C['text2'], 'fontFamily': FONTS['body']})
        for d in _DIAS_SEMANA
    ] + [html.Div('Semana', style={'width': '90px', 'textAlign': 'center', 'fontSize': '10px',
                                     'color': C['text2'], 'fontFamily': FONTS['body']})],
        style={'display': 'flex', 'marginBottom': '4px'})

    linhas = [cabecalho]
    for semana in semanas:
        celulas = []
        saldo_semana = 0.0
        dias_operados_semana = 0
        for dia in semana:
            if dia == 0:
                celulas.append(html.Div(style={'flex': '1', 'minHeight': '40px', 'margin': '2px'}))
                continue
            data_iso = f"{ano:04d}-{mes:02d}-{dia:02d}"
            info = por_dia.get(data_iso)
            detalhe = detalhe_dias.get(data_iso)
            if info is None:
                bg, cor_texto = C['bg'], C['text2']
            elif info['saldo'] > 0:
                bg = pastel['green_bg'] if pastel else C['green']
                cor_texto = pastel['texto'] if pastel else C['bg']
                saldo_semana += info['saldo']
                dias_operados_semana += 1
            elif info['saldo'] < 0:
                bg = pastel['red_bg'] if pastel else C['red']
                cor_texto = pastel['texto'] if pastel else C['bg']
                saldo_semana += info['saldo']
                dias_operados_semana += 1
            else:
                bg = pastel['neutro_bg'] if pastel else C['border']
                cor_texto = pastel['texto'] if pastel else C['text2']
                dias_operados_semana += 1
            estilo_celula = {'background': bg, 'borderRadius': TZ_RAIO_CELULA,
                              'flex': '1', 'minHeight': '40px', 'margin': '2px', 'padding': '4px',
                              'color': cor_texto, 'textAlign': 'center'}
            conteudo = [html.Div(str(dia), style={'fontSize': '11px'})]
            if info is not None and 'daily_pl' in stats_visiveis:
                conteudo.append(html.Div(metricas.formatar_moeda(info['saldo'], moeda, casas=0),
                                          style={'fontSize': '10px', 'fontFamily': FONTS['body'], 'fontWeight': '700'}))
            if info is not None and detalhe is not None:
                estilo_extra = {'fontSize': '9px', 'fontFamily': FONTS['body'], 'opacity': '0.85'}
                if 'n_trades' in stats_visiveis:
                    conteudo.append(html.Div(f"{detalhe['n']} trade(s)", style=estilo_extra))
                # R-Multiple e Day Winrate ficam NA MESMA LINHA (conferido em
                # `image2.png`: "-1.14R, 33.33%" é um texto só, não 2 linhas).
                partes_linha = []
                if 'r_multiple' in stats_visiveis:
                    rm = detalhe['r_multiple_medio']
                    partes_linha.append(f"{rm:+.2f}R" if rm is not None else '—R')
                if 'winrate' in stats_visiveis:
                    partes_linha.append(f"{detalhe['taxa_acerto'] * 100:.1f}%")
                if partes_linha:
                    conteudo.append(html.Div(', '.join(partes_linha), style=estilo_extra))
                if 'pontos' in stats_visiveis:
                    conteudo.append(html.Div(f"{detalhe['pontos_total']:+.0f}pts", style=estilo_extra))
            if info is not None:
                # Clicável só quando há operação no dia — navega pro
                # Histórico já filtrado (ver `clicar_dia_calendario`).
                celulas.append(html.Div(conteudo, id={'type': 'diario-cal-dia', 'index': data_iso},
                                         n_clicks=0, style={**estilo_celula, 'cursor': 'pointer'}))
            else:
                celulas.append(html.Div(conteudo, style=estilo_celula))
        cor_semana = (C['green'] if saldo_semana > 0 else (C['red'] if saldo_semana < 0 else C['text2']))
        celulas.append(html.Div([
            html.Div(metricas.formatar_moeda(saldo_semana, moeda, casas=0),
                      style={'fontSize': '11px', 'fontWeight': '700',
                             'fontFamily': FONTS['body'], 'color': cor_semana}),
            html.Div(f"{dias_operados_semana} dia(s)", style={'fontSize': '9px', 'color': C['text2']}),
        ], style={'width': '90px', 'minHeight': '40px', 'margin': '2px', 'padding': '4px',
                  'textAlign': 'center', 'background': C['panel'], 'borderRadius': TZ_RAIO_CELULA,
                  'boxShadow': TZ_SOMBRA_CARD}))
        linhas.append(html.Div(celulas, style={'display': 'flex'}))
    return html.Div(linhas)


def _conteudo_config_calendario(stats_visiveis: list) -> html.Div:
    """Painel "Display stats" (item F, fase 4) — mesmo padrão de
    `_conteudo_checklist_pre_trade` (Checklist de verdade, marcável), só
    que este PERSISTE a seleção (grava em `store-calendario-stats-
    visiveis` via `atualizar_stats_visiveis_calendario`), ao contrário do
    checklist pré-trade que é ritual efêmero de propósito."""
    return html.Div([
        html.Div('Stats visíveis por dia', style={'fontSize': '12px', 'fontWeight': '600',
                                                    'fontFamily': FONTS['body'],
                                                    'color': C['text'], 'marginBottom': '8px'}),
        dcc.Checklist(
            id='diario-cal-config-itens',
            options=[{'label': rotulo, 'value': chave} for chave, rotulo in _OPCOES_STATS_CALENDARIO],
            value=stats_visiveis, labelStyle={'display': 'inline-block', 'marginRight': '16px', 'fontSize': '12px',
                                               'fontFamily': FONTS['body']},
            inputStyle={'marginRight': '6px'},
        ),
    ], style=_tz_estilo_card({'marginBottom': '8px', 'padding': '14px 16px'}))


def _grade_calendario_anual(por_mes: dict, ano: int, tema: str = 'light', moeda: str = 'BRL') -> html.Div:
    """Grade 4x3 (um trimestre por linha) com o saldo agregado de cada mês
    do ano — usada quando o toggle "Ano Corrente" está ligado. Mesma
    paleta pastel-no-claro/saturado-no-escuro de `_grade_calendario`."""
    pastel = TZ_PASTEL_CALENDARIO.get(tema)
    linhas = []
    for trimestre in range(4):
        celulas = []
        for i in range(3):
            mes_num = trimestre * 3 + i + 1
            info = por_mes.get(f"{ano:04d}-{mes_num:02d}")
            if info is None:
                bg, cor_texto = C['bg'], C['text2']
            elif info['saldo'] > 0:
                bg = pastel['green_bg'] if pastel else C['green']
                cor_texto = pastel['texto'] if pastel else C['bg']
            elif info['saldo'] < 0:
                bg = pastel['red_bg'] if pastel else C['red']
                cor_texto = pastel['texto'] if pastel else C['bg']
            else:
                bg = pastel['neutro_bg'] if pastel else C['border']
                cor_texto = pastel['texto'] if pastel else C['text2']
            celulas.append(html.Div([
                html.Div(_NOMES_MES[mes_num][:3], style={'fontSize': '11px', 'fontWeight': '700'}),
                html.Div(metricas.formatar_moeda(info['saldo'], moeda, casas=0) if info else '—',
                          style={'fontSize': '10px', 'fontFamily': FONTS['body']}),
                html.Div(f"{info['n']} op." if info else '', style={'fontSize': '9px'}),
            ], style={'background': bg, 'borderRadius': TZ_RAIO_CELULA,
                       'flex': '1', 'minHeight': '52px', 'margin': '2px', 'padding': '6px',
                       'color': cor_texto, 'textAlign': 'center'}))
        linhas.append(html.Div(celulas, style={'display': 'flex'}))
    return html.Div(linhas)


def _ranking_confiabilidade_compacto(scores: dict, saldos_por_estrategia: dict, n: int = 3) -> html.Div:
    """Resumo "Confiabilidade por Estratégia" pro Dashboard (2026-08-05,
    item 18 do upgrade "upgrade geral parte 1.docx": "traga o bloco pro
    módulo Estratégias, deixe no Dashboard um ranking das 3 melhores e 3
    piores estratégias por Score — deixe um botão 'ver mais' que leve
    para a tabela completa no módulo Estratégias"). A tabela completa/
    interativa (grid + detalhe expandido por linha) migrou pra
    `diario_app_classico.py::_construir_diario_estrategias` — aqui fica
    só o resumo + navegação (`navegar_para_estrategias`)."""
    nomes = _mapa_nomes_estrategias()
    elegiveis = [(tag, v) for tag, v in scores.items() if v['amostra_suficiente'] and v['score'] is not None]
    if not elegiveis:
        return html.Div('Amostra insuficiente ainda em qualquer estratégia pra entrar no ranking.',
                        style={'fontSize': '12px', 'color': C['text2']})
    ordenado = sorted(elegiveis, key=lambda item: item[1]['score'], reverse=True)
    melhores = ordenado[:n]
    piores = list(reversed(ordenado[-n:])) if len(ordenado) > n else []

    def _linha_ranking(tag, v, cor_score):
        saldo = saldos_por_estrategia.get(tag, {}).get('saldo')
        cor_saldo = C['green'] if (saldo or 0) > 0 else (C['red'] if (saldo or 0) < 0 else C['text2'])
        return html.Div([
            html.Span(nomes.get(tag, tag.replace('_', ' ').title()), style={'flex': '1', 'fontSize': '12px', 'fontWeight': '600'}),
            html.Span(f"R$ {saldo:+.0f}" if saldo is not None else '—', style={
                'width': '90px', 'textAlign': 'right', 'fontSize': '12px', 'fontFamily': FONTS['body'], 'color': cor_saldo}),
            html.Span(f"{v['score']:.0f}", style={
                'width': '46px', 'textAlign': 'right', 'fontSize': '12px', 'fontWeight': '700',
                'fontFamily': FONTS['body'], 'color': cor_score}),
        ], style={'display': 'flex', 'padding': '4px 0', 'borderBottom': f'1px solid {C["border"]}'})

    colunas = [
        html.Div([
            html.Div('Melhores por Score', style={'fontSize': '11px', 'color': C['text2'], 'fontWeight': '600', 'marginBottom': '4px'}),
            *[_linha_ranking(tag, v, C['green']) for tag, v in melhores],
        ], style={'flex': '1', 'minWidth': '220px'}),
    ]
    if piores:
        colunas.append(html.Div([
            html.Div('Piores por Score', style={'fontSize': '11px', 'color': C['text2'], 'fontWeight': '600', 'marginBottom': '4px'}),
            *[_linha_ranking(tag, v, C['red']) for tag, v in piores],
        ], style={'flex': '1', 'minWidth': '220px'}))

    return html.Div([
        html.Div(colunas, style={'display': 'flex', 'gap': '20px', 'flexWrap': 'wrap', 'marginBottom': '10px'}),
        dmc.Button('Ver tabela completa', id='btn-ver-mais-confiabilidade', n_clicks=0,
                   variant='subtle', size='xs', rightSection=DashIconify(icon='tabler:arrow-right', width=14)),
    ])


def _grupo_cards_estatistica(titulo: str, cards: list, crescer: bool = False) -> html.Div:
    """Um grupo rotulado de cards (2026-07-30, feedback: "está meio bagunçado,
    aleatório. Agrupe as quantidades de operações, taxas e valores em blocos
    diferentes") — antes os 15 cards eram uma única fileira sem nenhuma
    ordem lógica visível (contagem, R$ e % intercalados). Agora cada grupo
    tem seu próprio rótulo e uma borda mais sutil que a dos cards
    individuais, só pra indicar "isto pertence junto".

    `crescer=True` (2026-07-31, feedback: "redimensionar os blocos pra
    tirar os espaços vazios" — o grupo "Taxas" com só 2 cards ficava bem
    mais estreito que "Quantidade" ao lado, sobrando um vão vazio até a
    borda direita do container) — faz o grupo crescer (`flex: 1`) pra
    preencher o espaço que sobra na linha, e os cards dentro dele (já
    `flex: 1` em `_card_estatistica`) se espalham por essa largura extra
    em vez de ficarem colados à esquerda."""
    return html.Div([
        html.Div(titulo, style={'fontSize': '12px', 'color': C['text2'], 'fontFamily': FONTS['body'],
                                 'fontWeight': '600', 'marginBottom': '10px'}),
        html.Div(cards, style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
    ], style={'padding': '12px', 'borderRadius': TZ_RAIO_CARD, 'background': 'rgba(128,128,128,0.05)',
              **({'flex': '1', 'minWidth': '240px'} if crescer else {})})


def _cards_detalhe_estatisticas(stats: dict, tema: str = 'light',
                                  ano: int | None = None, mes: int | None = None,
                                  ativo: str | None = None) -> html.Div:
    """Grupos Quantidade/Taxas/Valores (nome antigo: `_cards_estatisticas`) —
    demovidos pra bloco secundário "Detalhes" quando o header estilo
    TradeZella (`_cards_header_tradezella`) entrou (item F, fase 2,
    2026-07-30). Nenhum dado foi descartado: Wins/Losses/Breakeven/
    Sequências/Max Gain-Loss/Drawdown/Ganho-Perda Total continuam aqui,
    só não fazem mais parte do header principal.

    `ano`/`mes` (2026-07-31) seguem o MESMO recorte de período já usado
    em `stats` (calendário) — passados adiante pra
    `dt.evolucao_metricas_expandida` pra sparkline e número grande sempre
    falarem do mesmo período."""
    moeda = stats.get('moeda', 'BRL')
    evolucao = dt.evolucao_metricas_expandida(ano=ano, mes=mes, ativo=ativo)
    payoff_str = f"{stats['payoff']:.2f}" if stats['payoff'] is not None else '—'
    grupo_quantidade = _grupo_cards_estatistica('Quantidade', [
        _card_estatistica('Operações', str(stats['num_operacoes'])),
        _card_estatistica('Wins', str(stats['n_win']), cor=C['green']),
        _card_estatistica('Losses', str(stats['n_loss']), cor=C['red']),
        _card_estatistica('Breakeven', str(stats['n_breakeven'])),
        _card_estatistica('Seq. Wins', str(stats['maior_sequencia_win'])),
        _card_estatistica('Seq. Losses', str(stats['maior_sequencia_loss'])),
    ])
    # Taxa de Acerto já aparece no card do header (`_cards_header_tradezella`)
    # — removida daqui pra não duplicar (2026-07-31, feedback do Jesué).
    # Payoff/Fator de Recuperação ganham sparkline + variação % (2026-07-31,
    # pedido: "esse mini gráfico, e a setinha... adicione nas taxas").
    fator_recuperacao = stats['fator_recuperacao']
    grupo_taxas = _grupo_cards_estatistica('Taxas', [
        _card_com_sparkline('Payoff', payoff_str, evolucao['payoff'], tema),
        _card_com_sparkline('Fator de Recuperação',
                             f"{fator_recuperacao:.2f}" if fator_recuperacao is not None else '—',
                             evolucao['fator_recuperacao'], tema,
                             cor_valor=('green' if (fator_recuperacao or 0) >= 1
                                        else ('red' if fator_recuperacao is not None else None))),
    ], crescer=True)
    # Saldo Total removido (2026-07-31, feedback: "remova o Saldo Total dos
    # Detalhes - é a mesma informação repetida" — já é o card "Saldo
    # Líquido" do header). Expectativa também sai daqui: vira card
    # PRÓPRIO em destaque logo abaixo (mesmo feedback: "quero dar mais
    # destaque para a expectativa, é um dos principais indicadores").
    grupo_valores = _grupo_cards_estatistica(f'Valores ({metricas.simbolo_moeda(moeda)})', [
        # Ganho Total/Perda Total (2026-07-30, feedback: "está faltando") —
        # soma de todos os vencedores/perdedores, ao lado de Max Gain/Max Loss
        # (melhor/pior trade ÚNICO) pra não confundir os dois.
        _card_estatistica('Ganho Total', metricas.formatar_moeda(stats['ganho_total'], moeda), cor=C['green']),
        _card_estatistica('Perda Total', metricas.formatar_moeda(stats['perda_total'], moeda), cor=C['red']),
        _card_estatistica('Max Gain', metricas.formatar_moeda(stats['max_gain'], moeda), cor=C['green']),
        _card_estatistica('Max Loss', metricas.formatar_moeda(stats['max_loss'], moeda), cor=C['red']),
        _card_estatistica('Drawdown Máx.', metricas.formatar_moeda(stats['drawdown_maximo_reais'], moeda),
                           cor=C['red'] if stats['drawdown_maximo_reais'] < 0 else None),
    ], crescer=True)
    card_expectativa = _card_expectativa_destaque(evolucao['expectativa'], tema, moeda)
    return html.Div([
        card_expectativa,
        html.Div([grupo_quantidade, grupo_taxas],
                  style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap', 'marginTop': '12px'}),
        html.Div([grupo_valores], style={'display': 'flex', 'gap': '12px', 'flexWrap': 'wrap', 'marginTop': '12px'}),
    ], style={'marginBottom': '16px'})


_TOGGLE_LARGURA = 42


_TOGGLE_ALTURA = 22


_TOGGLE_BOLINHA = 18


_TOGGLE_MARGEM = 2


def _estilo_toggle_trilho(ativo: bool) -> dict:
    """Trilho do toggle switch 'Ano Inteiro' (feedback 2026-07-12: pediu o
    visual padrão de toggle switch de app — trilho arredondado + bolinha
    deslizante — no lugar do botão retangular antigo. Só no clássico: no
    SQ'98 esse visual destoaria do bisel Win98, mantido como botão."""
    return {
        'position': 'relative', 'width': f'{_TOGGLE_LARGURA}px', 'height': f'{_TOGGLE_ALTURA}px',
        'borderRadius': f'{_TOGGLE_ALTURA // 2}px', 'border': 'none', 'padding': '0',
        'cursor': 'pointer', 'flexShrink': '0',
        # C['border'] (#1C1C28) é quase invisível contra o fundo escuro pra
        # servir de trilho "desligado" — tom intermediário só pra esse uso.
        'background': C['green'] if ativo else '#3A3A48',
        'transition': 'background-color 0.2s ease',
    }


def _estilo_toggle_bolinha(ativo: bool) -> dict:
    deslocamento = _TOGGLE_LARGURA - _TOGGLE_BOLINHA - _TOGGLE_MARGEM if ativo else _TOGGLE_MARGEM
    return {
        'position': 'absolute', 'top': f'{_TOGGLE_MARGEM}px', 'left': f'{deslocamento}px',
        'width': f'{_TOGGLE_BOLINHA}px', 'height': f'{_TOGGLE_BOLINHA}px', 'borderRadius': '50%',
        'background': '#FFFFFF', 'boxShadow': '0 1px 3px rgba(0,0,0,0.4)',
        'transition': 'left 0.2s ease',
    }


def _cards_ou_vazio_estatisticas(stats: dict, tema: str = 'light',
                                   ano: int | None = None, mes: int | None = None,
                                   ativo: str | None = None) -> html.Div:
    """2026-08-02, correção do Jesué: um período real sem trades (ex: mês
    corrente ainda em andamento) mostrava só a frase "Nenhum trade real
    neste período" no lugar dos cards — "acredito que os blocos tenham
    que ser carregados, mesmo com valores zerados. Senão fica parecendo um
    bug." `calcular_estatisticas` já devolve um dict `vazio` seguro (todo
    campo numérico em 0/None, nunca NaN/divisão por zero — ver o dict
    `vazio` lá) especificamente pra isso; `_cards_header_tradezella`/
    `_cards_detalhe_estatisticas` já toleram `num_operacoes == 0` (guardas
    explícitos nos RingProgress/payoff/profit_factor). O aviso de "nenhum
    trade" continua aparecendo — só que ACIMA dos cards zerados, não no
    lugar deles (ver `atualizar_periodo_estatisticas`, que já emite esse
    aviso separadamente via `Output('...', 'children')` do título)."""
    return html.Div([
        _cards_header_tradezella(stats, tema),
        _bloco_tradezella('Detalhes', _cards_detalhe_estatisticas(stats, tema, ano=ano, mes=mes, ativo=ativo)),
    ])


def _resto_periodo_estatisticas(stats: dict, tema: str = 'light') -> html.Div:
    """Evolução de saldo do período — a parte do painel de Estatísticas que
    pode ser livremente recriada a cada troca de mês/ano (nenhum desses
    componentes guarda estado de clique). O calendário em si (grade +
    botões) NÃO mora aqui — ver o comentário em `_construir_diario_
    estatisticas` pro porquê.

    Por Ativo/Janela/Sala/Tags Setup-Erro NÃO moram mais aqui (2026-07-30,
    "upgrade diario.docx": "junte os blocos... num painel de dashboard
    estatístico") — migraram pra dentro de "Análise de Estratégia"
    (`_bloco_analise_estrategia`), junto de "Confiabilidade por Estratégia".
    Antes eram sensíveis ao período (mês/ano selecionado); agora são
    ALL-TIME, consistentes com o resto daquele painel — só a Evolução de
    Saldo continua reagindo ao seletor de período."""
    return html.Div([
        _bloco_tradezella('Evolução de Saldo', dcc.Graph(
            figure=_grafico_evolucao_saldo(stats, tema, stats.get('moeda', 'BRL')),
            config={'displayModeBar': False, 'responsive': True},
            style={'width': '100%'},
        )),
    ])


def _bloco_status_badges() -> html.Div:
    """Reconciliação MT5 (agora em background) e resumo de Aderência aos Traçados
    (agora enriquece o Histórico) — ver diario_app_98.py::
    _bloco_status_badges_98 pro mesmo raciocínio."""
    resumo_rec = dt.resumo_reconciliacao()
    if resumo_rec is None:
        badge_rec = html.Span('Reconciliação: aguardando o 1º ciclo em background (a cada 5min).',
                               style={'color': C['text2']})
    else:
        problemas = (resumo_rec['divergente'] + resumo_rec['sem_correspondencia_mt5']
                     + resumo_rec['sem_correspondencia_diario'])
        if problemas == 0:
            badge_rec = html.Span(
                ['Reconciliação MT5: ', *_texto_com_icone('tabler:check', f"{resumo_rec['ok']} trade(s) OK (últimos 30 dias).")],
                style={'color': C['green'], 'display': 'inline-flex', 'alignItems': 'center', 'gap': '2px'})
        else:
            badge_rec = html.Span(
                ['Reconciliação MT5: ', *_texto_com_icone('tabler:alert-triangle',
                    f"{problemas} pendência(s) nos últimos 30 dias "
                    f"({resumo_rec['divergente']} divergente(s), {resumo_rec['sem_correspondencia_mt5']} sem execução MT5, "
                    f"{resumo_rec['sem_correspondencia_diario']} sem registro no diário).")],
                style={'color': C['gold'], 'display': 'inline-flex', 'alignItems': 'center', 'gap': '2px'})

    resumo_ader = dt.avaliar_aderencia_tracados()
    if resumo_ader.empty:
        badge_ader = html.Span(
            'Aderência aos Traçados: sem trades avaliáveis ainda (precisa de nível S/R referenciado + snapshot de mercado).',
            style={'color': C['text2']})
    else:
        total = len(resumo_ader)
        precisos = int((resumo_ader['classificacao'] == 'visto_preciso').sum())
        badge_ader = html.Span(f"Aderência aos Traçados: {precisos}/{total} trade(s) vistos com precisão pelo motor "
                                f"(ver coluna no Histórico).", style={'color': C['text']})

    return _bloco_tradezella('Status', html.Div([
        html.Div(badge_rec, style={'fontSize': '12px', 'marginBottom': '4px'}),
        html.Div(badge_ader, style={'fontSize': '12px'}),
    ]))


def _opacidade_progress_tracker(n: int) -> float:
    """Bucket de intensidade do Progress Tracker — 0 trades = célula vazia,
    escala até 1.0 (calibrado contra o limiar de overtrading já documentado
    no estudo de estratégias vencedoras: 5+ trades/dia é o ponto onde os
    dias começam a virar prejuízo)."""
    if n <= 0:
        return 0.0
    if n <= 2:
        return 0.25
    if n <= 4:
        return 0.5
    if n <= 6:
        return 0.75
    return 1.0


def _bloco_progress_tracker(por_dia: dict, tema: str = 'light') -> dmc.Card:
    """Progress Tracker (item F, fase 7, `spec_redesenho_tradezella.md`) —
    heatmap estilo GitHub contribution graph, últimas 52 semanas terminando
    hoje. Intensidade = Nº de trades operados naquele dia (decisão do
    Jesué, 2026-07-30: "analise o padrão do TradeZella e implemente da
    mesma forma" — usa `por_dia['n']`, que já existe em
    `calcular_estatisticas`, em vez de inventar uma leitura de disciplina/
    aderência sem regra formal cadastrada — mesma cautela já documentada em
    `calcular_score_confiabilidade`)."""
    p = PALETA_GAUGE.get(tema, PALETA_GAUGE['light'])
    hoje = date.today()
    # Python: segunda=0..domingo=6 -> aqui queremos domingo=0..sábado=6
    # (primeiro dia da semana = domingo, mesma convenção do resto do Diário).
    indice_domingo = (hoje.weekday() + 1) % 7
    domingo_atual = hoje - timedelta(days=indice_domingo)
    n_semanas = 52
    domingo_inicio = domingo_atual - timedelta(weeks=n_semanas - 1)

    def _cor_celula(n: int) -> str:
        op = _opacidade_progress_tracker(n)
        return _hex_para_rgba(p['green'], op) if op > 0 else 'rgba(128,128,128,0.08)'

    colunas_semana, rotulos_mes = [], []
    mes_ja_rotulado = None
    for semana in range(n_semanas):
        domingo_semana = domingo_inicio + timedelta(weeks=semana)
        celulas = []
        rotulo_mes = ''
        for offset in range(7):
            dia = domingo_semana + timedelta(days=offset)
            if dia > hoje:
                celulas.append(html.Div(style={'width': '11px', 'height': '11px'}))
                continue
            n = por_dia.get(dia.isoformat(), {}).get('n', 0)
            celulas.append(html.Div(title=f"{dia.strftime('%d/%m/%Y')}: {n} trade(s)", style={
                'width': '11px', 'height': '11px', 'borderRadius': '2px', 'background': _cor_celula(n),
            }))
            if dia.day <= 7 and dia.month != mes_ja_rotulado:
                rotulo_mes = _NOMES_MES[dia.month][:3]
                mes_ja_rotulado = dia.month
        rotulos_mes.append(rotulo_mes)
        colunas_semana.append(html.Div(celulas, style={'display': 'flex', 'flexDirection': 'column', 'gap': '2px'}))

    linha_meses = html.Div([
        html.Div(rotulo, style={'width': '11px', 'fontSize': '9px', 'color': p['text2'], 'flexShrink': '0'})
        for rotulo in rotulos_mes
    ], style={'display': 'flex', 'gap': '2px', 'marginBottom': '4px'})

    legenda = html.Div([
        html.Span('Menos', style={'fontSize': '10px', 'color': p['text2'], 'marginRight': '6px'}),
        *[html.Div(style={'width': '11px', 'height': '11px', 'borderRadius': '2px', 'marginRight': '3px',
                           'background': (_hex_para_rgba(p['green'], op) if op > 0 else 'rgba(128,128,128,0.08)')})
          for op in (0.0, 0.25, 0.5, 0.75, 1.0)],
        html.Span('Mais', style={'fontSize': '10px', 'color': p['text2'], 'marginLeft': '3px'}),
    ], style={'display': 'flex', 'alignItems': 'center', 'marginTop': '8px'})

    return dmc.Card([
        dmc.Text('Progress Tracker', size='sm', fw=600, mb='sm'),
        html.Div([linha_meses, html.Div(colunas_semana, style={'display': 'flex', 'gap': '2px'})],
                  style={'overflowX': 'auto', 'paddingBottom': '4px'}),
        legenda,
        _bloco_o_que_fazer(
            'Cada célula é um dia — quanto mais escura, mais trades reais você operou nele. '
            'Últimas 52 semanas até hoje.',
            dt.feedback_progress_tracker(por_dia)),
    ], withBorder=True, shadow='sm', radius='lg', p='lg', mb='md', className='tz-card', w='100%')


def _construir_diario_estatisticas(stats_geral: dict, scores: dict, tema: str = 'light') -> html.Div:
    """Shell montado 1x por ativação da aba — ver
    `diario_app_98.py::_construir_diario98_estatisticas` pro raciocínio
    completo (mesma estrutura, só a casca visual muda).

    Os botões de navegação do calendário (◀/▶/"Ver Ano Inteiro") moram AQUI,
    criados uma única vez, e NÃO dentro de `diario-periodo-conteudo`/
    `diario-resto-periodo-conteudo` (que `atualizar_periodo_estatisticas`
    reconstrói a cada troca de mês/ano/toggle). Causa raiz do bug relatado
    em 2026-07-12 (calendário pulando pra mês/ano aparentemente aleatório e
    o toggle "Ano Corrente" às vezes não fazendo nada): um `html.Button`
    recriado do zero nasce com `n_clicks=0`, e como esse prop é Input do
    callback de navegação, o Dash detectava essa queda pra 0 como "mais um
    clique" e disparava a navegação sozinha — cada clique real gerava 0-2
    cliques fantasmas extras, em direção imprevisível pro usuário. Com os
    botões fixos aqui e só `children`/`style` atualizados por callback
    (nunca o componente inteiro), `n_clicks` nunca mais reseta."""
    if stats_geral['num_operacoes'] == 0:
        return _placeholder_aba('Estatísticas (nenhum trade real lançado ainda)')

    hoje = date.today()
    mes_inicial = f"{hoje.year:04d}-{hoje.month:02d}"
    # ActionIcon circular com ícone Tabler — correção 2026-08-01 (glifos
    # Unicode ◀▶ como ícone de botão liam como "site velho dos anos 2000",
    # ver spec_redesenho_tradezella.md seção "Regra permanente").
    def _botao_cal_nav(icone, id_botao):
        return dmc.ActionIcon(DashIconify(icon=icone, width=16), id=id_botao, n_clicks=0,
                               variant='subtle', color='gray', radius='xl', size='md')
    return html.Div([
        dcc.Store(id='diario-calendario-mes', data=mes_inicial),
        dcc.Store(id='diario-modo-ano', data=False),
        # Stats configuráveis por dia no calendário (item F, fase 4) —
        # persiste entre sessões (mesmo padrão de `store-tema`), já que é
        # preferência de exibição, não estado de navegação.
        dcc.Store(id='store-calendario-stats-visiveis', data=_STATS_CALENDARIO_PADRAO, storage_type='local'),

        # Seletor de ativo (Fase 4 da integração cTrader, 2026-08-06):
        # "Todos" (value='', default) preserva o consolidado de sempre —
        # sempre BRL, homogêneo entre ativos (ver `converter_usd_para_
        # reais`). Selecionar um ativo forex (XAUUSD/EURUSD, nativo em USD)
        # troca TODA a página (cards, calendário, evolução de saldo) pra
        # US$ — ver `atualizar_periodo_estatisticas`/`calcular_estatisticas
        # (ativo=...)`. Value vazia, não `None`, porque `dmc.Select` some
        # com o placeholder se `value=None` inicial não bate com nenhuma
        # opção — `''` é a opção real "Todos os Ativos" abaixo.
        dmc.Select(
            id='diario-estat-ativo', label='Ativo', value='', clearable=False, w=200, mb='md',
            data=[{'label': 'Todos os Ativos', 'value': ''}] +
                 [{'label': a, 'value': a} for a in cfg.ATIVOS_DIARIO],
        ),

        html.Div(id='diario-cards-conteudo'),

        # Composição assimétrica real (item F, Fase 2.6 — `dmc.Grid`, não
        # mais uma pilha vertical de blocos full-width): coluna estreita à
        # esquerda (Evolução de Saldo, Anticovardia Score, Progress
        # Tracker, Desempenho por Duração — todos empilhados), coluna
        # larga à direita (Calendário + Trades Recentes) — mesma
        # composição do Dashboard real do TradeZella (`image1.png` do docx
        # de referência). UM ÚNICO grid (2026-07-31, feedback recorrente
        # de "espaço vazio"): tentativas anteriores usavam grids
        # SEPARADOS pra cada par de blocos (Evolução+Score / Calendário,
        # depois Progress Tracker / Trades Recentes) — cada par forçado a
        # bater altura exata sempre sobrava vão (esticando o menor, se
        # `align='stretch'`, ou deixando fundo vazio abaixo dele, se
        # `align='flex-start'`). Empilhar TUDO relacionado numa coluna só
        # deixa as duas colunas fluírem em alturas totais parecidas — sem
        # forçar par a par — e ainda aproveita o conteúdo real (Progress
        # Tracker, Duração da Operação) pra preencher a coluna esquerda em
        # vez de forçá-la a esticar vazia. O resto do conteúdo exclusivo
        # do Super Quant (Vencedora/Perdedora, Melhor por X, breakdowns,
        # Confiabilidade, Mentor de IA) continua existindo, só que ABAIXO
        # deste grid — decisão do Jesué: "mantenha o que já temos,
        # adicione o que o TradeZella agrega".
        dmc.Grid([
            dmc.GridCol([
                html.Div(id='diario-resto-periodo-conteudo'),
                _bloco_anticovardia_score(tema),
                _bloco_discipline_score(tema),
                _bloco_duracao_performance(tema),
            ], span={'base': 12, 'md': 5}),
            dmc.GridCol(
                html.Div([_bloco_tradezella('Calendário', html.Div([
                    html.Div([
                        html.Div([
                            _botao_cal_nav('tabler:chevron-left', 'btn-diario-cal-mes-prev'),
                            html.Span(_NOMES_MES[hoje.month], id='diario-calendario-label-mes', style={
                                'margin': '0 8px', 'fontSize': '12px', 'fontFamily': FONTS['display'],
                                'fontWeight': '700',
                            }),
                            _botao_cal_nav('tabler:chevron-right', 'btn-diario-cal-mes-next'),
                        ], style={'display': 'flex', 'alignItems': 'center', 'marginRight': '16px'}),
                        html.Div([
                            _botao_cal_nav('tabler:chevron-left', 'btn-diario-cal-ano-prev'),
                            html.Span(str(hoje.year), id='diario-calendario-label-ano', style={
                                'margin': '0 8px', 'fontSize': '12px', 'fontFamily': FONTS['display'],
                                'fontWeight': '700',
                            }),
                            _botao_cal_nav('tabler:chevron-right', 'btn-diario-cal-ano-next'),
                        ], style={'display': 'flex', 'alignItems': 'center'}),
                        html.Span('Ano Inteiro', style={
                            'marginLeft': 'auto', 'marginRight': '8px', 'fontSize': '11px', 'color': C['text2'],
                            'fontFamily': FONTS['display'],
                        }),
                        html.Button(html.Div(id='diario-modo-ano-bolinha', style=_estilo_toggle_bolinha(False)),
                                    id='btn-diario-modo-ano', n_clicks=0, style=_estilo_toggle_trilho(False)),
                        # Config de stats por dia (item F, fase 4) — botão
                        # estático (mesmo raciocínio de `btn-diario-
                        # checklist`: nunca recriado pela própria callback,
                        # só o `children` do painel abaixo muda) que
                        # abre/fecha um `dcc.Checklist`.
                        dmc.Button('Stats', id='btn-diario-cal-config', n_clicks=0,
                                   leftSection=DashIconify(icon='tabler:adjustments-horizontal', width=14),
                                   variant='subtle', color='gray', size='xs', ml='8px'),
                    ], style={
                        # `flexWrap`: 4 mês + ano viraram grupos separados
                        # (feedback 2026-07-12) — a fileira ficou mais larga
                        # que o botão único de antes e, sem wrap, estourava a
                        # largura do iframe do Diário (dashboard.py embute em
                        # 100% de largura), forçando scroll horizontal que
                        # por sua vez empurrava a página inteira a precisar
                        # de scroll vertical também. Com wrap, em telas
                        # estreitas o toggle só desce pra 2ª linha.
                        'display': 'flex', 'flexWrap': 'wrap', 'alignItems': 'center',
                        'rowGap': '8px', 'marginBottom': '8px',
                    }),
                    html.Div(id='diario-cal-config-conteudo'),
                    html.Div(id='diario-calendario-grid'),
                ])),
                # Trades Recentes (item F, 2026-07-31 — "você não está
                # mostrando o bloco de trades recentes no dashboard, como
                # é no TradeZella") + Progress Tracker (item F, fase 7)
                # empilhados logo abaixo do Calendário nesta MESMA coluna
                # direita — sempre ALL-TIME (não filtra por período do
                # calendário acima), mesma convenção de `stats_geral`/
                # `scores` abaixo. Progress Tracker fica nesta coluna (não
                # na esquerda, com Duração da Operação) pra equilibrar a
                # altura total das duas colunas — verificado ao vivo via
                # `getBoundingClientRect`: as duas fecham a 31px uma da
                # outra nesta combinação, contra ~586px quando os dois
                # ficavam juntos na esquerda.
                _bloco_trades_recentes(tema),
                _bloco_progress_tracker(stats_geral['por_dia'], tema),
                ]), span={'base': 12, 'md': 7},
            ),
        ], gutter='md', mb='md', align='flex-start'),

        # `stats_geral` é o SEM filtro de período (ver a chamada em
        # `montar_sub_aba_diario`), igual ao `scores` — os dois lados da
        # tabela fundida precisam falar do mesmo recorte.
        #
        # "Análise de Estratégia" (2026-07-30) fica ACIMA de "Confiabilidade
        # por Estratégia", pedido explícito do docx — os dois são ALL-TIME,
        # mesmo recorte (stats_geral/scores computados 1x na entrada da aba).
        _bloco_tradezella('Análise de Estratégia', _bloco_analise_estrategia(stats_geral, tema)),
        _bloco_tradezella('Confiabilidade por Estratégia',
                          _ranking_confiabilidade_compacto(scores, stats_geral['por_estrategia'])),

        _bloco_status_badges(),
    ])


app = dash.Dash(
    __name__,
    title='Super Quant — Anticovardia',
    update_title=None,
    suppress_callback_exceptions=True,
)

app.layout = dmc.MantineProvider(id='mantine-provider-dashboard', defaultColorScheme='light', children=html.Div([

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

    # Store da seção ativa do menu lateral (rollout TradeZella, 2026-07-31)
    # — "Dashboard" (ex-Estatísticas) é a nova home/página inicial.
    dcc.Store(id='store-aba-ativa', data='dashboard'),
    # Menu retrátil (2026-07-31) — só a trilha de ícones fica visível
    # quando True (painel de rótulos recolhido + `navbar` do AppShell
    # encolhido, ver `alternar_menu_colapsado`).
    dcc.Store(id='store-menu-colapsado', data=True),
    # Árvore do menu (2026-08-04, fusão trilha+painel): {chave_grupo: bool}
    # — só grupos com sub-seções (hoje só 'coaching') usam isto. Clicar no
    # título do grupo abre/fecha a lista de sub-páginas inline, ver
    # `selecionar_aba`/`atualizar_arvore_menu`.
    dcc.Store(id='store-menu-grupo-aberto', data={}),

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

    # Dummy Output da ponte de seção pro iframe do Diário (rollout
    # TradeZella, 2026-07-31) — ver clientside_callback perto de
    # `atualizar_painel_principal` e `assets/secao_diario.js`.
    dcc.Store(id='store-secao-dummy'),
    dcc.Store(id='store-hist-filtro-dummy'),

    # Filtro de data do Histórico vindo do clique num dia do calendário do
    # Dashboard (rollout TradeZella, 2026-07-31) — `clicar_dia_calendario`
    # escreve aqui (não pode mais escrever direto em `diario-hist-filtro-
    # data-calendario`, que agora mora no processo FILHO); um
    # clientside_callback repassa por postMessage, ver `assets/
    # secao_diario.js`.
    dcc.Store(id='store-historico-filtro-data-calendario'),

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
    # MENU LATERAL (fusão trilha+painel, 2026-08-04 — correção do Jesué
    # sobre o rollout TradeZella original: "você colocou esse menu extra
    # ao lado como se fosse uma expansão... não quero dessa forma"). Antes
    # eram 2 elementos DOM paralelos (trilha de ícones + painel de
    # `NavLink` ícone+texto) — agora é UMA lista de linhas (`_linha_menu`),
    # 1 por grupo, que anima de "só ícone" (recolhido) pra "ícone + texto"
    # (expandido) via CSS (`.sq-menu-*`, `assets/style.css`), nunca
    # duplicando o item em outro lugar. Grupos com sub-seções (hoje só
    # Coaching) ganham uma árvore inline recolhível (chevron + lista
    # indentada, `store-menu-grupo-aberto`) em vez de um painel à parte.
    # `btn-menu-colapsar` (☰) continua encolhendo o `navbar` do próprio
    # `dmc.AppShell` (Output direto no prop, não só CSS) pra sobrar só os
    # ícones — mesma disciplina de sempre: nenhum elemento clicável é
    # recriado, só `className`/`style` mudam via callback (`atualizar_
    # menu_ativo`/`atualizar_arvore_menu`/`alternar_menu_colapsado`).
    dmc.AppShell([
        dmc.AppShellHeader(
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
            ], style={'display': 'flex', 'alignItems': 'center', 'height': '100%', 'padding': '0 20px'}),
        ),
        dmc.AppShellNavbar(
            html.Div([
                html.Button(DashIconify(icon='tabler:menu-2', width=20),
                            id='btn-menu-colapsar', n_clicks=0, title='Recolher/expandir menu', style={
                    'width': '38px', 'height': '32px', 'border': 'none',
                    'background': 'transparent', 'color': C['text2'], 'cursor': 'pointer',
                    'marginBottom': '8px', 'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center',
                }),
                html.Div([_linha_menu(grupo, nome, icone, sub) for grupo, nome, icone, sub in _GRUPOS_MENU],
                         style={'flex': 1, 'display': 'flex', 'flexDirection': 'column', 'gap': '2px'}),
                # Sino de notificações — movido pro pé do menu (2026-08-04,
                # pedido do Jesué: "coloque o sino para baixo" — ficava
                # solto no topo, com um vão grande até o rodapé quando
                # recolhido). `dmc.Popover` NÃO controlado por callback
                # (abre/fecha sozinho no clique, Mantine cuida do próprio
                # estado client-side) — mais simples e sem risco de
                # `ReferenceError` de Output pra um componente que só existe
                # client-side. `indicador-notificacoes` (badge vermelho) É
                # controlado via callback (`atualizar_badges_menu`), pois
                # reflete dado real (pendências).
                dmc.Popover([
                    dmc.PopoverTarget(
                        html.Div([
                            dmc.Indicator(
                                DashIconify(icon='tabler:bell', width=20, className='sq-menu-icone'),
                                id='indicador-notificacoes', size=8, color='red', disabled=True,
                            ),
                            html.Span('Notificações', className='sq-menu-texto'),
                        ], id='btn-notificacoes', n_clicks=0, title='Notificações',
                           className='sq-menu-item', style={'cursor': 'pointer'}),
                    ),
                    dmc.PopoverDropdown(html.Div(id='conteudo-notificacoes', children=[
                        dmc.Text('Notificações', size='sm', fw=700, mb='xs'),
                        dmc.Text('Nenhuma notificação do Quant Lab ainda.', size='xs', c=C['text2']),
                    ], style={'minWidth': '240px'})),
                ], position='right-start', withArrow=True),
                # Rodapé — sessão de usuário logado (visual, sem login real
                # por enquanto — ver `_bloco_sessao_usuario`).
                _bloco_sessao_usuario(),
            ], id='menu-coluna', className='sq-menu-coluna sq-menu-recolhido', style={
                'display': 'flex', 'flexDirection': 'column', 'height': '100%',
                'paddingTop': '4px', 'paddingBottom': '8px', 'boxSizing': 'border-box',
            }),
            p=0,
        ),
        dmc.AppShellMain(
    # =========================================================================
    # CONTEÚDO PRINCIPAL
    # =========================================================================
    html.Div(id='conteudo-principal', children=[

      # DASHBOARD (ex-Estatísticas do Diário) — nova página inicial
      # (rollout TradeZella, 2026-07-31: "Tire a aba Estatísticas de dentro
      # do Diário, e torne-o a página inicial do Dashboard"). Casca estática
      # construída 1x aqui (mesmo padrão do Hero/Quant Lab abaixo) — os
      # botões de navegação do calendário moram dentro dela, nunca
      # recriados; `atualizar_periodo_estatisticas` só troca `children`/
      # `style` de placeholders internos.
      html.Div(id='painel-dashboard', children=[
          _construir_diario_estatisticas(dt.calcular_estatisticas(), dt.calcular_score_confiabilidade(), 'light'),
      ]),

      html.Div(id='painel-quantlab', style={'display': 'none'}, children=[

        # Seletor WIN / WDO + status de conexão/contrato/atualização — só
        # fazem sentido dentro do Quant Lab agora (rollout TradeZella,
        # 2026-07-31: "essas informações agora só fazem sentido dentro do
        # Quant Lab"), não mais um cabeçalho global visível em qualquer
        # aba. MESMOS ids de sempre (`atualizar_dashboard`, tick de 1s,
        # não muda nada) — só a posição na árvore mudou. Estático, nunca
        # recriado (só `style` muda via `atualizar_estilo_ativo_
        # selecionado`) — ver docstring de `construir_hero`.
        html.Div([
            html.Div([
                html.Button('WIN', id='btn-win', n_clicks=0,
                            className='btn-ativo',
                            style=_estilo_botao_ativo('WIN', 'WIN')),
                html.Button('WDO', id='btn-wdo', n_clicks=0,
                            className='btn-ativo',
                            style=_estilo_botao_ativo('WDO', 'WIN')),
            ], style={'display': 'flex', 'gap': '6px', 'marginBottom': '6px'}),
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
        ], style={'marginBottom': '16px'}),

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
                html.Button([_icone('tabler:pointer', tamanho=13), ' Cursor'], id='btn-tracado-cursor',
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

      # Backtesting tick a tick (protótipo — ver
      # spec_prototipo_backtesting_tick.md) — conteúdo real montado sob
      # demanda por `carregar_backtest` (Input em store-aba-ativa), não
      # eager no boot do processo: evita ler o diário inteiro toda vez
      # que o dashboard sobe, só quando a aba é de fato aberta.
      html.Div(id='painel-back', style={'display': 'none'}, children=[
          secao('BACKTESTING TICK A TICK'),
          dcc.Loading(
              id='loading-backtest', type='dot', color=C['gold'],
              children=html.Div(id='backtest-conteudo'),
          ),
          dmc.Drawer(
              id='drawer-detalhe-backtest',
              opened=False,
              position='right',
              size='1160px',
              offset=16,
              radius='md',
              shadow='xl',
              title='Reconstrução Tick a Tick',
              transitionProps={'duration': 220, 'transition': 'slide-left'},
              overlayProps={'opacity': 0.15, 'blur': 2},
              children=dcc.Loading(
                  id='loading-backtest-drawer', type='dot', color=C['gold'],
                  children=html.Div(id='drawer-detalhe-backtest-conteudo'),
              ),
          ),
          dcc.Store(id='store-backtest-id-trade'),
          dcc.Store(id='store-backtest-chart-payload'),
          dcc.Store(id='store-backtest-chart-dummy'),
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
          # Notificações (item 1D, upgrade 2026-08-04) — toggle por tipo
          # de evento do sino do menu lateral. Persistido em `config_
          # operacional.json` (mesmo arquivo do Modo Aprendizagem — server-
          # side de verdade, lido pelos 2 processos), não `localStorage`.
          html.Div([
              html.Div('Notificações', style={
                  'fontSize': '10px', 'color': C['text2'], 'textTransform': 'uppercase',
                  'letterSpacing': '0.08em', 'fontFamily': FONTS['display'], 'marginBottom': '10px',
              }),
              html.Div([
                  dmc.Switch(id='switch-notif-posicao_aberta', label='Posição aberta/fechada',
                             checked=config_operacional.notificacoes_config()['posicao_aberta'], size='sm'),
                  dmc.Switch(id='switch-notif-pendencia_diario', label='Pendência de Diário',
                             checked=config_operacional.notificacoes_config()['pendencia_diario'], size='sm'),
                  dmc.Switch(id='switch-notif-pendencia_coaching', label='Pendência de Coaching',
                             checked=config_operacional.notificacoes_config()['pendencia_coaching'], size='sm'),
                  dmc.Switch(id='switch-notif-sinal_quantlab', label='Sinal do Quant Lab',
                             checked=config_operacional.notificacoes_config()['sinal_quantlab'], size='sm'),
              ], style={'display': 'flex', 'flexDirection': 'column', 'gap': '10px'}),
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
    }),
        ),
    ], id='appshell-principal', header={'height': 60}, navbar={'width': 52, 'breakpoint': 'sm'}, padding=0),

    # =========================================================================
    # CHAT FLUTUANTE — Mentor IA (ícone no canto inferior direito)
    # =========================================================================

    # Store para controlar se o chat está aberto
    dcc.Store(id='store-chat-aberto', data=False),

    # Botão flutuante
    html.Div([
        html.Button(_icone('tabler:sparkles', tamanho=26, cor=C['bg']), id='btn-chat-toggle', n_clicks=0, style={
            'width': '56px', 'height': '56px',
            'borderRadius': '50%',
            'background': f'linear-gradient(135deg, {C["gold"]}, #A07840)',
            'border': 'none', 'cursor': 'pointer',
            'display': 'flex', 'alignItems': 'center', 'justifyContent': 'center',
            'color': C['bg'],
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
                html.Span(_icone('tabler:sparkles', tamanho=14, cor=C['gold']), style={'marginRight': '4px'}),
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
}))


# =============================================================================
# CALLBACKS
# =============================================================================

# -----------------------------------------------------------------------------
# BACKTESTING TICK A TICK — protótipo (spec_prototipo_backtesting_tick.md)
# -----------------------------------------------------------------------------
# Grid + Drawer seguindo o MESMO molde já validado em diario_app_classico.py
# (_grid_historico/atualizar_detalhe_historico_grid): `cellClicked` nesta
# versão do dash-ag-grid nunca traz `data`, só rowIndex — recupera a linha
# via `State('rowData')`; toda coluna precisa `sortable: True` (senão
# `cellClicked` não dispara nela). Sem cross-import de diario_app_classico.py
# (convenção do projeto) — duplicado localmente, mesmo idioma.

_CELL_STYLE_ELEGIVEL_BACKTEST = {
    'styleConditions': [
        {'condition': "params.value === 'Tick disponível'", 'style': {'color': 'var(--green)'}},
        {'condition': "params.value === 'Sem ticket MT5'", 'style': {'color': 'var(--text2)'}},
    ]
}
_CELL_STYLE_RESULTADO_BACKTEST = {
    'styleConditions': [
        {'condition': 'params.value > 0', 'style': {'color': 'var(--green)', 'fontWeight': 700}},
        {'condition': 'params.value < 0', 'style': {'color': 'var(--red)', 'fontWeight': 700}},
    ]
}


def _linhas_grid_backtest() -> list[dict]:
    """1 linha por trade do Diário, marcando quais têm ticket MT5 vinculado
    (`tickets_finalizados.parquet`) — só esses são elegíveis a reconstrução
    tick a tick (ver "cobertura desigual da base" na spec)."""
    trades = dt.listar_trades()
    if trades.empty:
        return []

    tickets_df = persistencia.carregar_tickets_finalizados()
    ids_com_ticket = (set(int(i) for i in tickets_df['id_trade'].unique())
                       if tickets_df is not None and not tickets_df.empty else set())

    linhas = []
    for _, t in trades.sort_values('data_op', ascending=False).iterrows():
        id_trade = int(t['id_trade'])
        elegivel = id_trade in ids_com_ticket
        resultado = t.get('resultado_pontos')
        linhas.append({
            'id_trade': id_trade,
            'data': pd.Timestamp(t['data_op']).strftime('%d/%m/%Y') if pd.notna(t.get('data_op')) else '—',
            'ativo': t.get('ativo') or '—',
            'tipo_operacao': t.get('tipo_operacao') or '—',
            'resultado_pontos': float(resultado) if pd.notna(resultado) else None,
            'resultado_label': f"{resultado:+.0f} pts" if pd.notna(resultado) else '—',
            'elegivel': elegivel,
            'elegivel_label': 'Tick disponível' if elegivel else 'Sem ticket MT5',
            'acao_label': 'Ver' if elegivel else '—',
        })
    return linhas


def _grid_backtest(linhas: list) -> dag.AgGrid:
    return dag.AgGrid(
        id='grid-backtest',
        rowData=linhas,
        columnDefs=[
            {'field': 'id_trade', 'hide': True},
            {'field': 'data', 'headerName': 'Data', 'sortable': True, 'filter': True, 'maxWidth': 100},
            {'field': 'ativo', 'headerName': 'Ativo', 'sortable': True, 'filter': True, 'maxWidth': 80},
            {'field': 'tipo_operacao', 'headerName': 'Direção', 'sortable': True, 'filter': True, 'maxWidth': 90},
            {'field': 'resultado_label', 'headerName': 'Resultado', 'sortable': True, 'filter': True,
             'cellStyle': _CELL_STYLE_RESULTADO_BACKTEST},
            {'field': 'elegivel_label', 'headerName': 'Reconstrução', 'sortable': True, 'filter': True,
             'flex': 1, 'cellStyle': _CELL_STYLE_ELEGIVEL_BACKTEST},
            {'field': 'acao_label', 'headerName': '', 'sortable': True, 'filter': False, 'maxWidth': 75,
             'cellStyle': {'color': 'var(--gold)', 'fontWeight': 700, 'cursor': 'pointer'}},
        ],
        defaultColDef={'resizable': True, 'sortable': True, 'flex': 1},
        dashGridOptions={'domLayout': 'autoHeight', 'pagination': True, 'paginationPageSize': 20,
                          'paginationPageSizeSelector': [10, 20, 50, 100]},
        style={'width': '100%'},
        className='ag-theme-alpine',
    )


@app.callback(
    Output('backtest-conteudo', 'children'),
    Input('store-aba-ativa', 'data'),
)
def carregar_backtest(aba_ativa):
    """Monta a grid só quando a aba Backtesting é aberta de fato — não no
    boot do processo (mesmo raciocínio de `painel-diario` ser um iframe
    separado: nada aqui deve pesar no tick de 1s do resto do dashboard)."""
    if aba_ativa != 'back':
        return no_update
    linhas = _linhas_grid_backtest()
    if not linhas:
        return _placeholder_aba('Backtesting (nenhum trade lançado ainda)')
    n_elegiveis = sum(1 for l in linhas if l['elegivel'])
    return html.Div([
        html.Div(
            f'{n_elegiveis} de {len(linhas)} trades têm ticket MT5 vinculado — só esses '
            'permitem reconstrução tick a tick (trades antigos/importados sem ticket ficam '
            'de fora, ver spec_prototipo_backtesting_tick.md).',
            style={'fontSize': '12px', 'color': C['text2'], 'marginBottom': '12px'},
        ),
        _grid_backtest(linhas),
    ])


def _linha_veredito(label: str, valor) -> html.Div:
    return html.Div([
        html.Span(label, style={'color': C['text2'], 'fontSize': '12px'}),
        html.Span(str(valor), style={'fontFamily': FONTS['mono'], 'fontSize': '13px', 'fontWeight': 600}),
    ], style={'display': 'flex', 'justifyContent': 'space-between', 'padding': '6px 0',
              'borderBottom': f'1px solid {C["border"]}'})


def _conteudo_detalhe_backtest_indisponivel(linha: dict) -> html.Div:
    return _placeholder_aba(
        f"Trade #{linha.get('id_trade')} não tem ticket MT5 vinculado — "
        "sem reconstrução tick a tick possível (trade antigo/importado)."
    )


def _conteudo_detalhe_backtest_erro(id_trade: int, motivo: str) -> html.Div:
    return _placeholder_aba(f"Trade #{id_trade}: {motivo}")


def _conteudo_detalhe_backtest(id_trade: int) -> tuple:
    """Corpo do Drawer: alinha o trade contra os deals reais do MT5 (I/O —
    única chamada MT5 síncrona deste módulo, disparada só pelo clique do
    usuário), carrega o tick do dia já persistido (`--backfill-ticks`) e
    roda o motor de análise (`backtesting_tick.py`, puro). Retorna
    `(conteudo, payload_grafico)` — `payload_grafico` é `None` quando não
    há gráfico pra desenhar (erro ou sem tick)."""
    try:
        mt5_loader.conectar()
        alinhamento = backtest.alinhar_trade_tick(id_trade)
    except Exception as e:
        return _conteudo_detalhe_backtest_erro(id_trade, f"falha ao consultar o MT5 ({e})"), None
    finally:
        try:
            mt5_loader.desconectar()
        except Exception:
            pass

    if alinhamento is None:
        return _conteudo_detalhe_backtest_erro(id_trade, "deals não encontrados no MT5 pro período do trade."), None

    ticks = backtest.carregar_tick_para_trade(alinhamento)
    if ticks is None:
        return _conteudo_detalhe_backtest_erro(
            id_trade,
            f"tick do dia {alinhamento['datetime_entrada'].date()} ainda não foi baixado — "
            "rode `python run_backtest.py --backfill-ticks`.",
        ), None

    entrada, saida = alinhamento['datetime_entrada'], alinhamento['datetime_saida']
    tem_saida = saida is not None and alinhamento['preco_saida'] is not None

    q_entrada = backtest.qualidade_fill(
        ticks, entrada, alinhamento['preco_entrada'], alinhamento['tipo_operacao'], e_entrada=True)
    q_saida = (backtest.qualidade_fill(
        ticks, saida, alinhamento['preco_saida'], alinhamento['tipo_operacao'], e_entrada=False)
        if tem_saida else None)

    violinos = []
    estopado = None
    if tem_saida:
        janela_trade = ticks[(ticks['datetime'] >= entrada) & (ticks['datetime'] <= saida)]
        violinos = backtest.detectar_violinos(janela_trade, alinhamento['ativo'])
        estopado = backtest.correto_mas_estopado(
            ticks, alinhamento['ativo'], saida, alinhamento['preco_saida'], alinhamento['tipo_operacao'])

    cor_resultado = C['text2']
    resultado_label = '—'
    if tem_saida:
        sinal = 1 if alinhamento['tipo_operacao'] == 'compra' else -1
        pontos = (alinhamento['preco_saida'] - alinhamento['preco_entrada']) * sinal
        cor_resultado = C['green'] if pontos > 0 else (C['red'] if pontos < 0 else C['text2'])
        resultado_label = f"{pontos:+.0f} pts"

    header = html.Div([
        html.Div(f"Trade #{id_trade} — {alinhamento['ativo']} {alinhamento['contrato']} "
                  f"({alinhamento['tipo_operacao']})",
                  style={'fontSize': '14px', 'fontWeight': 700}),
        html.Div(resultado_label, style={'fontSize': '20px', 'fontWeight': 800, 'color': cor_resultado}),
        html.Div(
            'Saída exata do MT5' if alinhamento['exit_fonte'] == 'mt5_exato'
            else 'Saída aproximada (fonte: Diário — sem ticket de saída exato no MT5)',
            style={'fontSize': '11px', 'color': C['text2'] if alinhamento['exit_fonte'] == 'mt5_exato' else C['gold']},
        ),
    ], style={'marginBottom': '16px'})

    grafico = html.Div([
        html.Div(id='lw-chart-backtest', style={'width': '100%', 'height': '100%'}),
    ], style={
        'height': '380px', 'marginBottom': '4px', 'position': 'relative',
        'background': C['bg'], 'border': f'1px solid {C["border"]}', 'borderRadius': '10px',
    })

    secoes = [header, grafico]

    linhas_qualidade = []
    if q_entrada:
        linhas_qualidade.append(_linha_veredito(
            'Folga na entrada (pts deixados na mesa)', f"{q_entrada['folga_pontos']:.0f}"))
    if q_saida:
        linhas_qualidade.append(_linha_veredito(
            'Folga na saída (pts deixados na mesa)', f"{q_saida['folga_pontos']:.0f}"))
    if linhas_qualidade:
        secoes.append(_secao_detalhe_backtest('QUALIDADE DE EXECUÇÃO', html.Div(linhas_qualidade)))

    if tem_saida:
        linhas_veredito = [
            _linha_veredito('Violinos detectados na janela do trade', len(violinos)),
        ]
        if estopado is not None:
            linhas_veredito.append(_linha_veredito(
                'Correto mas estopado?',
                'SIM' if estopado['correto_mas_estopado'] else 'não',
            ))
            if estopado.get('excursao_favoravel_pontos') is not None:
                linhas_veredito.append(_linha_veredito(
                    f"Excursão favorável em {estopado['janela_minutos']} min após a saída",
                    f"{estopado['excursao_favoravel_pontos']:.0f} pts",
                ))
        secoes.append(_secao_detalhe_backtest('VEREDITO', html.Div(linhas_veredito)))

    secoes.append(_secao_detalhe_backtest('SIMULAÇÃO "E SE"', html.Div([
        html.Div([
            dcc.Input(id='backtest-sim-stop', type='number', placeholder='Stop (pts)', step='any',
                      style={'width': '110px', 'marginRight': '8px'}),
            dcc.Input(id='backtest-sim-alvo', type='number', placeholder='Alvo (pts, opcional)', step='any',
                      style={'width': '140px', 'marginRight': '8px'}),
            html.Button('Simular', id='backtest-sim-btn', style={
                'background': C['gold'], 'color': C['bg'], 'border': 'none',
                'borderRadius': '6px', 'padding': '6px 14px', 'cursor': 'pointer',
                'fontSize': '12px', 'fontWeight': 700,
            }),
        ], style={'display': 'flex', 'alignItems': 'center', 'marginBottom': '10px'}),
        html.Div(id='backtest-sim-resultado', style={'fontSize': '13px', 'fontFamily': FONTS['mono']}),
    ])))

    janela_grafico = ticks[
        (ticks['datetime'] >= entrada - pd.Timedelta(minutes=10)) &
        (ticks['datetime'] <= (saida if tem_saida else entrada) + pd.Timedelta(minutes=10))
    ]
    # Reamostra pra no máx. ~150 pontos ANTES de mandar pro gráfico —
    # achado real 2026-08-04: este build vendorizado da Lightweight
    # Charts tem um teto de renderização de ~166-184 barras (confirmado
    # até com dado sintético limpo e evenly-spaced — não é limite de
    # espaçamento/config, é intrínseco ao build) — passar mais que isso
    # faz `fitContent()`/qualquer range explícito ser silenciosamente
    # ignorado, mostrando só o rabo mais recente sem aviso nenhum. Tick
    # bruto de um trade de minutos facilmente passa de 1000+ pontos, daí
    # SEMPRE reamostrar aqui, não só quando "muito grande". Timestamps
    # também precisam ser ÚNICOS e crescentes (exigência da lib) — o
    # bucketing já garante isso. Só o TRAÇO visual perde granularidade;
    # a análise (qualidade_fill/detectar_violinos) roda no tick bruto.
    if janela_grafico.empty:
        tick_reamostrado = janela_grafico
    else:
        _MAX_PONTOS_GRAFICO = 150
        duracao_s = (janela_grafico['datetime'].max() - janela_grafico['datetime'].min()).total_seconds()
        largura_bucket_s = max(1, int(duracao_s // _MAX_PONTOS_GRAFICO) + 1)
        offset_s = (janela_grafico['datetime'] - janela_grafico['datetime'].min()).dt.total_seconds()
        bucket = (offset_s // largura_bucket_s).astype(int)
        tick_reamostrado = (
            janela_grafico.assign(_bucket=bucket)
            .groupby('_bucket', as_index=False)
            .agg(datetime=('datetime', 'last'), price=('price', 'last'))
        )

    payload = {
        'ticks': [
            {'time': int(dt.timestamp()), 'value': float(p)}
            for dt, p in zip(tick_reamostrado['datetime'], tick_reamostrado['price'])
        ],
        'linhas': [
            {'preco': alinhamento['preco_entrada'], 'cor': C['gold'], 'titulo': 'Entrada'},
        ] + ([{'preco': alinhamento['preco_saida'], 'cor': C['red'] if resultado_label.startswith('-') else C['green'],
               'titulo': 'Saída'}] if tem_saida else []),
        'marcadores': [
            {'time': int(entrada.timestamp()), 'position': 'belowBar' if alinhamento['tipo_operacao'] == 'compra' else 'aboveBar',
             'color': C['gold'], 'shape': 'arrowUp' if alinhamento['tipo_operacao'] == 'compra' else 'arrowDown',
             'text': 'Entrada'},
        ] + ([{'time': int(saida.timestamp()), 'position': 'aboveBar' if alinhamento['tipo_operacao'] == 'compra' else 'belowBar',
               'color': C['red'] if resultado_label.startswith('-') else C['green'],
               'shape': 'arrowDown' if alinhamento['tipo_operacao'] == 'compra' else 'arrowUp',
               'text': 'Saída'}] if tem_saida else []),
    }

    return html.Div(secoes), payload



def _secao_detalhe_backtest(titulo: str, conteudo) -> html.Div:
    """Mesmo molde de `_secao_detalhe_trade` em diario_app_classico.py
    (rótulo dourado maiúsculo + separador) — duplicado aqui de propósito,
    convenção do projeto (sem import cruzado entre módulos de UI)."""
    return html.Div([
        html.Div(titulo, style={
            'fontSize': '11px', 'color': C['gold'], 'fontFamily': FONTS['display'],
            'fontWeight': '700', 'textTransform': 'uppercase', 'letterSpacing': '0.06em',
            'marginBottom': '10px',
        }),
        conteudo,
    ], style={'paddingTop': '16px', 'marginTop': '16px', 'borderTop': f'1px solid {C["border"]}'})


@app.callback(
    Output('drawer-detalhe-backtest', 'opened', allow_duplicate=True),
    Output('drawer-detalhe-backtest-conteudo', 'children', allow_duplicate=True),
    Output('store-backtest-chart-payload', 'data', allow_duplicate=True),
    Output('store-backtest-id-trade', 'data', allow_duplicate=True),
    Input('grid-backtest', 'cellClicked'),
    State('grid-backtest', 'rowData'),
    prevent_initial_call=True,
)
def abrir_detalhe_backtest(cell_clicked, row_data):
    """Mesmo fix já validado em `diario_app_classico.atualizar_detalhe_
    historico_grid`: `cellClicked` nesta versão do dash-ag-grid nunca traz
    `data`, só `rowIndex` — recupera a linha via `State('rowData')`.
    `store-backtest-chart-payload` é um Store ESTÁTICO (já existe no
    layout inicial, ver `painel-back`) — nunca recriado aqui dentro, só
    atualizado via Output, pra não colidir em ID com ele mesmo."""
    if not cell_clicked or not row_data:
        return no_update, no_update, no_update, no_update
    row_index = cell_clicked.get('rowIndex')
    if row_index is None or not (0 <= row_index < len(row_data)):
        return no_update, no_update, no_update, no_update
    linha = row_data[row_index]
    if not linha.get('elegivel'):
        return True, _conteudo_detalhe_backtest_indisponivel(linha), None, no_update
    id_trade = linha.get('id_trade')
    if id_trade is None:
        return no_update, no_update, no_update, no_update
    conteudo, payload = _conteudo_detalhe_backtest(int(id_trade))
    return True, conteudo, payload, int(id_trade)


@app.callback(
    Output('backtest-sim-resultado', 'children'),
    Input('backtest-sim-btn', 'n_clicks'),
    State('backtest-sim-stop', 'value'),
    State('backtest-sim-alvo', 'value'),
    State('store-backtest-id-trade', 'data'),
    prevent_initial_call=True,
)
def simular_backtest(n_clicks, stop_pontos, alvo_pontos, id_trade):
    """Recalcula o resultado do trade com stop/alvo alternativos sobre a
    série de tick real — refaz o alinhamento (nova chamada MT5, rápida:
    só os deals de 1 trade) em vez de guardar os ticks inteiros no
    navegador (poderiam ser milhares de pontos por trade)."""
    if not n_clicks or not stop_pontos or id_trade is None:
        return no_update
    try:
        mt5_loader.conectar()
        alinhamento = backtest.alinhar_trade_tick(int(id_trade))
    except Exception as e:
        return f"Falha ao consultar o MT5: {e}"
    finally:
        try:
            mt5_loader.desconectar()
        except Exception:
            pass
    if alinhamento is None:
        return "Trade não encontrado."

    ticks = backtest.carregar_tick_para_trade(alinhamento)
    if ticks is None:
        return "Tick do dia não disponível."

    resultado = backtest.simular_stop_alternativo(
        ticks, alinhamento['datetime_entrada'], alinhamento['preco_entrada'],
        alinhamento['tipo_operacao'], float(stop_pontos),
        float(alvo_pontos) if alvo_pontos else None,
    )
    if resultado.get('saida') is None:
        return resultado.get('motivo', 'Sem dado suficiente.')
    return (f"Saída simulada: {resultado['saida']} @ {resultado['preco_saida']:.0f} "
            f"em {resultado['datetime_saida']} — resultado: {resultado['resultado_pontos']:+.0f} pts")


app.clientside_callback(
    # try/catch é proposital — achado real 2026-08-04: uma exceção aqui
    # dentro (ex.: LightweightCharts lançando por qualquer motivo) parecia
    # corromper a aplicação da resposta INTEIRA do callback Python que
    # disparou esta store (o Drawer às vezes nem abria, mesmo o servidor
    # devolvendo os dados certos) — dash-renderer processa as callbacks
    # clientside dependentes como parte da mesma leva de atualização.
    # Nunca deixar este renderizador quebrar o resto da resposta.
    """function(payload) {
        try {
            if (window.SQ_CHART_BACK) window.SQ_CHART_BACK.renderizar(payload);
        } catch (e) {
            console.error('[backtest] falha ao renderizar o gráfico de tick:', e);
        }
        return '';
    }""",
    Output('store-backtest-chart-dummy', 'data'),
    Input('store-backtest-chart-payload', 'data'),
)


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

# Ponte DMC (rollout TradeZella, 2026-07-31) — `dmc.MantineProvider` tem
# sistema de tema PRÓPRIO (`forceColorScheme`), independente do `C`/CSS
# vars já existente. Sem isso, componentes DMC (AppShell/NavLink/Card)
# ficariam sempre no esquema default enquanto o resto da página troca por
# CSS var — os dois sistemas precisam ouvir o MESMO `store-tema` (mesmo
# padrão já usado em `diario_app_classico.py`).
app.clientside_callback(
    "function(tema) { return tema || 'light'; }",
    Output('mantine-provider-dashboard', 'forceColorScheme'),
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


# Notificações (item 1D) — 1 callback por switch (`dmc.Switch` já expõe
# `checked` como fonte da verdade, sem precisar de className manual como
# o toggle CSS antigo do Modo Aprendizagem).
def _registrar_switch_notificacao(chave: str):
    @app.callback(
        Output(f'switch-notif-{chave}', 'checked'),
        Input(f'switch-notif-{chave}', 'checked'),
        prevent_initial_call=True,
    )
    def _callback(checked):
        config_operacional.definir_notificacao(chave, bool(checked))
        return no_update
    _callback.__name__ = f'alternar_notificacao_{chave}'


for _chave_notif in ('posicao_aberta', 'pendencia_diario', 'pendencia_coaching', 'sinal_quantlab'):
    _registrar_switch_notificacao(_chave_notif)


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
        return html.Div(_texto_com_icone('tabler:alert-triangle', f'Erro ao recalibrar: {str(e)[:150]}'),
                         style={'fontSize': '12px', 'color': C['red'], 'display': 'flex', 'alignItems': 'center', 'gap': '4px'})
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

        try:
            if MT5_OK:
                diario_trade.atualizar_qualidade_execucao_pendentes(limite=3)
        except Exception:
            # Mesma cautela do resto do ciclo: falha aqui (tick indisponível,
            # IPC instável) nunca pode derrubar o resto da sincronização.
            log_sync_posicoes.exception('Falha em atualizar_qualidade_execucao_pendentes')
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
        return no_update, no_update, no_update, _texto_com_icone('tabler:alert-triangle', 'Informe um preço.')
    try:
        preco_float = float(str(preco).replace(',', '.'))
    except ValueError:
        return no_update, no_update, no_update, _texto_com_icone('tabler:alert-triangle', 'Preço inválido.')
    tracados.criar_tracado(ativo, 'horizontal', preco_float)
    return ('', tracados.listar_tracados(ativo).to_dict('records'), None,
            _texto_com_icone('tabler:check', f'Linha criada em {preco_float:,.0f}.'))


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
        status = _texto_com_icone('tabler:alert-triangle', erro)
    elif df_ativo is None:
        status = _texto_com_icone('tabler:alert-triangle', 'Base não encontrada')
    else:
        status = f'● {len(df_ativo):,} candles'

    contrato_str = f'{c_win} · {c_wdo}'
    upd_str = f'Última atualização: {agora}'

    # Janela de notícia (Copom/FOMC/NFP) — não opera notícia, mas esses
    # horários distorcem a leitura de S/R (ver calendario_economico.py).
    cal_str, cal_style = '', {'fontSize': '10px', 'fontFamily': FONTS['display'], 'color': C['text2']}
    evento_ativo = cal_eco.janela_de_noticia_ativa(agora_dt, margem_minutos=30)
    if evento_ativo:
        cal_str = _texto_com_icone('tabler:alert-triangle', f"JANELA DE NOTÍCIA: {evento_ativo['nome']}", tamanho=11)
        cal_style = {**cal_style, 'color': C['red'], 'fontWeight': '700', 'display': 'flex', 'alignItems': 'center', 'gap': '4px'}
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
            html.Span(_icone('tabler:sparkles', tamanho=11, cor=C['gold']), style={'marginRight': '4px'}),
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
                    html.Span(_icone('tabler:clock', tamanho=13, cor=C['yellow']), style={'marginRight': '4px'}),
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
            status_str = _texto_com_icone('tabler:clock', f'Aguarde {segundos_espera}s · Limite da API atingido')

        elif isinstance(resposta_bruta, dict) and resposta_bruta.get('erro'):
            msg_ia = html.Div([
                html.Div([
                    html.Span(_texto_com_icone('tabler:alert-triangle', resposta_bruta["erro"]),
                              style={'color': C['red'], 'display': 'inline-flex', 'alignItems': 'center', 'gap': '4px'}),
                ], style={
                    'background': 'var(--bolha-erro)', 'borderRadius': '10px 10px 10px 2px',
                    'padding': '10px 14px', 'fontSize': '13px',
                    'fontFamily': FONTS['body'],
                    'maxWidth': '90%', 'borderLeft': f'2px solid {C["red"]}',
                }),
            ], style={'display': 'flex', 'justifyContent': 'flex-start'})
            tokens_str = ''
            status_str = _texto_com_icone('tabler:alert-triangle', 'Erro na API')

        else:
            resposta = resposta_bruta.get('texto', '')
            tokens_usados = resposta_bruta.get('tokens_usados', 0)
            tokens_max = cfg.GEMINI_MAX_TOKENS
            pct = round(tokens_usados / tokens_max * 100) if tokens_max > 0 else 0

            msg_ia = html.Div([
                html.Div([
                    html.Span(_icone('tabler:sparkles', tamanho=11, cor=C['gold']), style={'marginRight': '4px'}),
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
            html.Div(_texto_com_icone('tabler:alert-triangle', f'Erro inesperado: {str(e)[:60]}'),
                     style={'color': C['red'], 'fontSize': '13px',
                            'fontFamily': FONTS['body'], 'display': 'flex', 'alignItems': 'center', 'gap': '4px'}),
        ], style={'display': 'flex'})
        status_str = _texto_com_icone('tabler:alert-triangle', 'Erro inesperado')

    novo_historico = (historico_atual or []) + [msg_usuario, msg_ia]

    return novo_historico, status_str, '', None, ''


# -----------------------------------------------------------------------------
# MENU LATERAL — troca de seção real (rollout TradeZella, 2026-07-31)
# -----------------------------------------------------------------------------
# Navbar de duas colunas (trilha de ícones + painel de rótulos, ver
# `_GRUPOS_MENU` perto do `app.layout`). Mesma disciplina de sempre
# (seletor de ativo, abas antigas): nunca recriar o `dmc.NavLink`/botão de
# trilha, só trocar `active`/`style`/`display` (evita o bug histórico de
# reset de `n_clicks` documentado no CHANGELOG.md de 2026-07-04).


@app.callback(
    Output('store-aba-ativa', 'data'),
    Output('store-menu-grupo-aberto', 'data'),
    Output('store-menu-colapsado', 'data', allow_duplicate=True),
    [Input(f'rail-{grupo}', 'n_clicks') for grupo in _GRUPOS_CHAVES]
    + [Input(f'tab-{secao}', 'n_clicks') for secao in _SUBITENS_TODOS],
    State('store-menu-grupo-aberto', 'data'),
    State('store-menu-colapsado', 'data'),
    prevent_initial_call=True,
)
def selecionar_aba(*args):
    """Clique numa linha de grupo SEM sub-seções navega direto (mesmo
    comportamento de sempre). Clique numa linha de grupo COM sub-seções
    (hoje só Coaching) só abre/fecha a árvore inline — nunca navega
    sozinho (pedido explícito: "quando clicamos em cima do título, deve
    abrir a árvore de páginas") — EXCETO quando o menu está recolhido, aí
    o clique expande o menu inteiro e já entra na 1ª sub-seção, senão o
    clique não teria nenhum efeito visível (árvore ficaria "aberta" mas
    invisível, ícone-only). Clique numa sub-seção (`tab-{secao}`) sempre
    navega, nunca mexe na árvore."""
    *_n_clicks, grupos_abertos, colapsado_atual = args
    ctx = callback_context
    if not ctx.triggered:
        return no_update, no_update, no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    grupos_abertos = dict(grupos_abertos or {})
    if origem.startswith('rail-'):
        grupo = origem[len('rail-'):]
        secoes = _GRUPO_PARA_SECOES[grupo]
        if len(secoes) == 1:
            return secoes[0], no_update, no_update
        if colapsado_atual:
            grupos_abertos[grupo] = True
            return secoes[0], grupos_abertos, False
        grupos_abertos[grupo] = not grupos_abertos.get(grupo, False)
        return no_update, grupos_abertos, no_update
    return origem[len('tab-'):], no_update, no_update


@app.callback(
    [Output(f'tab-{secao}', 'active') for secao in _SUBITENS_TODOS],
    [Output(f'rail-{grupo}', 'style') for grupo in _GRUPOS_CHAVES],
    Input('store-aba-ativa', 'data'),
)
def atualizar_menu_ativo(aba_ativa):
    """Realça a linha do grupo ativo (cada grupo é 1 linha só agora, sem
    painel separado — ver `_linha_menu`) e a sub-seção ativa dentro da
    árvore de Coaching, se for o caso."""
    grupo_ativo = _SECAO_PARA_GRUPO.get(aba_ativa)
    ativos_sub = [secao == aba_ativa for secao in _SUBITENS_TODOS]
    estilos_linha = [_estilo_item_menu(grupo == grupo_ativo) for grupo in _GRUPOS_CHAVES]
    return (*ativos_sub, *estilos_linha)


@app.callback(
    [Output(f'arvore-{grupo}', 'className') for grupo in _GRUPOS_COM_SUB],
    [Output(f'chevron-{grupo}', 'className') for grupo in _GRUPOS_COM_SUB],
    Input('store-menu-grupo-aberto', 'data'),
    Input('store-menu-colapsado', 'data'),
)
def atualizar_arvore_menu(grupos_abertos, colapsado):
    """Chevron gira + árvore expande (CSS `grid-template-rows` 0fr→1fr,
    `.sq-arvore`/`.sq-arvore-aberta` em `assets/style.css`) conforme
    `store-menu-grupo-aberto`. Com o menu recolhido a árvore nunca aparece
    (mesmo que o Store diga "aberta") — `.sq-menu-recolhido .sq-arvore` já
    força `display:none` via CSS, então aqui só computamos a classe
    "aberta" normalmente, sem precisar ramificar em `colapsado` (o
    2º Input só existe pra reavaliar quando o menu abre/fecha, sem
    precisar recriar nada)."""
    grupos_abertos = grupos_abertos or {}
    classes_arvore = ['sq-arvore sq-arvore-aberta' if grupos_abertos.get(grupo) else 'sq-arvore'
                       for grupo in _GRUPOS_COM_SUB]
    classes_chevron = ['sq-menu-chevron sq-chevron-aberto' if grupos_abertos.get(grupo) else 'sq-menu-chevron'
                        for grupo in _GRUPOS_COM_SUB]
    return (*classes_arvore, *classes_chevron)


@app.callback(
    Output('appshell-principal', 'navbar'),
    Output('menu-coluna', 'className'),
    Output('store-menu-colapsado', 'data'),
    Input('btn-menu-colapsar', 'n_clicks'),
    State('store-menu-colapsado', 'data'),
    prevent_initial_call=True,
)
def alternar_menu_colapsado(n_clicks, colapsado_atual):
    """☰ — recolhe/expande o menu (pedido do Jesué: "menu retrátil").
    Só troca a `className` da coluna única (`.sq-menu-recolhido`/`.sq-
    menu-expandido`, animação CSS em `assets/style.css`) — o `navbar` do
    próprio `dmc.AppShell` também precisa encolher (`Output` direto no
    prop, substitui o dict inteiro), senão sobra um vão vazio entre os
    ícones e o conteúdo principal."""
    if not n_clicks:
        return no_update, no_update, no_update
    colapsado_novo = not bool(colapsado_atual)
    navbar = {'width': 52 if colapsado_novo else 260, 'breakpoint': 'sm'}
    classe_coluna = 'sq-menu-coluna ' + ('sq-menu-recolhido' if colapsado_novo else 'sq-menu-expandido')
    return navbar, classe_coluna, colapsado_novo


@app.callback(
    Output('indicador-menu-posicao', 'disabled'),
    Output('indicador-menu-historico', 'disabled'),
    Output('indicador-menu-coaching', 'disabled'),
    Output('indicador-notificacoes', 'disabled'),
    Input('interval-posicao-sync', 'n_intervals'),
    Input('interval-reconciliacao', 'n_intervals'),
)
def atualizar_badges_menu(_n_posicao, _n_reconciliacao):
    """Badges de pendência real no menu (item 1D) — nunca decorativo:
    Posição = há posição aberta agora (`estado['posicoes_abertas']`, já
    sincronizado por `sincronizar_posicoes`); Diário = pendência de
    reconciliação MT5×diário (`dt.resumo_reconciliacao`, mesmo dado do
    badge de Status); Coaching = check-in do dia ainda não preenchido
    hoje (`dt.obter_checkin_dia`). Os toggles de `config_operacional.
    notificacoes_config()` (Configurações) decidem se cada badge pode
    aparecer, mesmo havendo pendência real — usuário desligou, badge não
    acende. Sino acende se QUALQUER um dos 3 estiver ativo."""
    config_notif = config_operacional.notificacoes_config()

    with lock:
        posicoes = estado.get('posicoes_abertas')
    tem_posicao = posicoes is not None and len(posicoes) > 0
    mostrar_posicao = config_notif.get('posicao_aberta', True) and tem_posicao

    resumo_rec = dt.resumo_reconciliacao()
    pendencia_diario = bool(resumo_rec) and (
        resumo_rec['divergente'] + resumo_rec['sem_correspondencia_mt5']
        + resumo_rec['sem_correspondencia_diario'] > 0)
    mostrar_diario = config_notif.get('pendencia_diario', True) and pendencia_diario

    pendencia_coaching = dt.obter_checkin_dia(date.today()) is None
    mostrar_coaching = config_notif.get('pendencia_coaching', True) and pendencia_coaching

    mostrar_sino = mostrar_posicao or mostrar_diario or mostrar_coaching
    return not mostrar_posicao, not mostrar_diario, not mostrar_coaching, not mostrar_sino


@app.callback(
    Output('painel-dashboard', 'style'),
    Output('painel-quantlab', 'style'),
    Output('painel-diario', 'style'),
    Output('painel-back', 'style'),
    Output('painel-config', 'style'),
    Input('store-aba-ativa', 'data'),
)
def atualizar_painel_principal(aba_ativa):
    """`maxWidth`/`margin:auto` (2026-07-31, correção do Jesué: "veja essas
    margens muito largas") mora AQUI agora, por painel — antes era um
    único `maxWidth:1600px` em `conteudo-principal` (pai de TODOS os
    painéis), o que também espremia o Diário (grid/galeria querem a
    largura CHEIA da tela). Dashboard/Quant Lab/Backtesting/Configurações
    continuam centrados numa largura de leitura confortável; só o Diário
    (`_ABAS_DIARIO_FILHO`) fica sem esse teto."""
    def _estilo(mostrar, com_max_width=True):
        base = {'maxWidth': '1600px', 'margin': '0 auto'} if com_max_width else {}
        if not mostrar:
            base = {**base, 'display': 'none'}
        return base
    return (
        _estilo(aba_ativa == 'dashboard'),
        _estilo(aba_ativa == 'lab'),
        _estilo(aba_ativa in _ABAS_DIARIO_FILHO, com_max_width=False),
        _estilo(aba_ativa == 'back'),
        _estilo(aba_ativa == 'config'),
    )


# Ponte de seção pro iframe do Diário (Task #34, rollout TradeZella) —
# mesmo princípio de `assets/tema.js` (postMessage entre os 2 processos
# Dash, portas diferentes = origens diferentes, sem Store compartilhado):
# quando a seção ativa é uma das 6 que ainda moram no filho, avisa o
# iframe qual delas mostrar (o filho escreve em `store-diario-sub-aba`
# via `assets/secao_diario.js`, que troca a sub-aba renderizada por
# `montar_sub_aba_diario`). Guardar `window.SQ_SECAO.atual` permite ao
# filho pedir a seção atual de novo se o iframe recarregar depois do pai
# (mesmo "pedir" já usado pro tema).
app.clientside_callback(
    """
    function(secaoAtiva) {
        window.SQ_SECAO = window.SQ_SECAO || {};
        var secoesFilho = ['historico', 'estrategias', 'posicao', 'padroes', 'bem_estar'];
        if (secoesFilho.indexOf(secaoAtiva) === -1) return '';
        window.SQ_SECAO.atual = secaoAtiva;
        var iframe = document.querySelector('#painel-diario iframe');
        if (iframe) iframe.contentWindow.postMessage({tipo: 'sq-secao', secao: secaoAtiva}, '*');
        return '';
    }
    """,
    Output('store-secao-dummy', 'data'),
    Input('store-aba-ativa', 'data'),
)

# Ponte do filtro de data do calendário pro Histórico (rollout TradeZella,
# 2026-07-31) — ver `clicar_dia_calendario` (clique num dia do calendário
# do Dashboard) e `assets/secao_diario.js` (lado que recebe, no filho).
app.clientside_callback(
    """
    function(dataFiltro) {
        if (!dataFiltro) return '';
        var iframe = document.querySelector('#painel-diario iframe');
        if (iframe) iframe.contentWindow.postMessage({tipo: 'sq-hist-filtro-data', data: dataFiltro}, '*');
        return '';
    }
    """,
    Output('store-hist-filtro-dummy', 'data'),
    Input('store-historico-filtro-data-calendario', 'data'),
    prevent_initial_call=True,
)




# -----------------------------------------------------------------------------
# CALLBACKS DO DASHBOARD (ex-Estatísticas) — relocados de
# diario_app_classico.py (rollout TradeZella, 2026-07-31). Mesmos corpos,
# só registrados contra o `app` deste processo agora.
# -----------------------------------------------------------------------------

@app.callback(
    Output('diario-mentor-ia-conteudo', 'children'),
    Output('btn-gerar-mentor-ia', 'children'),
    Input('btn-gerar-mentor-ia', 'n_clicks'),
    prevent_initial_call=True,
)
def gerar_mentor_ia(n_clicks):
    """Gera (ou regenera) a análise do Mentor de IA. Bloqueante — mesma
    convenção do chat de IA (`enviar_mensagem_chat` em dashboard.py): não
    há um segundo round-trip, o callback só retorna quando a resposta real
    (ou erro) chega. `dcc.Loading` cobre a espera."""
    if not n_clicks:
        return no_update, no_update
    resumo = dt.montar_resumo_para_mentor_ia()
    resultado = ai_analyst.gerar_analise_evolucao_operacional(resumo)
    if resultado.get('erro'):
        conteudo = html.Div(_texto_com_icone('tabler:alert-triangle', resultado['erro']),
                             style={'fontSize': '12px', 'color': C['red'], 'display': 'flex', 'alignItems': 'center', 'gap': '4px'})
        return conteudo, no_update
    cache = ai_analyst.carregar_analise_diaria()
    return _conteudo_mentor_ia(cache), 'Gerar nova análise'


@app.callback(
    Output('store-aba-ativa', 'data', allow_duplicate=True),
    Input('btn-ver-mais-confiabilidade', 'n_clicks'),
    prevent_initial_call=True,
)
def navegar_para_estrategias(n_clicks):
    """"Ver mais" do ranking compacto de Confiabilidade por Estratégia
    (2026-08-05, item 18 do upgrade) — a tabela completa/interativa (grid
    + detalhe expandido por linha, com o clique-na-linha que antes vivia
    aqui em `atualizar_detalhe_estrategia_grid`) migrou pra
    `diario_app_classico.py::_construir_diario_estrategias`. Só navega
    pra lá, mesmo mecanismo de `rail-{grupo}`/`tab-{secao}`
    (`Output('store-aba-ativa', ...)`), com `allow_duplicate=True` porque
    `selecionar_aba` já é dono desse Output."""
    if not n_clicks:
        return no_update
    return 'estrategias'


@app.callback(
    Output('diario-calendario-mes', 'data'),
    Input('btn-diario-cal-mes-prev', 'n_clicks'),
    Input('btn-diario-cal-mes-next', 'n_clicks'),
    Input('btn-diario-cal-ano-prev', 'n_clicks'),
    Input('btn-diario-cal-ano-next', 'n_clicks'),
    State('diario-calendario-mes', 'data'),
    prevent_initial_call=True,
)
def navegar_calendario_classico(n_mes_prev, n_mes_next, n_ano_prev, n_ano_next, mes_atual):
    """Setas de mês e de ano são controles TOTALMENTE separados (feedback
    2026-07-12: um único par de setas reaproveitado pros dois modos —
    trocando de significado conforme `diario-modo-ano` — fazia o usuário
    clicar esperando andar um mês e o calendário pular um ano inteiro,
    3 vezes reportado mesmo depois de rotular os botões dinamicamente. A
    seta de mês SEMPRE anda mês a mês (com rollover de ano) e a de ano
    SEMPRE anda ano a ano, não importa qual grade (`diario-modo-ano`) está
    sendo exibida — `modo_ano` não é mais lido aqui, só decide o que
    `atualizar_periodo_estatisticas` desenha embaixo."""
    ctx = callback_context
    if not ctx.triggered or not mes_atual:
        return no_update
    trigger = ctx.triggered[0]
    if not trigger['value']:
        # Um clique de verdade SEMPRE incrementa `n_clicks` (nunca zera
        # sozinho) — um trigger com valor 0 só pode vir do próprio botão
        # tendo sido recriado (Dash reseta `n_clicks` de um componente
        # novo pra 0), nunca de uma ação do usuário. Guarda defensiva:
        # com os botões agora fixos (nunca recriados, ver
        # `_construir_diario_estatisticas`) isso não deveria mais
        # acontecer, mas custa nada blindar contra o mesmo bug voltando
        # se algum código futuro recriar os botões de novo por engano.
        return no_update
    ano, mes = (int(x) for x in mes_atual.split('-'))
    origem = trigger['prop_id'].split('.')[0]
    if origem in ('btn-diario-cal-mes-prev', 'btn-diario-cal-mes-next'):
        passo = -1 if origem == 'btn-diario-cal-mes-prev' else 1
        mes += passo
        if mes == 0:
            mes, ano = 12, ano - 1
        elif mes == 13:
            mes, ano = 1, ano + 1
    else:
        passo = -1 if origem == 'btn-diario-cal-ano-prev' else 1
        ano += passo
    return f"{ano:04d}-{mes:02d}"


@app.callback(
    Output('diario-modo-ano', 'data'),
    Input('btn-diario-modo-ano', 'n_clicks'),
    State('diario-modo-ano', 'data'),
    prevent_initial_call=True,
)
def alternar_modo_ano(n_clicks, modo_atual):
    if not n_clicks:  # ver comentário em navegar_calendario_classico
        return no_update
    return not bool(modo_atual)


@app.callback(
    Output('diario-cal-config-conteudo', 'children'),
    Output('btn-diario-cal-config', 'children'),
    Input('btn-diario-cal-config', 'n_clicks'),
    State('btn-diario-cal-config', 'children'),
    State('store-calendario-stats-visiveis', 'data'),
    prevent_initial_call=True,
)
def alternar_config_calendario(n_clicks, rotulo_atual, stats_visiveis):
    """Abre/fecha o painel "Display stats" do calendário (item F, fase 4)
    — mesma mecânica do checklist pré-trade: o próprio rótulo do botão é
    o estado, nunca recriado por esta callback. Rótulo texto puro ("Stats"/
    "Fechar", sem glifo ⚙/✕) desde 2026-08-01."""
    if not n_clicks:
        return no_update, no_update
    if rotulo_atual == 'Stats':
        return _conteudo_config_calendario(stats_visiveis or _STATS_CALENDARIO_PADRAO), 'Fechar'
    return None, 'Stats'


@app.callback(
    Output('colapso-analise-estrategia-detalhes', 'style'),
    Output('btn-analise-estrategia-detalhes', 'children'),
    Input('btn-analise-estrategia-detalhes', 'n_clicks'),
    State('btn-analise-estrategia-detalhes', 'children'),
    prevent_initial_call=True,
)
def alternar_detalhes_analise_estrategia(n_clicks, rotulo_atual):
    """Abre/fecha os detalhes de "Análise de Estratégia" (2026-07-31,
    feedback: "deixe Vencedora/Perdedora e Mentor de IA visíveis, o resto
    contraído") — mesma mecânica de `alternar_config_calendario`/
    `alternar_modo_ano`: o próprio rótulo do botão (texto "Ver detalhes"/
    "Ocultar detalhes" — sem seta Unicode desde 2026-08-01) é o estado,
    só o `style` (`display`) alterna, conteúdo sempre presente no DOM
    (ver docstring de `_bloco_analise_estrategia` — `dmc.Collapse` tem bug
    real de não abrir via callback nesta versão, descartado)."""
    if not n_clicks:
        return no_update, no_update
    if rotulo_atual == 'Ver detalhes':
        return {'display': 'block'}, 'Ocultar detalhes'
    return {'display': 'none'}, 'Ver detalhes'


@app.callback(
    Output('store-calendario-stats-visiveis', 'data'),
    Input('diario-cal-config-itens', 'value'),
    prevent_initial_call=True,
)
def atualizar_stats_visiveis_calendario(valores):
    return valores or []


@app.callback(
    Output('diario-cards-conteudo', 'children'),
    Output('diario-calendario-grid', 'children'),
    Output('diario-calendario-label-mes', 'children'),
    Output('diario-calendario-label-ano', 'children'),
    Output('btn-diario-modo-ano', 'style'),
    Output('diario-modo-ano-bolinha', 'style'),
    Output('diario-resto-periodo-conteudo', 'children'),
    Input('diario-calendario-mes', 'data'),
    Input('diario-modo-ano', 'data'),
    Input('store-calendario-stats-visiveis', 'data'),
    Input('diario-estat-ativo', 'value'),
    State('store-tema', 'data'),
)
def atualizar_periodo_estatisticas(mes_atual, modo_ano, stats_visiveis, ativo, tema_atual):
    """Recalcula estatísticas + calendário a cada troca de mês/ano/toggle/
    ativo — ver `diario_app_98.py::atualizar_periodo_estatisticas_98` pro
    raciocínio completo. Só atualiza `children`/`style` de componentes que já
    existem (criados 1x em `_construir_diario_estatisticas`) — NUNCA
    substitui os botões de navegação inteiros, pra não repetir o bug de
    `n_clicks` resetando (ver comentário lá). Os labels de mês/ano são
    atualizados SEMPRE, independente de `modo_ano` — as setas de mês e ano
    ficam visíveis e funcionais nos dois modos de grade (ver
    `navegar_calendario_classico`), só a grade abaixo (dias vs meses) muda.

    `store-calendario-stats-visiveis` (item F, fase 4) também é Input —
    marcar/desmarcar uma stat no calendário precisa remontar a grade na
    hora, sem esperar trocar de mês.

    `diario-estat-ativo` (Fase 4 da integração cTrader, 2026-08-06) filtra
    tudo pra um único ativo — `None`/`''` (opção "Todos") preserva o
    comportamento de sempre (consolidado, sempre BRL)."""
    if not mes_atual:
        return (no_update,) * 7
    ano, mes = (int(x) for x in mes_atual.split('-'))
    modo_ano = bool(modo_ano)
    ativo = ativo or None
    if modo_ano:
        stats = dt.calcular_estatisticas(ano=ano, ativo=ativo)
        grade = _grade_calendario_anual(stats['por_mes'], ano, tema_atual or 'light', stats.get('moeda', 'BRL'))
    else:
        stats = dt.calcular_estatisticas(ano=ano, mes=mes, ativo=ativo)
        detalhe_dias = dt.detalhe_dias_calendario(ano=ano, mes=mes, ativo=ativo)
        grade = _grade_calendario(stats['por_dia'], detalhe_dias, ano, mes, stats_visiveis, tema_atual or 'light',
                                   stats.get('moeda', 'BRL'))
    return (
        _cards_ou_vazio_estatisticas(stats, tema_atual or 'light', ano=ano, mes=None if modo_ano else mes,
                                      ativo=ativo),
        grade,
        _NOMES_MES[mes],
        str(ano),
        _estilo_toggle_trilho(modo_ano),
        _estilo_toggle_bolinha(modo_ano),
        _resto_periodo_estatisticas(stats, tema_atual or 'light'),
    )


# Output mudou de `store-diario-sub-aba` (Store do FILHO) pra
# `store-aba-ativa` (Store do PAI) + um novo `store-historico-filtro-
# data-calendario` — a Estatística (Dashboard) e o Histórico agora vivem
# em PROCESSOS Dash diferentes (rollout TradeZella, 2026-07-31), então uma
# única callback Python não pode mais escrever direto nos dois; o
# clientside_callback da ponte de seção (perto de `atualizar_painel_
# principal`) repassa a data pro iframe via postMessage.
@app.callback(
    Output('store-aba-ativa', 'data', allow_duplicate=True),
    Output('store-historico-filtro-data-calendario', 'data'),
    Input({'type': 'diario-cal-dia', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True,
)
def clicar_dia_calendario(n_clicks_lista):
    ctx = callback_context
    if not ctx.triggered or not any(n_clicks_lista or []):
        return no_update, no_update
    prop_id = ctx.triggered[0]['prop_id'].rsplit('.', 1)[0]
    componente_id = json.loads(prop_id)
    return 'historico', componente_id['index']


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
