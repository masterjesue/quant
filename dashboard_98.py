# =============================================================================
# dashboard_98.py — Super Quant '98
# =============================================================================
# Segundo front-end do Super Quant — visual Windows 98 (metáfora de desktop
# completa: janelas, taskbar, Start, ícones). Roda em paralelo ao dashboard.py
# clássico (dark mode), cada um em sua porta, compartilhando toda a lógica de
# dados/negócio (persistencia.py, mt5_loader.py, features.py, metricas.py,
# ai_analyst.py, calendario_economico.py) — só a camada visual é própria.
#
# Roda com: python dashboard_98.py
# Abre no browser em: http://127.0.0.1:8051
#
# Ao abrir, executa automaticamente a atualização incremental (busca candles
# e ticks novos desde a última sessão) — sem nenhuma ação manual do usuário.
# =============================================================================

import sys
import base64
import re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import dash
from dash import dcc, html, Input, Output, State, callback_context, no_update
import flask
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
import threading
import json
from datetime import datetime

import config_backtest as cfg
import persistencia
import run_backtest
import mt5_loader
import calendario_economico as cal_eco
import ai_analyst
import diario_trade as dt

# Flag: MT5 disponível
try:
    import MetaTrader5 as mt5
    MT5_OK = True
except ImportError:
    MT5_OK = False

# =============================================================================
# PALETA E TOKENS — tema Windows 98 (ver estilo_win98.py)
# =============================================================================

from estilo_win98 import (
    C, FONTS, win98_raised, win98_sunken, win98_button, win98_field,
    win98_titlebar, win98_window, win98_groupbox, texto_sobre,
    win98_desktop_icon, win98_start_menu_item, win98_taskbar, JANELAS,
)

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


def gauge_cockpit(valor_norm: float, label: str, sub: str, cor: str) -> go.Figure:
    """
    Velocímetro estilo cockpit aeronáutico — arco angular com agulha.
    valor_norm: 0.0 (mínimo) a 1.0 (máximo), 0.5 = neutro.
    """
    ang_min, ang_max = 225, -45  # graus, sentido horário no SVG
    ang = ang_min + (ang_max - ang_min) * valor_norm

    fig = go.Figure()

    # Arco de fundo (zona vermelha → amarela → verde)
    for i, (a_start, a_end, c) in enumerate([
        (225, 165, C['red']),
        (165, 105, C['yellow']),
        (105, 45,  C['green']),
        (45,  -45, C['green']),
    ]):
        thetas = np.linspace(np.radians(a_start), np.radians(a_end), 30)
        x_out = [0.85 * np.cos(t) for t in thetas]
        y_out = [0.85 * np.sin(t) for t in thetas]
        x_in  = [0.65 * np.cos(t) for t in thetas][::-1]
        y_in  = [0.65 * np.sin(t) for t in thetas][::-1]
        fig.add_trace(go.Scatter(
            x=x_out + x_in, y=y_out + y_in,
            fill='toself', fillcolor=c,
            line=dict(width=0), opacity=0.85,  # Win98 é chapado, sem glassmorphism
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
        mode='lines', line=dict(color=C['text'], width=3),
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
                       font=dict(family=FONTS['display'], size=11, color=C['text2']),
                       showarrow=False)

    fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(visible=False, range=[-1.1, 1.1]),
        yaxis=dict(visible=False, range=[-1.1, 1.1], scaleanchor='x'),
        height=200,
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
    corpo = html.Div([
        html.Div(valor, style={
            'fontSize': '20px', 'fontWeight': '700',
            'fontFamily': FONTS['mono'], 'color': cor,
            'lineHeight': '1.1',
        }),
        html.Div(sub, style={
            'fontSize': '11px', 'color': C['text2'],
            'fontFamily': FONTS['display'], 'marginTop': '4px',
        }) if sub else None,
    ])
    return win98_groupbox(titulo, corpo, estilo_extra={'minWidth': largura, 'height': altura})


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

from metricas import extrair_metricas


# =============================================================================
# CONSTRUÇÃO DOS BLOCOS DO DASHBOARD
# =============================================================================

def construir_hero(m_win: dict, m_wdo: dict, ativo: str) -> html.Div:
    """Seção hero: velocímetros de força e contexto do ativo selecionado.

    Não inclui mais o seletor WIN/WDO/WIN×WDO — esse bloco virou estático em
    app.layout (ver `_estilo_botao_ativo`) porque esta função é reconstruída
    a cada 1s (`atualizar_dashboard`), e recriar os `html.Button` com
    `n_clicks=0` a cada tick zerava o contador de cliques no browser — causa
    raiz do bug "troca pra WDO e volta sozinho pro WIN" (ver CHANGELOG.md
    2026-07-04).
    """
    m = m_win if ativo == 'WIN' else m_wdo

    # Cores dos gauges
    sf = m.get('score_forca', 0.5)
    sc = m.get('score_contexto', 0.5)
    # C['amber'] (não C['yellow']) no neutro — o amarelo puro aparece como
    # texto/traço fino em cima de fundo branco/cinza claro (ver Feedback UX
    # SuperQuant98.txt) e fica quase ilegível ali.
    cor_forca   = C['green'] if sf > 0.6 else (C['red'] if sf < 0.4 else C['amber'])
    cor_ctx     = C['green'] if sc > 0.6 else (C['red'] if sc < 0.4 else C['amber'])

    label_forca = 'COMPRADORES' if sf > 0.6 else ('VENDEDORES' if sf < 0.4 else 'NEUTRO')
    label_ctx   = 'FAVORÁVEL' if sc > 0.6 else ('DESFAVORÁVEL' if sc < 0.4 else 'NEUTRO')

    return html.Div([
        html.Div(dcc.Graph(id='gauge-forca',
                  figure=gauge_cockpit(sf, 'FORÇA', label_forca, cor_forca),
                  config={'displayModeBar': False},
                  style={'width': '200px'}), style=win98_sunken(padding='4px')),
        html.Div(dcc.Graph(id='gauge-contexto',
                  figure=gauge_cockpit(sc, 'CONTEXTO', label_ctx, cor_ctx),
                  config={'displayModeBar': False},
                  style={'width': '200px'}), style=win98_sunken(padding='4px')),
    ], style={'display': 'flex', 'gap': '8px', 'alignItems': 'center'})


def _estilo_botao_ativo(nome: str, ativo_atual: str) -> dict:
    """Estilo do botão seletor de ativo (WIN/WDO/CORRELACAO) dado o ativo
    selecionado. Extraído do antigo `construir_hero` para ser reutilizado
    tanto no layout estático inicial quanto no callback dedicado
    `atualizar_estilo_ativo_selecionado` — o `html.Button` em si nunca é
    recriado, só o `style` muda."""
    return win98_button(pressed=(ativo_atual == nome))


def construir_market_view(m: dict) -> html.Div:
    """Cards de leitura rápida — cada card mostra número, conclusão e janela temporal."""

    def _card_mv(titulo, numero, conclusao, detalhe, cor):
        """Card com três camadas: número bruto, conclusão interpretada, contexto/janela."""
        corpo = html.Div([
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
        ])
        return win98_groupbox(titulo, corpo, cor_led=cor,
                               estilo_extra={'minWidth': '160px', 'flex': '1'})

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
            cor_qm = C['amber']

        detalhe_qm = f'Delta {delta_sinal}{delta_acum:,} · {janela_n} candles · {fluxo_periodo}'
    else:
        numero_qm = '—'
        conclusao_qm = 'Tick não disponível neste período'
        detalhe_qm = 'Fluxo calculado só onde há dado de tick'
        cor_qm = C['text2']

    card_qm = _card_mv('Quem Manda', numero_qm, conclusao_qm, detalhe_qm, cor_qm)

    # --- CARD 2: TENDÊNCIA
    tend   = m.get('tendencia', '—')
    fase   = m.get('fase', '—')
    saude  = m.get('saude', '—')
    cor_tend = C['green'] if tend == 'alta' else (C['red'] if tend == 'baixa' else C['amber'])

    mapa_conclusao_tend = {
        ('alta',  'inicio',   'forte'):  'Alta nascente — impulso acelerando',
        ('alta',  'inicio',   'normal'): 'Alta em início — momentum positivo',
        ('alta',  'meio',     'forte'):  'Alta saudável — pullbacks rasos',
        ('alta',  'meio',     'normal'): 'Alta em andamento — saúde normal',
        ('alta',  'exaustao', 'fraca'):  'Alta em exaustão — cuidado com reversão',
        ('baixa', 'inicio',   'forte'):  'Baixa nascente — impulso vendedor forte',
        ('baixa', 'inicio',   'normal'): 'Baixa em início — momentum vendedor',
        ('baixa', 'meio',     'forte'):  'Baixa saudável — resistências cedem',
        ('baixa', 'meio',     'normal'): 'Baixa em andamento — saúde normal',
        ('baixa', 'exaustao', 'fraca'):  'Baixa em exaustão — possível absorção',
        ('lateral', 'indefinido', 'indefinido'): 'Mercado lateral — operar nas bordas',
    }
    conclusao_tend = mapa_conclusao_tend.get(
        (tend, fase, saude),
        f'{tend.upper()} · {fase} · {saude}'
    )
    detalhe_tend = f'Fase: {fase} · Saúde: {saude} · últimos 500 candles'

    card_tend = _card_mv('Tendência', tend.upper(), conclusao_tend, detalhe_tend, cor_tend)

    # --- CARD 3: VOLATILIDADE
    amp_rel = m.get('amp_dia_atr', np.nan)
    atr_val = m.get('atr', np.nan)
    def _nan(v): return isinstance(v, float) and np.isnan(v)

    if not _nan(amp_rel):
        if amp_rel >= 1.5:
            numero_vol = f'{amp_rel:.1f}× ATR'
            conclusao_vol = 'Volatilidade alta — amplitude acima do normal'
            cor_vol = C['amber']
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
    cor_dp = {'alta': C['green'], 'media': C['amber'],
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

    # Sem secao('MARKET VIEW') aqui — renderizado dentro da janela Quant Lab
    # (ver 'market-view-conteudo' em app.layout), sem título próprio.
    return html.Div([card_qm, card_tend, card_vol, card_bu, card_dp, card_jan],
                     style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'})


def construir_price_action(df: pd.DataFrame, m: dict) -> html.Div:
    """
    Seção Price Action — sem gráfico de candles (Jesué já tem o seu no ProfitPRO).
    Mostra os N_NIVEIS_SR (default 3) níveis de S/R de cada lado — ordenados
    por RELEVÂNCIA (nível forte + toques + recência do último toque, não
    mais só proximidade — ver _rankear_niveis_sr em features.py, mudado em
    2026-07-08 depois de um caso real onde o nível mais próximo era uma
    zona genérica sem repetição real, enquanto um suporte bem mais forte
    ficava mais abaixo no ranking só por estar mais longe).

    Abaixo dos 3 níveis rankeados, se houver, mostra também o "pivô
    imediato" (último fundo/topo confirmado, ainda sem reteste — ver
    calcular_pivo_imediato em features.py): referência tática de curtíssimo
    prazo que só some quando NÃO está redundante com um dos níveis já
    rankeados acima.
    """
    preco = m.get('preco', np.nan)
    preco_fmt = _fmt(preco, 0)

    tend   = m.get('tendencia', '—')
    fase   = m.get('fase', '—')
    saude  = m.get('saude', '—')
    candle = str(m.get('tipo_candle', '—')).replace('_', ' ').title()

    def _nan(v): return v is None or (isinstance(v, float) and np.isnan(v))

    def _proximidade(dist_atr):
        if _nan(dist_atr): return 'distância não calculada'
        if dist_atr <= 0.3: return 'muito próximo'
        if dist_atr <= 1.0: return f'{dist_atr:.1f} ATR de distância'
        return f'{dist_atr:.1f} ATR — longe'

    def _leitura_nivel(toques, eh_resistencia, prob_bounce):
        """Leitura interpretada do nível, incorporando a % de probabilidade calculada."""
        prob_txt = f'{prob_bounce:.0f}%' if not _nan(prob_bounce) else '—'
        romp_txt = f'{100-prob_bounce:.0f}%' if not _nan(prob_bounce) else '—'
        if tend == 'baixa':
            if eh_resistencia:
                return f'Em tendência de baixa ({fase}), probabilidade estimada de {prob_txt} de rejeição ' \
                       f'nesta resistência (vs. {romp_txt} de rompimento). Candle de força vendedora aqui é trigger potencial.'
            else:
                return f'Suporte sob pressão — em tendência de baixa, suportes tendem a ceder. ' \
                       f'Probabilidade estimada de {prob_txt} de bounce. Observe rompimento com volume e candle forte.'
        elif tend == 'alta':
            if eh_resistencia:
                return f'Resistência em tendência de alta, saúde {saude}. Probabilidade estimada de {romp_txt} ' \
                       f'de rompimento com continuidade em fase de {fase} (vs. {prob_txt} de rejeição).'
            else:
                return f'Suporte em tendência de alta, saúde {saude}. Probabilidade estimada de {prob_txt} de bounce. ' \
                       f'Procure candle de reversão ou pin comprador.'
        else:
            return f'Mercado lateral — S/R em lateralização tem comportamento de range. ' \
                   f'Probabilidade estimada de {prob_txt} de reação nesta borda. Operar rejeições, não rompimentos.'

    _STATUS_LABEL = {
        'aguardando_pullback': ('AGUARDANDO PULLBACK', C['yellow']),
        'aguardando_throwback': ('AGUARDANDO THROWBACK', C['yellow']),
        'rompida': ('ROMPIDA', C['text2']),
        'invalidada': ('INVALIDADA', C['text2']),
    }

    def _card_nivel_sr(nivel: dict, eh_resistencia: bool) -> html.Div:
        """Um card por nível rankeado — rank, preço, zona, toques/idade, badges, leitura com %."""
        cor = C['red'] if eh_resistencia else C['green']
        destaque = nivel.get('forte', False)
        prob = nivel.get('prob_bounce', np.nan)
        toques = nivel.get('toques', 0)
        idade = nivel.get('idade', 0)
        zona_min, zona_max = nivel.get('zona_min'), nivel.get('zona_max')
        zona_txt = f'{_fmt(zona_min,0)}–{_fmt(zona_max,0)}' if not (_nan(zona_min) or _nan(zona_max)) else '—'

        badges = []
        if destaque:
            badges.append(html.Span('REGIÃO FORTE', style={
                'fontSize': '9px', 'fontWeight': '700', 'color': texto_sobre(C['gold']),
                'background': C['gold'], 'borderRadius': '0',
                'padding': '2px 6px', 'marginRight': '6px', 'letterSpacing': '0.05em',
            }))
        if nivel.get('violado'):
            badges.append(html.Span('VIOLADO', style={
                'fontSize': '9px', 'fontWeight': '700', 'color': C['text'],
                'background': C['red'], 'borderRadius': '0',
                'padding': '2px 6px', 'marginRight': '6px', 'letterSpacing': '0.05em',
            }))
        status = nivel.get('status', 'ativa')
        if status in _STATUS_LABEL:
            label, cor_status = _STATUS_LABEL[status]
            badges.append(html.Span(label, style={
                'fontSize': '9px', 'fontWeight': '700', 'color': texto_sobre(cor_status),
                'background': cor_status, 'borderRadius': '0',
                'padding': '2px 6px', 'marginRight': '6px', 'letterSpacing': '0.05em',
            }))
        if not _nan(nivel.get('zona_espelho_id')):
            badges.append(html.Span('ZONA ESPELHO', style={
                'fontSize': '9px', 'fontWeight': '700', 'color': texto_sobre(C['blue']),
                'background': C['blue'], 'borderRadius': '0',
                'padding': '2px 6px', 'marginRight': '6px', 'letterSpacing': '0.05em',
            }))
        # Sinal intradiário — escopo do próprio pregão, independente do status
        # estrutural acima (ver comentário em features.py::calcular_zonas_sr).
        romp_hoje = nivel.get('rompimento_hoje', 'nenhum')
        if romp_hoje == 'rompeu_hoje':
            badges.append(html.Span('ROMPEU HOJE', style={
                'fontSize': '9px', 'fontWeight': '700', 'color': texto_sobre(C['yellow']),
                'background': C['yellow'], 'borderRadius': '0',
                'padding': '2px 6px', 'marginRight': '6px', 'letterSpacing': '0.05em',
            }))
        elif romp_hoje == 'pullback_confirmado_hoje':
            badges.append(html.Span('PULLBACK CONFIRMADO HOJE', style={
                'fontSize': '9px', 'fontWeight': '700', 'color': texto_sobre(C['green']),
                'background': C['green'], 'borderRadius': '0',
                'padding': '2px 6px', 'marginRight': '6px', 'letterSpacing': '0.05em',
            }))

        borda_cor = C['gold'] if destaque else cor

        corpo = html.Div([
            html.Div([
                html.Span(f'#{nivel["rank"]} ', style={
                    'fontSize': '10px', 'color': C['text2'], 'fontFamily': FONTS['mono'],
                }),
                html.Span(_fmt(nivel['preco'], 0), style={
                    'fontSize': '20px', 'fontWeight': '700',
                    'fontFamily': FONTS['mono'], 'color': cor,
                }),
                html.Span(f'  zona {zona_txt}', style={
                    'fontSize': '11px', 'color': C['text2'], 'fontFamily': FONTS['display'],
                }),
            ], style={'marginBottom': '4px'}),
            html.Div(badges, style={'marginBottom': '6px'}) if badges else html.Div(),
            html.Div(
                f'{toques} toque{"s" if toques != 1 else ""} · idade {idade} candles · '
                f'{_proximidade(nivel.get("dist_atr"))}',
                style={'fontSize': '12px', 'color': C['text2'], 'fontFamily': FONTS['display'], 'marginBottom': '6px'}
            ),
            html.Div(
                f'Probabilidade: {prob:.0f}% bounce · {100-prob:.0f}% rompimento' if not _nan(prob) else 'Probabilidade: —',
                style={'fontSize': '12px', 'fontWeight': '600', 'color': cor,
                       'fontFamily': FONTS['display'], 'marginBottom': '6px'}
            ),
            html.Div(_leitura_nivel(toques, eh_resistencia, prob), style={
                'fontSize': '12px', 'color': C['text'],
                'fontFamily': FONTS['body'], 'lineHeight': '1.5',
                'borderLeft': f'3px solid {cor}', 'paddingLeft': '10px',
            }),
        ])
        return win98_groupbox(None, corpo, cor_led=borda_cor, estilo_extra={'marginBottom': '10px'})

    def _card_pivo_imediato(pivo: dict, eh_resistencia: bool) -> html.Div:
        """Linha discreta (moldura afundada, sem groupbox) pro pivô imediato — ver docstring da função pai."""
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
        ], style=win98_sunken(padding='8px 12px', extra={'marginBottom': '10px'}))

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
                'color': C['green'] if tend=='alta' else (C['red'] if tend=='baixa' else C['amber']),
            }),
            html.Div(f'{fase} · {saude}', style={
                'fontSize': '12px', 'color': C['text2'],
                'fontFamily': FONTS['display'], 'marginTop': '2px',
            }),
        ], style={'flex': '1'}),
    ], style={
        **win98_sunken(padding='16px'),
        'display': 'flex', 'gap': '24px', 'marginBottom': '12px',
    })

    niveis_acima = m.get('niveis_sr_acima', [])
    niveis_abaixo = m.get('niveis_sr_abaixo', [])

    if niveis_acima:
        cards_resistencia = [
            html.Div('RESISTÊNCIAS MAIS PRÓXIMAS', style={
                'fontSize': '10px', 'color': C['red'], 'fontFamily': FONTS['display'],
                'letterSpacing': '0.08em', 'marginBottom': '8px',
            }),
        ] + [_card_nivel_sr(n, eh_resistencia=True) for n in niveis_acima]
    else:
        cards_resistencia = [html.Div('Sem resistência identificada.', style={'color': C['text2'], 'fontSize': '12px'})]

    if niveis_abaixo:
        cards_suporte = [
            html.Div('SUPORTES MAIS PRÓXIMOS', style={
                'fontSize': '10px', 'color': C['green'], 'fontFamily': FONTS['display'],
                'letterSpacing': '0.08em', 'marginBottom': '8px',
            }),
        ] + [_card_nivel_sr(n, eh_resistencia=False) for n in niveis_abaixo]
    else:
        cards_suporte = [html.Div('Sem suporte identificado.', style={'color': C['text2'], 'fontSize': '12px'})]

    pivo_acima = m.get('pivo_imediato_acima')
    if pivo_acima:
        cards_resistencia.append(_card_pivo_imediato(pivo_acima, eh_resistencia=True))
    pivo_abaixo = m.get('pivo_imediato_abaixo')
    if pivo_abaixo:
        cards_suporte.append(_card_pivo_imediato(pivo_abaixo, eh_resistencia=False))

    # Sem secao('PRICE ACTION') aqui — o título já vem da titlebar da janela
    # (win98_window('win-price-action', 'Price Action', ...) no app.layout).
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


def construir_big_players(df: pd.DataFrame) -> html.Div:
    """
    Big Players Footprints — análise interpretada do que está por trás do gráfico.
    Não mostra tape bruto — mostra conclusões acionáveis sobre comportamento institucional.
    Baseado nos dados de fluxo (delta, vol_comprador, vol_vendedor) já calculados.
    """
    if df is None or df.empty:
        return html.Div('Sem dados.', style={'color': C['text2']})

    # Trabalha com os últimos 500 candles (snapshot de análise)
    snap = df.tail(500).copy().reset_index(drop=True)
    hoje = snap[snap['datetime'].dt.date == pd.Timestamp.now().date()]
    tem_fluxo = (snap['fluxo_disponivel'] == True) if 'fluxo_disponivel' in snap.columns else pd.Series([False]*len(snap))
    snap_fluxo = snap[tem_fluxo].reset_index(drop=True)

    def _card_bp(titulo, valor, conclusao, detalhe, cor):
        corpo = html.Div([
            html.Div(valor, style={
                'fontSize': '16px', 'fontWeight': '700',
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
        ])
        return win98_groupbox(titulo, corpo, cor_led=cor,
                               estilo_extra={'flex': '1', 'minWidth': '200px'})

    if snap_fluxo.empty:
        # Sem dado de tick — análise só por anatomia de candle
        # Sem secao('BIG PLAYERS FOOTPRINTS') aqui — o título já vem da
        # titlebar da janela (win98_window('win-big-players', 'Big Players', ...)).
        return html.Div([
            html.Span('📊 ', style={'fontSize': '16px'}),
            html.Span(
                'Fluxo de tick não disponível neste período. '
                'A análise de big players requer dado de tick — disponível apenas '
                'para os contratos mais recentes. '
                'Análise de anatomia de candle disponível no Price Action.',
                style={'fontSize': '12px', 'color': C['text2'],
                       'fontFamily': FONTS['body']}
            ),
        ], style=win98_sunken(padding='14px', extra={'display': 'flex', 'alignItems': 'flex-start'}))

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
        cor_lado = C['green']
    elif pct_v > 60:
        lado_dominante = f'Vendedores {pct_v}%'
        conclusao_lado = 'Agressão vendedora dominante — pressão de venda real'
        cor_lado = C['red']
    else:
        lado_dominante = f'C {pct_c}% · V {pct_v}%'
        conclusao_lado = 'Mercado equilibrado — sem dominância clara de um lado'
        cor_lado = C['amber']

    detalhe_lado = (f'Delta acum: {int(delta_acum):+,} · {len(ultimos_fluxo)} candles com tick · '
                    f'{pct_diverg}% de divergência candle/delta')

    card_lado = _card_bp('Lado Dominante', lado_dominante, conclusao_lado, detalhe_lado, cor_lado)

    # -------------------------------------------------------------------------
    # ANÁLISE 2 — ABSORÇÃO (sinal de big player comprando/vendendo contra o movimento)
    # -------------------------------------------------------------------------
    # Absorção: candles de baixa mas compradores agredindo (big player comprando na queda)
    # ou candles de alta mas vendedores agredindo (big player vendendo na alta)
    absorcao_compra  = ((dir_col == -1) & (delta_col > 0)).sum()  # compra em queda
    absorcao_venda   = ((dir_col == 1)  & (delta_col < 0)).sum()  # venda em alta
    total_candles_f  = len(ultimos_fluxo)

    if absorcao_compra > total_candles_f * 0.3:
        abs_valor = f'{absorcao_compra} candles'
        abs_conclusao = 'Absorção compradora detectada — big player comprando na queda'
        abs_detalhe = 'Candles de baixa com delta positivo indicam demanda institucional oculta'
        abs_cor = C['green']
    elif absorcao_venda > total_candles_f * 0.3:
        abs_valor = f'{absorcao_venda} candles'
        abs_conclusao = 'Absorção vendedora detectada — distribuição em alta'
        abs_detalhe = 'Candles de alta com delta negativo indicam oferta institucional oculta'
        abs_cor = C['red']
    else:
        abs_valor = 'Não detectada'
        abs_conclusao = 'Sem sinal claro de absorção institucional'
        abs_detalhe = f'Absorção compra: {absorcao_compra} · venda: {absorcao_venda} candles'
        abs_cor = C['text2']

    card_abs = _card_bp('Absorção Institucional', abs_valor, abs_conclusao, abs_detalhe, abs_cor)

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
            cor_ol   = C['green'] if delta_ol > 0 else C['red']

            vol_valor = f'{int(len(outliers))} evento(s)'
            vol_conclusao = f'Volume anômalo detectado às {dt_ol} em {_fmt(preco_ol, 0)}'
            vol_detalhe = (f'Delta {delta_ol:+,} — lado {lado_ol} dominou · '
                           f'{int(mascara.sum())} eventos acima de 2σ')
        else:
            vol_valor = 'Sem anomalia'
            vol_conclusao = 'Volume distribuído normalmente — sem eventos institucionais óbvios'
            vol_detalhe = f'Média: {int(media_vol):,} · threshold 2σ: {int(media_vol + 2*std_vol):,}'
            cor_ol = C['text2']

        card_vol_bp = _card_bp('Volume Anômalo', vol_valor, vol_conclusao, vol_detalhe, cor_ol)
    else:
        card_vol_bp = _card_bp('Volume Anômalo', '—', 'Sem dado de tick suficiente', '', C['text2'])

    # -------------------------------------------------------------------------
    # ANÁLISE 4 — O QUE FAZER COM ESSA INFORMAÇÃO
    # -------------------------------------------------------------------------
    # Leitura consolidada acionável
    if pct_c > 60 and absorcao_compra > total_candles_f * 0.2:
        acao = 'Compradores dominam e absorvem a venda — cenário favorável para longs em S'
        cor_acao = C['green']
    elif pct_v > 60 and absorcao_venda > total_candles_f * 0.2:
        acao = 'Vendedores dominam e distribuem em alta — cenário favorável para shorts em R'
        cor_acao = C['red']
    elif n_diverg > total_candles_f * 0.4:
        acao = f'Alta divergência candle/delta ({pct_diverg}%) — mercado em transição, aguardar definição'
        cor_acao = C['amber']
    else:
        acao = 'Fluxo sem sinal direcional claro — aguardar setup com confluência de S/R e tendência'
        cor_acao = C['text2']

    card_acao = win98_groupbox(
        'O QUE FAZER COM ISSO',
        html.Div(acao, style={
            'fontSize': '14px', 'color': cor_acao,
            'fontFamily': FONTS['body'], 'lineHeight': '1.6',
            'fontWeight': '500',
        }),
        cor_led=C['gold'], estilo_extra={'marginTop': '12px'},
    )

    # Sem secao('BIG PLAYERS FOOTPRINTS') aqui — o título já vem da titlebar
    # da janela (win98_window('win-big-players', 'Big Players', ...)).
    return html.Div([
        html.Div([card_lado, card_abs, card_vol_bp],
                 style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
        card_acao,
    ])


# =============================================================================
# DIÁRIO DE TRADE — construção da UI (janela `win-diario`)
# =============================================================================
# Fase B do plano (ver `C:\Users\jesue\.claude\plans\t-tulo-super-quant-eager-
# knuth.md`): 4 abas internas (Novo Trade / Histórico / Estatísticas /
# Reconciliação) na MESMA janela — não 4 janelas, seguindo o padrão do SQ'98
# de janela por área de negócio. Só "Novo Trade" é funcional nesta fase; as
# outras 3 são placeholder até as Fases C/D. Toda a lógica de dados vem de
# `diario_trade.py` (`dt`) — zero duplicação com o dashboard clássico, só a
# construção de HTML é própria de cada front-end.

_ABAS_DIARIO98 = [
    ('novo', 'Novo Trade'),
    ('historico', 'Histórico'),
    ('estatisticas', 'Estatísticas'),
    ('reconciliacao', 'Reconciliação'),
]


def _opcoes_diario(valores: list[str]) -> list[dict]:
    """Converte vocabulário canônico (snake_case) em opções de dropdown legíveis."""
    return [{'label': v.replace('_', ' ').title(), 'value': v} for v in valores]


def _campo_diario98(label: str, componente) -> html.Div:
    return html.Div([
        html.Div(label, style={'fontSize': '10px', 'color': C['text2'],
                                'marginBottom': '2px', 'fontFamily': FONTS['display']}),
        componente,
    ], style={'marginBottom': '8px'})


def _construir_diario98_novo_trade() -> html.Div:
    campo = _campo_diario98
    linha = lambda *filhos: html.Div(list(filhos), style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'})

    return html.Div([
        win98_groupbox('Dados da Operação', html.Div([
            linha(
                campo('Data', dcc.Input(id='diario98-data', type='date', style=win98_field())),
                campo('Ativo', dcc.Dropdown(id='diario98-ativo', options=_opcoes_diario(cfg.ATIVOS),
                                             clearable=False, style={'width': '110px'})),
                campo('Janela', dcc.Dropdown(id='diario98-janela',
                                              options=_opcoes_diario(list(cfg.JANELAS_PREGAO.keys())),
                                              style={'width': '150px'})),
            ),
            linha(
                campo('Estratégia', dcc.Dropdown(id='diario98-estrategia', multi=True,
                                                  options=_opcoes_diario(dt.ESTRATEGIA_TAGS_CANONICAS),
                                                  style={'width': '280px'})),
                campo('Sala/Análise', dcc.Dropdown(id='diario98-sala',
                                                     options=_opcoes_diario(dt.SALA_ANALISE_CANONICA),
                                                     style={'width': '170px'})),
            ),
            linha(
                campo('Tipo', dcc.Dropdown(id='diario98-tipo-operacao', options=_opcoes_diario(dt.TIPO_OPERACAO),
                                            clearable=False, style={'width': '100px'})),
                campo('Alvo (pts)', dcc.Input(id='diario98-alvo', type='number', style=win98_field())),
                campo('Stop (pts)', dcc.Input(id='diario98-stop', type='number', style=win98_field())),
                campo('Lote', dcc.Input(id='diario98-lote', type='number', value=1, style=win98_field())),
            ),
            linha(
                campo('Preço Entrada', dcc.Input(id='diario98-preco-entrada', type='number', style=win98_field())),
                campo('Preço Saída', dcc.Input(id='diario98-preco-saida', type='number', style=win98_field())),
                campo('Tipo Saída', dcc.Dropdown(id='diario98-tipo-saida', options=_opcoes_diario(dt.TIPO_SAIDA),
                                                  style={'width': '140px'})),
            ),
            linha(
                campo('Resultado (pts)', dcc.Input(id='diario98-resultado', type='number', style=win98_field())),
                campo('Saldo (R$)', dcc.Input(id='diario98-saldo', type='number', style=win98_field())),
            ),
        ])),

        win98_groupbox('Nível de S/R referenciado (opcional)', html.Div([
            linha(
                campo('Preço', dcc.Input(id='diario98-nivel-sr', type='number', style=win98_field())),
                campo('Tipo', dcc.Input(id='diario98-nivel-sr-tipo', type='text', placeholder='ex: suporte',
                                         style=win98_field())),
            ),
        ])),

        win98_groupbox('Anotações', html.Div([
            campo('Resumo Operacional', dcc.Textarea(id='diario98-resumo', rows=3,
                                                       style={**win98_field(), 'height': 'auto', 'resize': 'none'})),
            campo('Obs (autocoaching)', dcc.Textarea(id='diario98-obs', rows=2,
                                                       style={**win98_field(), 'height': 'auto', 'resize': 'none'})),
            campo('Tags de Setup/Erro', dcc.Dropdown(id='diario98-tags-setup-erro', multi=True,
                                                       options=_opcoes_diario(dt.TAGS_SETUP_ERRO_INICIAIS))),
        ])),

        win98_groupbox('Print', html.Div([
            html.Div('Cole (Ctrl+V) um print ou envie um arquivo:', style={
                'fontSize': '10px', 'color': C['text2'], 'marginBottom': '6px'}),
            dcc.Upload(
                id='diario98-upload-imagem',
                children=html.Div('Arraste ou clique pra selecionar um arquivo', style={'fontSize': '11px'}),
                style={**win98_sunken(bg=C['window']), 'padding': '10px', 'textAlign': 'center', 'cursor': 'pointer'},
            ),
            dcc.Store(id='store-diario98-imagem-colada'),
            html.Div(id='diario98-preview-imagem', style={'marginTop': '8px'}),
        ])),

        html.Div([
            html.Button('Salvar Operação', id='btn-diario98-salvar', n_clicks=0,
                        className='win98-btn', style=win98_button()),
            html.Button('Limpar', id='btn-diario98-limpar', n_clicks=0,
                        className='win98-btn', style=win98_button(extra={'marginLeft': '8px'})),
        ], style={'marginTop': '10px'}),
        html.Div(id='diario98-status', style={'marginTop': '8px', 'fontSize': '11px', 'fontFamily': FONTS['mono']}),
    ])


def _construir_diario98_placeholder(nome: str) -> html.Div:
    return html.Div(f'{nome} — em construção.', style={
        'padding': '30px', 'textAlign': 'center', 'color': C['text2'],
        'fontFamily': FONTS['display'], 'fontSize': '12px',
    })


# --- Histórico (Fase C) -----------------------------------------------------

def _td_diario98(conteudo, extra=None) -> html.Td:
    estilo = {'padding': '4px 8px', 'fontSize': '11px', 'fontFamily': FONTS['mono'],
              'borderBottom': f"1px solid {C['border']}"}
    if extra:
        estilo.update(extra)
    return html.Td(conteudo, style=estilo)


def _linha_historico_98(trade: pd.Series) -> html.Tr:
    tags = trade['estrategia_tags']
    tags_str = ', '.join(tags) if hasattr(tags, '__iter__') else ''
    saldo = trade['saldo_operacao_reais']
    saldo_num = float(saldo) if pd.notna(saldo) else 0.0
    cor_saldo = C['green'] if saldo_num > 0 else (C['red'] if saldo_num < 0 else C['text2'])
    thumb = (
        html.Img(src=f"/diario-imagens/{trade['imagem_arquivo']}", style={
            'width': '48px', 'height': '32px', 'objectFit': 'cover',
            'border': f"1px solid {C['shadow_dk']}",
        }) if trade.get('imagem_arquivo') else html.Span('—', style={'color': C['text2']})
    )
    return html.Tr([
        _td_diario98(str(trade['data_op'])),
        _td_diario98(trade['ativo']),
        _td_diario98(trade.get('janela_pregao') or '—'),
        _td_diario98(tags_str or '—'),
        _td_diario98(trade.get('tipo_operacao') or '—'),
        _td_diario98(f"{trade['resultado_pontos']:+.0f}" if pd.notna(trade['resultado_pontos']) else '—'),
        _td_diario98(f"R$ {saldo_num:+.2f}" if pd.notna(saldo) else '—',
                     extra={'color': cor_saldo, 'fontWeight': '700'}),
        _td_diario98(thumb),
    ])


def _construir_diario98_historico() -> html.Div:
    return html.Div([
        html.Div([
            dcc.Dropdown(id='diario98-hist-ativo', options=_opcoes_diario(cfg.ATIVOS),
                         placeholder='Ativo', style={'width': '120px'}),
            dcc.Dropdown(id='diario98-hist-janela', options=_opcoes_diario(list(cfg.JANELAS_PREGAO.keys())),
                         placeholder='Janela', style={'width': '160px'}),
            dcc.Dropdown(id='diario98-hist-estrategia', options=_opcoes_diario(dt.ESTRATEGIA_TAGS_CANONICAS),
                         placeholder='Estratégia', style={'width': '190px'}),
        ], style={'display': 'flex', 'gap': '8px', 'marginBottom': '10px', 'flexWrap': 'wrap'}),
        html.Div(id='diario98-historico-tabela'),
    ])


# --- Estatísticas (Fase C) ---------------------------------------------------

def _card_estatistica_98(titulo: str, valor: str, cor=None) -> html.Div:
    return html.Div([
        html.Div(titulo, style={'fontSize': '9px', 'color': C['text2'], 'fontFamily': FONTS['display'],
                                 'textTransform': 'uppercase', 'letterSpacing': '0.05em', 'marginBottom': '4px'}),
        html.Div(valor, style={'fontSize': '15px', 'fontWeight': '700', 'fontFamily': FONTS['mono'],
                                'color': cor or C['text']}),
    ], style={**win98_sunken(bg=C['window'], padding='6px 10px'), 'minWidth': '110px'})


def _bloco_breakdown_98(titulo: str, dados: dict) -> html.Div:
    linhas = [
        html.Div([
            html.Span(chave.replace('_', ' ').title(), style={'flex': '1', 'fontSize': '11px'}),
            html.Span(str(v['n']), style={'width': '30px', 'textAlign': 'right', 'fontSize': '11px',
                                           'fontFamily': FONTS['mono']}),
            html.Span(f"R$ {v['saldo']:+.0f}", style={
                'width': '80px', 'textAlign': 'right', 'fontSize': '11px', 'fontFamily': FONTS['mono'],
                'color': C['green'] if v['saldo'] > 0 else (C['red'] if v['saldo'] < 0 else C['text2']),
            }),
        ], style={'display': 'flex', 'padding': '3px 0', 'borderBottom': f"1px solid {C['border']}"})
        for chave, v in dados.items()
    ] or [html.Div('Sem dados.', style={'fontSize': '11px', 'color': C['text2']})]
    return win98_groupbox(titulo, html.Div(linhas), estilo_extra={'flex': '1', 'minWidth': '220px'})


def _grafico_evolucao_saldo_98(stats: dict) -> go.Figure:
    fig = go.Figure()
    if stats['evolucao_data']:
        fig.add_trace(go.Scatter(
            x=stats['evolucao_data'], y=stats['evolucao_saldo_acumulado'],
            mode='lines', line=dict(color=C['gold'], width=2),
            fill='tozeroy', fillcolor='rgba(0,0,128,0.08)',
        ))
    fig.update_layout(
        paper_bgcolor=C['panel'], plot_bgcolor=C['window'],
        font=dict(family=FONTS['mono'], color=C['text'], size=10),
        margin=dict(l=45, r=10, t=10, b=30), height=180,
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor=C['border'], gridwidth=0.5, title='R$ acumulado'),
    )
    return fig


def _construir_diario98_estatisticas(stats: dict) -> html.Div:
    if stats['num_operacoes'] == 0:
        return _construir_diario98_placeholder('Estatísticas (nenhum trade real lançado ainda)')

    payoff_str = f"{stats['payoff']:.2f}" if stats['payoff'] is not None else '—'
    return html.Div([
        html.Div([
            _card_estatistica_98('Operações', str(stats['num_operacoes'])),
            _card_estatistica_98('Saldo Total', f"R$ {stats['saldo_total']:+.2f}",
                                  cor=C['green'] if stats['saldo_total'] > 0 else C['red']),
            _card_estatistica_98('Taxa de Acerto', f"{stats['taxa_acerto'] * 100:.1f}%"),
            _card_estatistica_98('Payoff', payoff_str),
            _card_estatistica_98('Expectativa', f"R$ {stats['expectativa']:+.2f}"),
            _card_estatistica_98('Max Gain', f"R$ {stats['max_gain']:+.2f}", cor=C['green']),
            _card_estatistica_98('Max Loss', f"R$ {stats['max_loss']:+.2f}", cor=C['red']),
            _card_estatistica_98('Seq. Wins', str(stats['maior_sequencia_win'])),
            _card_estatistica_98('Seq. Losses', str(stats['maior_sequencia_loss'])),
        ], style={'display': 'flex', 'gap': '8px', 'flexWrap': 'wrap', 'marginBottom': '14px'}),

        win98_groupbox('Evolução de Saldo', dcc.Graph(
            figure=_grafico_evolucao_saldo_98(stats), config={'displayModeBar': False},
        )),

        html.Div([
            _bloco_breakdown_98('Por Ativo', stats['por_ativo']),
            _bloco_breakdown_98('Por Janela', stats['por_janela']),
        ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
        html.Div([
            _bloco_breakdown_98('Por Sala/Análise', stats['por_sala']),
            _bloco_breakdown_98('Por Estratégia', stats['por_estrategia']),
        ], style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
    ])


# --- Reconciliação (Fase D) --------------------------------------------------

_COR_STATUS_RECONCILIACAO = {
    'ok': 'green', 'divergente': 'red',
    'sem_correspondencia_mt5': 'yellow', 'sem_correspondencia_diario': 'text2',
}


def _tabela_reconciliacao_98(df: pd.DataFrame | None) -> html.Div:
    if df is None or df.empty:
        return html.Div('Nenhum resultado ainda — clique em "Reconciliar agora".', style={
            'padding': '16px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '12px',
        })
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
        for t in ['ID Trade', 'Position MT5', 'Divergência', 'Confiança', 'Status']
    ])
    linhas = []
    for _, linha in df.iterrows():
        cor = C[_COR_STATUS_RECONCILIACAO.get(linha['mt5_match_status'], 'text2')]
        divergencia = linha['mt5_divergencia_reais']
        linhas.append(html.Tr([
            _td_diario98(str(int(linha['id_trade'])) if pd.notna(linha['id_trade']) else '—'),
            _td_diario98(str(int(linha['mt5_position_id'])) if pd.notna(linha['mt5_position_id']) else '—'),
            _td_diario98(f"R$ {divergencia:+.2f}" if pd.notna(divergencia) else '—'),
            _td_diario98(linha['confianca'] or '—'),
            _td_diario98((linha['mt5_match_status'] or '').replace('_', ' '),
                         extra={'color': cor, 'fontWeight': '700'}),
        ]))
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'})


def _construir_diario98_reconciliacao() -> html.Div:
    return html.Div([
        html.Div([
            html.Button('Reconciliar agora', id='btn-diario98-reconciliar', n_clicks=0,
                        className='win98-btn', style=win98_button()),
            html.Span(id='diario98-reconciliar-status', style={
                'marginLeft': '10px', 'fontSize': '11px', 'color': C['text2'],
            }),
        ], style={'marginBottom': '10px'}),
        html.Div(_tabela_reconciliacao_98(persistencia.carregar_reconciliacao()),
                 id='diario98-reconciliacao-tabela'),
    ])


# =============================================================================
# LAYOUT PRINCIPAL
# =============================================================================

app = dash.Dash(
    __name__,
    title="Super Quant '98",
    update_title=None,
    suppress_callback_exceptions=True,
    assets_folder='assets_98',
)

# Rota pras imagens do diário — `assets_98/` não serve `data/base_processada/
# diario_imagens/` (fora da pasta de assets do Dash). Primeira rota do
# projeto aceitando parâmetro de path do usuário — valida contra o padrão
# exato de nome de arquivo (`salvar_imagem` em diario_trade.py) ANTES de
# repassar pro Flask, pra nunca abrir uma superfície de path traversal.
_PADRAO_NOME_IMAGEM_DIARIO = re.compile(r'^\d{4}_[A-Z]+_\d{8}\.png$')


@app.server.route('/diario-imagens/<path:nome_arquivo>')
def _servir_imagem_diario(nome_arquivo):
    if not _PADRAO_NOME_IMAGEM_DIARIO.match(nome_arquivo):
        flask.abort(404)
    return flask.send_from_directory(cfg.PASTA_DIARIO_IMAGENS, nome_arquivo)

app.layout = html.Div([

    # Store para mensagem pendente (para preloader)
    dcc.Store(id='store-mensagem-pendente', data=None),

    # Store para candle atual (atualizado a 1s)
    dcc.Store(id='store-candle-atual', data={}),

    # Store do ativo selecionado
    dcc.Store(id='store-ativo', data='WIN'),
    dcc.Store(id='store-metricas', data={}),

    # Store da aba ativa da janela Diário
    dcc.Store(id='store-diario98-aba', data='novo'),

    # Interval rápido — 1s: atualiza preço, candle atual, fluxo
    dcc.Interval(id='interval-live', interval=1_000, n_intervals=0),
    # Interval lento — 30s: reconstrói features completas e blocos pesados
    dcc.Interval(id='interval-slow', interval=30_000, n_intervals=0),

    # =========================================================================
    # ÍCONES DE DESKTOP
    # Gerados a partir de JANELAS (fonte única de verdade em estilo_win98.py).
    # Só as janelas com `implementada=True` ganham ícone/item de Start —
    # Backtesting/Configurações continuam só registradas, sem janela real.
    # =========================================================================
    html.Div([
        win98_desktop_icon(j['id'], j['titulo'], j['icone'])
        for j in JANELAS if j['implementada']
    ], style={
        'position': 'fixed', 'top': '10px', 'left': '10px', 'zIndex': '1',
        'display': 'flex', 'flexDirection': 'column', 'gap': '4px',
    }),

    # =========================================================================
    # JANELA — Quant Lab (velocímetros + seletor de ativo + Market View)
    # Antes eram 2 janelas (Quant Lab e Market View); a primeira sobrava
    # muito espaço vazio sozinha, então viraram uma só (ver Feedback UX
    # SuperQuant98.txt).
    # =========================================================================
    win98_window(
        'win-quantlab', 'Quant Lab', icone='/assets/icons/my-computer.png',
        width='520px', min_width='460px',
        default_top_pct=3, default_left_pct=2,
        statusbar=[
            html.Div(id='status-conexao', children='Inicializando...', style={
                **win98_sunken(padding='2px 8px'), 'fontSize': '11px',
                'fontFamily': FONTS['display'], 'flex': '2',
            }),
            html.Div(id='status-contrato', children='—', style={
                **win98_sunken(padding='2px 8px'), 'fontSize': '11px',
                'fontFamily': FONTS['mono'], 'color': C['gold'], 'flex': '1',
            }),
            html.Div(id='status-atualizacao', children='', style={
                **win98_sunken(padding='2px 8px'), 'fontSize': '10px',
                'fontFamily': FONTS['display'], 'flex': '1',
            }),
        ],
        children=html.Div([
            html.Div([
                # Velocímetros — dinâmico, reconstruído a cada 1s
                html.Div(id='hero-conteudo', children=[
                    html.Div('Carregando base de dados...', style={
                        'color': C['text2'], 'fontFamily': FONTS['body'],
                        'fontSize': '14px', 'padding': '20px',
                    })
                ]),
                # Seletor WIN / WDO / WIN×WDO — estático, nunca recriado
                # (só o `style` de cada botão muda, via callback dedicado
                # `atualizar_estilo_ativo_selecionado`). Ver comentário em
                # `construir_hero`.
                html.Div([
                    html.Div('ATIVO', style={
                        'fontSize': '10px', 'color': C['text2'],
                        'fontFamily': FONTS['display'], 'marginBottom': '8px',
                        'letterSpacing': '0.08em',
                    }),
                    html.Div([
                        html.Button('WIN', id='btn-win', n_clicks=0,
                                    className='win98-btn',
                                    style=_estilo_botao_ativo('WIN', 'WIN')),
                        html.Button('WDO', id='btn-wdo', n_clicks=0,
                                    className='win98-btn',
                                    style=_estilo_botao_ativo('WDO', 'WIN')),
                        html.Button('WIN × WDO', id='btn-correlacao', n_clicks=0,
                                    className='win98-btn',
                                    style=_estilo_botao_ativo('CORRELACAO', 'WIN')),
                    ], style={'display': 'flex', 'gap': '8px'}),
                ], style={'marginTop': '14px'}),
            ], style={'marginBottom': '16px'}),
            html.Div(id='market-view-conteudo'),
        ]),
    ),

    # =========================================================================
    # JANELA — Price Action
    # =========================================================================
    win98_window(
        'win-price-action', 'Price Action', icone='/assets/icons/find-file.png',
        width='560px', min_width='400px',
        default_top_pct=3, default_left_pct=46,
        children=html.Div(id='price-action-conteudo'),
    ),

    # =========================================================================
    # JANELA — Big Players
    # =========================================================================
    win98_window(
        'win-big-players', 'Big Players', icone='/assets/icons/favorites.png',
        width='700px', min_width='420px',
        default_top_pct=48, default_left_pct=46,
        children=html.Div(id='big-players-conteudo'),
    ),

    # =========================================================================
    # JANELA — Diário de Trade (4 abas internas, ver seção de construção acima)
    # =========================================================================
    win98_window(
        'win-diario', 'Diário', icone='/assets/icons/notepad.png',
        width='620px', min_width='480px',
        default_top_pct=10, default_left_pct=25, start_closed=True,
        children=html.Div([
            html.Div([
                html.Button(nome, id=f'btn-diario98-aba-{chave}', n_clicks=0,
                            className='win98-btn',
                            style=win98_button(pressed=(chave == 'novo')))
                for chave, nome in _ABAS_DIARIO98
            ], style={'display': 'flex', 'gap': '6px', 'marginBottom': '10px'}),

            html.Div(_construir_diario98_novo_trade(), id='painel-diario98-novo'),
            html.Div(_construir_diario98_historico(),
                     id='painel-diario98-historico', style={'display': 'none'}),
            html.Div(html.Div(id='diario98-estatisticas-conteudo'),
                     id='painel-diario98-estatisticas', style={'display': 'none'}),
            html.Div(_construir_diario98_reconciliacao(),
                     id='painel-diario98-reconciliacao', style={'display': 'none'}),
        ], style={'maxHeight': '65vh', 'overflowY': 'auto', 'padding': '4px'}),
    ),

    # =========================================================================
    # CLIPPY — Mentor IA. Não é uma janela (sem titlebar/taskbar/minimizar):
    # é o assistente flutuante, sempre ancorado no canto, que abre seu balão
    # de fala ao ser clicado (arrastável) — fiel ao Clippy original do
    # Office 97. O sprite (#clippy-agent) é criado pelo próprio JS; aqui só
    # o balão (#clippy-balloon), que existe sempre no DOM (visibilidade
    # controlada por classe .open). Ver assets_98/clippy_agent.js e
    # assets_98/style.css (.clippy-balloon, no estilo do clippy.js original,
    # github.com/clippyjs/clippy.js).
    # =========================================================================
    html.Div([
        html.Div(className='clippy-tip'),
        html.Div([
            # Histórico em transcript de texto simples (estilo IRC) — sem
            # bolhas arredondadas, que são linguagem visual pós-2010.
            html.Div(id='chat-historico', children=[
                html.Div([
                    html.Span('Mentor: ', style={'fontWeight': '700', 'color': C['blue']}),
                    html.Span('Olá, Jesué. Estou monitorando o mercado.', style={'color': C['text']}),
                ], style={
                    'padding': '4px 2px', 'borderBottom': f'1px solid {C["border"]}',
                    'fontFamily': FONTS['mono'], 'fontSize': '11px',
                }),
            ], className='clippy-chat-historico'),

            html.Div(id='scroll-dummy', style={'display': 'none'}),

            # Seletor de modelo + preloader na mesma linha — os dois são
            # discretos de propósito (não são o foco do chat), e colocar o
            # preloader do lado (não empilhado acima) evita a sobreposição
            # que dava com o seletor logo abaixo (2026-07-08: o spinner
            # padrão do Dash "media" a posição do children de 0x0 e acabava
            # ancorado bem em cima do seletor).
            html.Div([
                # `html.Select` nativo não tem prop `value` sincronizável em
                # callback nesta versão do Dash (só componentes dcc.* têm
                # esse binding) — usa dcc.Dropdown, reskinado via CSS
                # (#chat-modelo em assets_98/style.css) pra aproximar da
                # moldura sunken do Win98. "Automático" tenta vários modelos
                # grátis da OpenRouter em ordem até um responder, evitando o
                # "limite excedido".
                dcc.Dropdown(
                    id='chat-modelo',
                    options=ai_analyst.opcoes_modelo(),
                    value='openrouter-auto',
                    clearable=False, searchable=False,
                    style={'fontFamily': FONTS['mono'], 'fontSize': '9px', 'width': '112px', 'flexShrink': '0'},
                ),
                # Preloader clássico Win98 — os bloquinhos azuis do "please
                # wait" de instalação/cópia (não o spinner de bolinhas
                # padrão do Dash, que "pulava" fora da própria caixa na
                # animação de bounce). Pequeno e discreto de propósito —
                # `chat-loading-target` tem tamanho real (não 0x0) pra
                # ocupar um espaço fixo reservado, sem "pular" no layout
                # quando a IA está respondendo.
                dcc.Loading(
                    id='chat-loading',
                    custom_spinner=html.Div(className='win98-progress',
                                             children=html.Div(className='win98-progress-fill')),
                    children=html.Div(id='chat-loading-target',
                                       style={'height': '10px', 'width': '36px'}),
                ),
            ], style={'display': 'flex', 'alignItems': 'center', 'gap': '6px', 'marginBottom': '4px'}),

            # Input + botão — 2026-07-08: era curto demais pra ler o que
            # estava sendo digitado, mas a correção certa é MAIS LINHAS
            # visíveis (textarea), não fonte maior — fonte volta ao
            # tamanho original (12px). Botão usa alignSelf:stretch pra
            # acompanhar a altura do textarea automaticamente.
            html.Div([
                dcc.Textarea(
                    id='chat-input',
                    placeholder='Preço ou pergunta...',
                    rows=3,
                    style={**win98_field(), 'flex': '1', 'height': 'auto',
                           'fontSize': '12px', 'padding': '4px 6px',
                           'resize': 'none', 'lineHeight': '1.35'},
                ),
                html.Button('Enviar', id='btn-chat-enviar', n_clicks=0,
                            className='win98-btn',
                            style=win98_button(extra={'minHeight': '21px', 'minWidth': '54px',
                                                       'fontSize': '11px', 'alignSelf': 'stretch'})),
            ], className='clippy-chat-inputrow'),

            html.Div(id='chat-status', children='', style={
                'fontSize': '9px', 'color': C['text2'],
                'fontFamily': FONTS['mono'], 'marginTop': '4px', 'textAlign': 'right',
            }),
        ], className='clippy-content'),
    ], id='clippy-balloon', className='clippy-balloon'),

    # =========================================================================
    # TASKBAR + START MENU
    # =========================================================================
    win98_taskbar(tray_children=[
        # Janela de notícia (Copom/FOMC/NFP) — calendario_economico.py.
        # Registrado em 2026-07-02: não opera notícia, mas esses horários
        # distorcem a leitura de S/R (ver CHANGELOG.md). Vive na bandeja
        # porque importa independente de qual janela está em foco/minimizada.
        html.Span(id='status-calendario', children='', style={
            'fontSize': '10px', 'fontFamily': FONTS['display'],
            'maxWidth': '260px', 'overflow': 'hidden',
            'textOverflow': 'ellipsis', 'whiteSpace': 'nowrap',
        }),
    ]),
    html.Div([
        html.Div([
            html.Div(html.Span('SUPER QUANT 98'), className='win98-start-side'),
            html.Div([
                win98_start_menu_item(j['id'], j['titulo'], j['icone'])
                for j in JANELAS if j['implementada']
            ], className='win98-start-items'),
        ], className='win98-start-menu-inner'),
    ], id='start-menu', className='win98-start-menu'),

], style={
    'background': C['bg'],
    'height': '100vh',
    'overflow': 'hidden',
    'color': C['text'],
    'fontFamily': FONTS['body'],
    'position': 'relative',
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
# round-trip do callback principal. Ver assets_98/style.css. Dispara só em
# store-ativo (nunca no tick de 1s), então não pisca a cada segundo.
# 4 ids de conteúdo (hero/market-view ficam na mesma janela Quant Lab desde
# a fusão; price-action e big-players são janelas próprias).
app.clientside_callback(
    """
    function(ativo) {
        ['hero-conteudo', 'market-view-conteudo', 'price-action-conteudo', 'big-players-conteudo']
            .forEach(function (id) {
                var el = document.getElementById(id);
                if (el) {
                    el.classList.remove('trocando-ativo');
                    void el.offsetWidth;  // força reflow p/ reiniciar a animação em cliques seguidos
                    el.classList.add('trocando-ativo');
                }
            });
        return '';
    }
    """,
    Output('scroll-dummy', 'children', allow_duplicate=True),
    Input('store-ativo', 'data'),
    prevent_initial_call=True,
)

@app.callback(
    Output('store-ativo', 'data'),
    Input('btn-win', 'n_clicks'),
    Input('btn-wdo', 'n_clicks'),
    Input('btn-correlacao', 'n_clicks'),
    State('store-ativo', 'data'),
    prevent_initial_call=True,
)
def selecionar_ativo(n_win, n_wdo, n_corr, ativo_atual):
    ctx = callback_context
    if not ctx.triggered:
        return ativo_atual
    botao = ctx.triggered[0]['prop_id'].split('.')[0]
    mapa = {'btn-win': 'WIN', 'btn-wdo': 'WDO', 'btn-correlacao': 'CORRELACAO'}
    return mapa.get(botao, ativo_atual)


@app.callback(
    Output('btn-win', 'style'),
    Output('btn-wdo', 'style'),
    Output('btn-correlacao', 'style'),
    Input('store-ativo', 'data'),
)
def atualizar_estilo_ativo_selecionado(ativo):
    """Só troca a cor do botão ativo — dispara apenas quando `store-ativo`
    muda (clique real), nunca a cada tick de 1s. Mantém os `html.Button`
    estáticos intactos (nunca recriados), preservando `n_clicks` no browser."""
    return (
        _estilo_botao_ativo('WIN', ativo),
        _estilo_botao_ativo('WDO', ativo),
        _estilo_botao_ativo('CORRELACAO', ativo),
    )


@app.callback(
    Output('store-candle-atual', 'data'),
    Input('interval-live', 'n_intervals'),
    State('store-ativo', 'data'),
)
def atualizar_candle_atual(n, ativo):
    """Callback rápido (1s): busca só o candle atual do MT5 para atualização em tempo real."""
    with lock:
        bases = estado['bases']

    df = bases.get(ativo if ativo != 'CORRELACAO' else 'WIN')
    if df is None or df.empty:
        return {}

    ultimo = df.iloc[-1]
    atr = float(ultimo.get('atr', 0) or 0)

    # Tenta buscar tick atual do MT5 para preço em tempo real
    preco_atual = float(ultimo['close'])
    try:
        if MT5_OK:
            ticker = estado.get('contrato_win') if ativo in ('WIN', 'CORRELACAO') else estado.get('contrato_wdo')
            if ticker and ticker != '—':
                tick = mt5.symbol_info_tick(ticker)
                if tick:
                    preco_atual = tick.last or tick.bid or preco_atual
    except Exception:
        pass

    return {
        'preco': preco_atual,
        'tipo_candle': str(ultimo.get('tipo_candle', '—')),
        'direcao': int(ultimo.get('direcao_candle', 0) or 0),
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


@app.callback(
    Output('hero-conteudo', 'children'),
    Output('market-view-conteudo', 'children'),
    Output('price-action-conteudo', 'children'),
    Output('big-players-conteudo', 'children'),
    Output('status-conexao', 'children'),
    Output('status-contrato', 'children'),
    Output('status-atualizacao', 'children'),
    Output('status-calendario', 'children'),
    Output('status-calendario', 'style'),
    Input('interval-live', 'n_intervals'),
    Input('store-ativo', 'data'),
    Input('store-candle-atual', 'data'),
)
def atualizar_dashboard(n_intervals, ativo, candle_atual):
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

    df_win   = bases.get('WIN')
    df_wdo   = bases.get('WDO')
    df_ativo = df_win if ativo in ('WIN', 'CORRELACAO') else df_wdo

    m_win = extrair_metricas(df_win) if df_win is not None else {}
    m_wdo = extrair_metricas(df_wdo) if df_wdo is not None else {}
    m     = m_win if ativo in ('WIN', 'CORRELACAO') else m_wdo

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
        return placeholder, placeholder, placeholder, placeholder, status, contrato_str, upd_str, cal_str, cal_style

    hero         = construir_hero(m_win, m_wdo, ativo)
    market_view  = construir_market_view(m)
    price_action = construir_price_action(df_ativo, m)
    big_players  = construir_big_players(df_ativo)

    return hero, market_view, price_action, big_players, status, contrato_str, upd_str, cal_str, cal_style


def _linha_chat(quem: str, texto: str, cor: str = None) -> html.Div:
    """Uma linha de transcript estilo IRC — sem bolhas arredondadas, que são
    linguagem visual pós-2010. `quem` já vem com o rótulo (ex. 'Você:',
    'Mentor:')."""
    return html.Div([
        html.Span(quem + ' ', style={'fontWeight': '700', 'color': cor or C['gold']}),
        html.Span(texto, style={'whiteSpace': 'pre-wrap', 'color': C['text']}),
    ], style={
        'padding': '4px 2px', 'borderBottom': f'1px solid {C["border"]}',
        'fontFamily': FONTS['mono'], 'fontSize': '12px', 'lineHeight': '1.5',
    })


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
    msg_usuario = _linha_chat('Você:', texto, cor=C['blue'])

    # Chama a IA (bloqueante — resposta real vem aqui mesmo)
    with lock:
        bases = estado['bases']
    df = bases.get(ativo if ativo != 'CORRELACAO' else 'WIN')
    m = extrair_metricas(df) if df is not None else {}

    try:
        resposta_bruta = ai_analyst.analisar_com_pergunta_raw(m, texto, ativo, modelo)

        if isinstance(resposta_bruta, dict) and resposta_bruta.get('erro_429'):
            # Rate limit — calcula timer de espera
            segundos_espera = resposta_bruta.get('retry_after', 60)
            modelo_limitado = resposta_bruta.get('modelo_usado', '')
            sufixo_modelo = f' ({modelo_limitado})' if modelo_limitado else ''
            msg_ia = _linha_chat(
                'Mentor:',
                f'⏱ Limite de requisições atingido{sufixo_modelo}. Aguarde {segundos_espera}s antes de enviar novamente.',
                cor=C['amber'],
            )
            status_str = f'⏱ Aguarde {segundos_espera}s · Limite da API atingido'

        elif isinstance(resposta_bruta, dict) and resposta_bruta.get('erro'):
            msg_ia = _linha_chat('Mentor:', f'⚠ {resposta_bruta["erro"]}', cor=C['red'])
            status_str = '⚠ Erro na API'

        else:
            resposta = resposta_bruta.get('texto', '')
            tokens_usados = resposta_bruta.get('tokens_usados', 0)
            tokens_max = cfg.GEMINI_MAX_TOKENS
            pct = round(tokens_usados / tokens_max * 100) if tokens_max > 0 else 0

            msg_ia = _linha_chat('Mentor:', resposta, cor=C['gold'])

            ts = datetime.now().strftime('%H:%M:%S')
            modelo_usado = resposta_bruta.get('modelo_usado', '')
            status_str = f'{ts} · {tokens_usados} tokens ({pct}% do limite) · {modelo_usado}'

    except Exception as e:
        msg_ia = _linha_chat('Mentor:', f'⚠ Erro inesperado: {str(e)[:60]}', cor=C['red'])
        status_str = '⚠ Erro inesperado'

    novo_historico = (historico_atual or []) + [msg_usuario, msg_ia]

    return novo_historico, status_str, '', None, ''


# -----------------------------------------------------------------------------
# DIÁRIO DE TRADE — callbacks (janela `win-diario`)
# -----------------------------------------------------------------------------

@app.callback(
    Output('store-diario98-aba', 'data'),
    [Input(f'btn-diario98-aba-{chave}', 'n_clicks') for chave, _ in _ABAS_DIARIO98],
    prevent_initial_call=True,
)
def selecionar_aba_diario98(*_n_clicks):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    botao_id = ctx.triggered[0]['prop_id'].split('.')[0]
    chave = botao_id.replace('btn-diario98-aba-', '')
    return chave


@app.callback(
    [Output(f'btn-diario98-aba-{chave}', 'style') for chave, _ in _ABAS_DIARIO98],
    Input('store-diario98-aba', 'data'),
)
def atualizar_estilo_aba_diario98(aba_ativa):
    return [win98_button(pressed=(chave == aba_ativa)) for chave, _ in _ABAS_DIARIO98]


@app.callback(
    [Output(f'painel-diario98-{chave}', 'style') for chave, _ in _ABAS_DIARIO98],
    Input('store-diario98-aba', 'data'),
)
def atualizar_painel_diario98(aba_ativa):
    return [{'display': 'block' if chave == aba_ativa else 'none'} for chave, _ in _ABAS_DIARIO98]


@app.callback(
    Output('diario98-preview-imagem', 'children'),
    Input('diario98-upload-imagem', 'contents'),
    Input('store-diario98-imagem-colada', 'data'),
    prevent_initial_call=True,
)
def preview_imagem_diario98(upload_contents, imagem_colada):
    ctx = callback_context
    if not ctx.triggered:
        return no_update
    origem = ctx.triggered[0]['prop_id'].split('.')[0]
    data_url = upload_contents if origem == 'diario98-upload-imagem' else imagem_colada
    if not data_url:
        return no_update
    return html.Img(src=data_url, style={'maxWidth': '240px', 'border': f"1px solid {C['shadow_dk']}"})


@app.callback(
    Output('diario98-status', 'children'),
    Input('btn-diario98-salvar', 'n_clicks'),
    State('diario98-data', 'value'),
    State('diario98-ativo', 'value'),
    State('diario98-janela', 'value'),
    State('diario98-estrategia', 'value'),
    State('diario98-sala', 'value'),
    State('diario98-tipo-operacao', 'value'),
    State('diario98-alvo', 'value'),
    State('diario98-stop', 'value'),
    State('diario98-lote', 'value'),
    State('diario98-preco-entrada', 'value'),
    State('diario98-preco-saida', 'value'),
    State('diario98-tipo-saida', 'value'),
    State('diario98-resultado', 'value'),
    State('diario98-saldo', 'value'),
    State('diario98-resumo', 'value'),
    State('diario98-obs', 'value'),
    State('diario98-nivel-sr', 'value'),
    State('diario98-nivel-sr-tipo', 'value'),
    State('diario98-tags-setup-erro', 'value'),
    State('diario98-upload-imagem', 'contents'),
    State('store-diario98-imagem-colada', 'data'),
    prevent_initial_call=True,
)
def salvar_novo_trade_98(n_clicks, data_op, ativo, janela, estrategia_tags, sala, tipo_op, alvo, stop, lote,
                          preco_entrada, preco_saida, tipo_saida, resultado, saldo, resumo, obs,
                          nivel_sr, nivel_sr_tipo, tags_setup_erro, upload_contents, imagem_colada):
    if not data_op or not ativo:
        return '⚠ Preencha ao menos Data e Ativo.'

    campos = dict(
        data_op=data_op, ativo=ativo, janela_pregao=janela,
        estrategia_tags=estrategia_tags or [], sala_analise=sala,
        tipo_operacao=tipo_op, alvo_pontos=alvo, stop_pontos=stop, lote=lote or 1,
        preco_entrada=preco_entrada, preco_saida=preco_saida, tipo_saida=tipo_saida,
        resultado_pontos=resultado, saldo_operacao_reais=saldo,
        resumo_operacional=resumo, obs=obs,
        nivel_sr_referencia=nivel_sr, nivel_sr_tipo=nivel_sr_tipo,
        tags_setup_erro=tags_setup_erro or [],
        conta='real',
    )

    imagem_bytes = None
    data_url = upload_contents or imagem_colada
    if data_url:
        try:
            imagem_bytes = base64.b64decode(data_url.split(',', 1)[1])
        except Exception:
            imagem_bytes = None

    id_trade = dt.registrar_trade(campos, imagem_bytes=imagem_bytes)
    if id_trade is None:
        return '⚠ Diário ocupado por outra escrita — tente novamente em instantes.'
    return f'✔ Operação #{id_trade} salva.'


@app.callback(
    Output('diario98-data', 'value'),
    Output('diario98-ativo', 'value'),
    Output('diario98-janela', 'value'),
    Output('diario98-estrategia', 'value'),
    Output('diario98-sala', 'value'),
    Output('diario98-tipo-operacao', 'value'),
    Output('diario98-alvo', 'value'),
    Output('diario98-stop', 'value'),
    Output('diario98-lote', 'value'),
    Output('diario98-preco-entrada', 'value'),
    Output('diario98-preco-saida', 'value'),
    Output('diario98-tipo-saida', 'value'),
    Output('diario98-resultado', 'value'),
    Output('diario98-saldo', 'value'),
    Output('diario98-resumo', 'value'),
    Output('diario98-obs', 'value'),
    Output('diario98-nivel-sr', 'value'),
    Output('diario98-nivel-sr-tipo', 'value'),
    Output('diario98-tags-setup-erro', 'value'),
    Output('diario98-preview-imagem', 'children', allow_duplicate=True),
    Output('store-diario98-imagem-colada', 'data', allow_duplicate=True),
    Input('btn-diario98-limpar', 'n_clicks'),
    prevent_initial_call=True,
)
def limpar_form_diario98(n_clicks):
    return (None, None, None, None, None, None, None, None, 1,
            None, None, None, None, None, None, None, None, None, None,
            None, None)


@app.callback(
    Output('diario98-historico-tabela', 'children'),
    Input('diario98-hist-ativo', 'value'),
    Input('diario98-hist-janela', 'value'),
    Input('diario98-hist-estrategia', 'value'),
)
def atualizar_historico_98(ativo, janela, estrategia):
    df = dt.listar_trades(ativo=ativo, janela=janela, estrategia_tag=estrategia, is_simulated=False)
    df = df.sort_values('data_op', ascending=False).head(50)
    if df.empty:
        return html.Div('Nenhuma operação encontrada.', style={
            'padding': '16px', 'color': C['text2'], 'textAlign': 'center', 'fontSize': '12px',
        })
    cabecalho = html.Tr([
        html.Th(t, style={'textAlign': 'left', 'padding': '4px 8px', 'fontSize': '10px', 'color': C['text2']})
        for t in ['Data', 'Ativo', 'Janela', 'Estratégia', 'Tipo', 'Pts', 'Saldo', 'Print']
    ])
    linhas = [_linha_historico_98(row) for _, row in df.iterrows()]
    return html.Table([html.Thead(cabecalho), html.Tbody(linhas)],
                       style={'width': '100%', 'borderCollapse': 'collapse'})


@app.callback(
    Output('diario98-estatisticas-conteudo', 'children'),
    Input('diario98-status', 'children'),
    Input('store-diario98-aba', 'data'),
)
def atualizar_estatisticas_98(_status, aba_ativa):
    # Recomputa só quando o usuário entra na aba Estatísticas ou logo após
    # salvar um trade — NÃO em `interval-slow` (30s). Aquele trigger rodava
    # pra sempre em segundo plano, mesmo com a janela Diário fechada, e cada
    # leitura do parquet (OneDrive) varia de ~5ms a ~0.8s; num servidor Dash
    # de thread única (`app.run()` sem `threaded=True`), esse meio segundo
    # ocasional travava o app inteiro, inclusive o tick de 1s do preço ao
    # vivo — causa raiz da lentidão geral relatada após esta feature entrar.
    if aba_ativa != 'estatisticas':
        return no_update
    return _construir_diario98_estatisticas(dt.calcular_estatisticas())


@app.callback(
    Output('diario98-reconciliacao-tabela', 'children'),
    Output('diario98-reconciliar-status', 'children'),
    Input('btn-diario98-reconciliar', 'n_clicks'),
    prevent_initial_call=True,
)
def reconciliar_98(n_clicks):
    try:
        resultado = dt.reconciliar_com_mt5(dias_atras=30)
    except Exception as e:
        return no_update, f'⚠ Erro ao reconciliar: {str(e)[:100]}'
    return _tabela_reconciliacao_98(resultado), f'Concluído — {len(resultado)} linhas.'


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  SUPER QUANT '98")
    print("  Abrindo em: http://127.0.0.1:8051")
    print("=" * 60)
    app.run(debug=False, host='127.0.0.1', port=8051)
