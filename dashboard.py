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
from dash import dcc, html, Input, Output, State, callback_context, no_update
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

        # Atualização incremental em background (busca candles novos)
        if MT5_OK:
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
# MÉTRICAS CALCULADAS A PARTIR DA BASE
# =============================================================================

def extrair_metricas(df: pd.DataFrame, n_snapshot: int = 500) -> dict:
    """
    Extrai métricas do snapshot atual (últimos 500 candles).

    JANELAS EXPLÍCITAS — cada métrica usa a mesma janela ou declara a sua:
      - Fluxo (delta, razão de agressão): últimos 20 candles COM fluxo disponível
        (mesma janela para as duas métricas — elimina a contradição anterior)
      - Tendência/fase/saúde: último candle do snapshot (já é resultado de janela longa)
      - Amplitude do dia: só os candles do dia atual
      - Build-up/double pressure: último candle (já é resultado de janela de 8 candles)
    """
    if df is None or df.empty:
        return {}

    snap = df.tail(n_snapshot).copy().reset_index(drop=True)
    ultimo = snap.iloc[-1]
    hoje = snap[snap['datetime'].dt.date == pd.Timestamp.now().date()]

    m = {}
    JANELA_FLUXO = 20  # candles com fluxo — mesma janela para delta E razão de agressão

    def _safe_int(v, default=0):
        try:
            f = float(v)
            return default if (f != f) else int(f)  # f != f detecta nan
        except (TypeError, ValueError):
            return default

    def _safe_bool(v, default=False):
        try:
            if v is None or v is pd.NA:
                return default
            return bool(v)
        except (TypeError, ValueError):
            return default

    # Preço atual
    m['preco'] = ultimo['close']

    # Tendência
    m['tendencia'] = ultimo.get('estado_tendencia', '—')
    m['fase']      = ultimo.get('fase_tendencia', '—')
    m['saude']     = ultimo.get('saude_tendencia', '—')
    m['tipo_candle'] = ultimo.get('tipo_candle', '—')
    m['atr'] = ultimo.get('atr', np.nan)

    # Amplitude do dia vs ATR
    if not hoje.empty and not np.isnan(float(m['atr']) if m['atr'] is not None else np.nan) and m['atr'] > 0:
        amp_dia = hoje['high'].max() - hoje['low'].min()
        m['amp_dia'] = amp_dia
        m['amp_dia_atr'] = round(amp_dia / m['atr'], 2)
    else:
        m['amp_dia'] = np.nan
        m['amp_dia_atr'] = np.nan

    # -------------------------------------------------------------------------
    # FLUXO — janela única de JANELA_FLUXO candles com dado real
    # Resolve a contradição anterior: delta e razão de agressão agora olham
    # exatamente o mesmo período
    # -------------------------------------------------------------------------
    tem_fluxo_col = snap.get('fluxo_disponivel', pd.Series([False]*len(snap)))
    snap_com_fluxo = snap[tem_fluxo_col == True].tail(JANELA_FLUXO)

    if snap_com_fluxo.empty:
        m['tem_fluxo']       = False
        m['delta_acum']      = None
        m['razao_agressao']  = None
        m['janela_fluxo_n']  = 0
        m['fluxo_periodo']   = '—'
    else:
        delta_col  = pd.to_numeric(snap_com_fluxo['delta'],         errors='coerce')
        vol_c_col  = pd.to_numeric(snap_com_fluxo.get('vol_comprador', pd.Series([np.nan]*len(snap_com_fluxo))), errors='coerce')
        vol_v_col  = pd.to_numeric(snap_com_fluxo.get('vol_vendedor',  pd.Series([np.nan]*len(snap_com_fluxo))), errors='coerce')

        delta_total  = delta_col.sum()
        vol_c_total  = vol_c_col.sum()
        vol_v_total  = vol_v_col.sum()
        vol_total    = vol_c_total + vol_v_total

        m['tem_fluxo']      = True
        m['delta_acum']     = int(delta_total)
        m['razao_agressao'] = round(vol_c_total / vol_total, 3) if vol_total > 0 else None
        m['janela_fluxo_n'] = len(snap_com_fluxo)

        # Período coberto pela janela de fluxo
        dt_ini = snap_com_fluxo['datetime'].iloc[0]
        dt_fim = snap_com_fluxo['datetime'].iloc[-1]
        m['fluxo_periodo'] = f"{pd.to_datetime(dt_ini).strftime('%H:%M')}–{pd.to_datetime(dt_fim).strftime('%H:%M')}"

    # S/R — campos escalares legados (alias do rank 1 — ai_analyst.py depende destes)
    m['sr_acima']            = ultimo.get('sr_acima', np.nan)
    m['sr_abaixo']           = ultimo.get('sr_abaixo', np.nan)
    m['dist_sr_acima_atr']   = ultimo.get('dist_sr_acima_atr', np.nan)
    m['dist_sr_abaixo_atr']  = ultimo.get('dist_sr_abaixo_atr', np.nan)
    m['qualidade_sr_acima']  = _safe_int(ultimo.get('qualidade_sr_acima', 0))
    m['qualidade_sr_abaixo'] = _safe_int(ultimo.get('qualidade_sr_abaixo', 0))

    # S/R multi-nível (Fase 1) — até N_NIVEIS_SR níveis de cada lado, ordenados
    # por proximidade (rank 1 = mais próximo). Degrada graciosamente: se o
    # parquet ainda não tiver as colunas novas (schema antigo, antes de rodar
    # `python run_backtest.py --features`), a lista simplesmente fica mais
    # curta — nunca lança KeyError.
    def _extrair_niveis(lado: str) -> list:
        niveis = []
        for rank in range(1, cfg.N_NIVEIS_SR + 1):
            pref = f'sr_{lado}_{rank}'
            preco = ultimo.get(pref, np.nan)
            if pref not in snap.columns or (isinstance(preco, float) and np.isnan(preco)):
                continue
            niveis.append({
                'rank': rank,
                'preco': preco,
                'toques': _safe_int(ultimo.get(f'{pref}_toques', 0)),
                'idade': _safe_int(ultimo.get(f'{pref}_idade', 0)),
                'violado': _safe_bool(ultimo.get(f'{pref}_violado', False)),
                'forte': _safe_bool(ultimo.get(f'{pref}_forte', False)),
                'dist_atr': ultimo.get(f'{pref}_dist_atr', np.nan),
                'zona_min': ultimo.get(f'{pref}_zona_min', np.nan),
                'zona_max': ultimo.get(f'{pref}_zona_max', np.nan),
                'status': str(ultimo.get(f'{pref}_status', 'ativa')),
                'rejeicao_media': ultimo.get(f'{pref}_rejeicao_media', np.nan),
                'agressao_media': ultimo.get(f'{pref}_agressao_media', np.nan),
                'impulso_medio': ultimo.get(f'{pref}_impulso_medio', np.nan),
                'forca_toques': ultimo.get(f'{pref}_forca_toques', np.nan),
                'zona_espelho_id': ultimo.get(f'{pref}_zona_espelho_id', np.nan),
                'prob_bounce': ultimo.get(f'{pref}_prob_bounce', np.nan),
                'rompimento_hoje': str(ultimo.get(f'{pref}_rompimento_hoje', 'nenhum')),
            })
        return niveis

    m['niveis_sr_acima']  = _extrair_niveis('acima')
    m['niveis_sr_abaixo'] = _extrair_niveis('abaixo')

    # Build-up
    m['buildup_ativo']   = _safe_bool(ultimo.get('buildup_ativo', False))
    m['buildup_duracao'] = _safe_int(ultimo.get('buildup_duracao', 0))
    m['buildup_pct_atr'] = ultimo.get('buildup_pct_atr', np.nan)

    # Double pressure
    m['double_pressure'] = _safe_bool(ultimo.get('double_pressure', False))
    m['dp_streak']       = _safe_int(ultimo.get('dp_streak', 0))
    m['dp_qualidade']    = str(ultimo.get('dp_qualidade', '—'))

    # -------------------------------------------------------------------------
    # SCORES para os velocímetros — baseados em métricas da MESMA janela
    # -------------------------------------------------------------------------
    score = 0.5
    if m['tendencia'] == 'alta':   score += 0.15
    elif m['tendencia'] == 'baixa': score -= 0.15
    if m['fase'] == 'inicio':       score += 0.05
    elif m['fase'] == 'exaustao':   score -= 0.10
    if m['tem_fluxo'] and m['delta_acum'] is not None:
        if m['delta_acum'] > 0:    score += 0.15
        elif m['delta_acum'] < 0:  score -= 0.15
    if m['double_pressure']:
        dir_c = ultimo.get('direcao_candle', 0)
        score += 0.05 if dir_c == 1 else -0.05
    m['score_forca'] = max(0.0, min(1.0, score))

    ctx = 0.5
    if m['tendencia'] in ('alta', 'baixa') and m['fase'] in ('inicio', 'meio'):
        ctx += 0.2
    if m['saude'] == 'forte':  ctx += 0.1
    elif m['saude'] == 'fraca': ctx -= 0.1
    if m['buildup_ativo']:     ctx += 0.1
    if m['dp_qualidade'] in ('alta', 'media'): ctx += 0.1
    m['score_contexto'] = max(0.0, min(1.0, ctx))

    return m


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
    cor_forca   = C['green'] if sf > 0.6 else (C['red'] if sf < 0.4 else C['yellow'])
    cor_ctx     = C['green'] if sc > 0.6 else (C['red'] if sc < 0.4 else C['yellow'])

    label_forca = 'COMPRADORES' if sf > 0.6 else ('VENDEDORES' if sf < 0.4 else 'NEUTRO')
    label_ctx   = 'FAVORÁVEL' if sc > 0.6 else ('DESFAVORÁVEL' if sc < 0.4 else 'NEUTRO')

    return html.Div([
        dcc.Graph(id='gauge-forca',
                  figure=gauge_cockpit(sf, 'FORÇA', label_forca, cor_forca),
                  config={'displayModeBar': False},
                  style={'width': '200px'}),
        dcc.Graph(id='gauge-contexto',
                  figure=gauge_cockpit(sc, 'CONTEXTO', label_ctx, cor_ctx),
                  config={'displayModeBar': False},
                  style={'width': '200px'}),
    ], style={'display': 'flex', 'gap': '8px', 'alignItems': 'center'})


def _estilo_botao_ativo(nome: str, ativo_atual: str) -> dict:
    """Estilo do botão seletor de ativo (WIN/WDO/CORRELACAO) dado o ativo
    selecionado. Extraído do antigo `construir_hero` para ser reutilizado
    tanto no layout estático inicial quanto no callback dedicado
    `atualizar_estilo_ativo_selecionado` — o `html.Button` em si nunca é
    recriado, só o `style` muda."""
    ativo_flag = (ativo_atual == nome)
    base = {
        'padding': '8px 20px', 'borderRadius': '6px',
        'cursor': 'pointer', 'fontFamily': FONTS['display'],
        'fontSize': '13px', 'transition': 'transform 0.08s ease, background 0.15s ease',
    }
    if nome == 'WIN':
        base.update({
            'background': C['green'] if ativo_flag else C['panel'],
            'color': C['bg'] if ativo_flag else C['text2'],
            'border': f'1px solid {C["green"]}',
            'fontWeight': '700',
        })
    elif nome == 'WDO':
        base.update({
            'background': C['green'] if ativo_flag else C['panel'],
            'color': C['bg'] if ativo_flag else C['text2'],
            'border': f'1px solid {C["border"]}',
            'fontWeight': '700',
        })
    else:  # 'CORRELACAO'
        base.update({
            'background': C['panel'], 'color': C['text2'],
            'border': f'1px solid {C["border"]}',
        })
    return base


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

    # --- CARD 2: TENDÊNCIA
    tend   = m.get('tendencia', '—')
    fase   = m.get('fase', '—')
    saude  = m.get('saude', '—')
    cor_tend = C['green'] if tend == 'alta' else (C['red'] if tend == 'baixa' else C['yellow'])

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
        html.Div([card_qm, card_tend, card_vol, card_bu, card_dp, card_jan],
                 style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
    ])


def construir_price_action(df: pd.DataFrame, m: dict) -> html.Div:
    """
    Seção Price Action — sem gráfico de candles (Jesué já tem o seu no ProfitPRO).
    Mostra os N_NIVEIS_SR (default 3) níveis de S/R mais próximos de cada
    lado — ordenados por PROXIMIDADE (rank 1 = mais próximo, é o próximo a
    ser testado). Força/status/probabilidade não reordenam, só destacam
    visualmente qual nível é a "região mais forte" (mais antiga, sem
    violação por fechamento, 3+ toques — ver nivel_forte em features.py).
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
                'fontSize': '9px', 'fontWeight': '700', 'color': C['bg'],
                'background': C['gold'], 'borderRadius': '4px',
                'padding': '2px 6px', 'marginRight': '6px', 'letterSpacing': '0.05em',
            }))
        if nivel.get('violado'):
            badges.append(html.Span('VIOLADO', style={
                'fontSize': '9px', 'fontWeight': '700', 'color': C['text'],
                'background': C['red'], 'borderRadius': '4px',
                'padding': '2px 6px', 'marginRight': '6px', 'letterSpacing': '0.05em',
            }))
        status = nivel.get('status', 'ativa')
        if status in _STATUS_LABEL:
            label, cor_status = _STATUS_LABEL[status]
            badges.append(html.Span(label, style={
                'fontSize': '9px', 'fontWeight': '700', 'color': C['bg'],
                'background': cor_status, 'borderRadius': '4px',
                'padding': '2px 6px', 'marginRight': '6px', 'letterSpacing': '0.05em',
            }))
        if not _nan(nivel.get('zona_espelho_id')):
            badges.append(html.Span('ZONA ESPELHO', style={
                'fontSize': '9px', 'fontWeight': '700', 'color': C['bg'],
                'background': C['blue'], 'borderRadius': '4px',
                'padding': '2px 6px', 'marginRight': '6px', 'letterSpacing': '0.05em',
            }))
        # Sinal intradiário — escopo do próprio pregão, independente do status
        # estrutural acima (ver comentário em features.py::calcular_zonas_sr).
        romp_hoje = nivel.get('rompimento_hoje', 'nenhum')
        if romp_hoje == 'rompeu_hoje':
            badges.append(html.Span('ROMPEU HOJE', style={
                'fontSize': '9px', 'fontWeight': '700', 'color': C['bg'],
                'background': C['yellow'], 'borderRadius': '4px',
                'padding': '2px 6px', 'marginRight': '6px', 'letterSpacing': '0.05em',
            }))
        elif romp_hoje == 'pullback_confirmado_hoje':
            badges.append(html.Span('PULLBACK CONFIRMADO HOJE', style={
                'fontSize': '9px', 'fontWeight': '700', 'color': C['bg'],
                'background': C['green'], 'borderRadius': '4px',
                'padding': '2px 6px', 'marginRight': '6px', 'letterSpacing': '0.05em',
            }))

        borda_cor = C['gold'] if destaque else cor

        return html.Div([
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
        ], style={
            'background': C['panel'], 'border': f'1px solid {C["border"]}',
            'borderLeft': f'3px solid {borda_cor}',
            'borderRadius': '10px', 'padding': '14px', 'marginBottom': '10px',
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
        'borderRadius': '12px', 'padding': '20px',
        'marginBottom': '12px',
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

    return html.Div([
        secao('PRICE ACTION'),
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
        return html.Div([secao('BIG PLAYERS FOOTPRINTS'),
                         html.Div('Sem dados.', style={'color': C['text2']})])

    # Trabalha com os últimos 500 candles (snapshot de análise)
    snap = df.tail(500).copy().reset_index(drop=True)
    hoje = snap[snap['datetime'].dt.date == pd.Timestamp.now().date()]
    tem_fluxo = (snap['fluxo_disponivel'] == True) if 'fluxo_disponivel' in snap.columns else pd.Series([False]*len(snap))
    snap_fluxo = snap[tem_fluxo].reset_index(drop=True)

    def _card_bp(titulo, valor, conclusao, detalhe, cor):
        return html.Div([
            html.Div(titulo, style={
                'fontSize': '10px', 'color': C['text2'],
                'textTransform': 'uppercase', 'letterSpacing': '0.08em',
                'fontFamily': FONTS['display'], 'marginBottom': '6px',
            }),
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
        ], style={
            'background': C['panel'],
            'border': f'1px solid {C["border"]}',
            'borderLeft': f'3px solid {cor}',
            'borderRadius': '10px', 'padding': '12px 14px',
            'flex': '1', 'minWidth': '200px',
        })

    if snap_fluxo.empty:
        # Sem dado de tick — análise só por anatomia de candle
        return html.Div([
            secao('BIG PLAYERS FOOTPRINTS'),
            html.Div([
                html.Span('📊 ', style={'fontSize': '16px'}),
                html.Span(
                    'Fluxo de tick não disponível neste período. '
                    'A análise de big players requer dado de tick — disponível apenas '
                    'para os contratos mais recentes. '
                    'Análise de anatomia de candle disponível no Price Action.',
                    style={'fontSize': '12px', 'color': C['text2'],
                           'fontFamily': FONTS['body']}
                ),
            ], style={
                'background': C['panel'], 'border': f'1px solid {C["border"]}',
                'borderRadius': '10px', 'padding': '14px',
            }),
        ])

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
        cor_lado = C['yellow']

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
        cor_acao = C['yellow']
    else:
        acao = 'Fluxo sem sinal direcional claro — aguardar setup com confluência de S/R e tendência'
        cor_acao = C['text2']

    card_acao = html.Div([
        html.Div('O QUE FAZER COM ISSO', style={
            'fontSize': '10px', 'color': C['gold'],
            'textTransform': 'uppercase', 'letterSpacing': '0.08em',
            'fontFamily': FONTS['display'], 'marginBottom': '8px',
        }),
        html.Div(acao, style={
            'fontSize': '14px', 'color': cor_acao,
            'fontFamily': FONTS['body'], 'lineHeight': '1.6',
            'fontWeight': '500',
        }),
    ], style={
        'background': C['panel'],
        'border': f'1px solid {C["border"]}',
        'borderLeft': f'3px solid {C["gold"]}',
        'borderRadius': '10px', 'padding': '14px',
        'marginTop': '12px',
    })

    return html.Div([
        secao('BIG PLAYERS FOOTPRINTS'),
        html.Div([card_lado, card_abs, card_vol_bp],
                 style={'display': 'flex', 'gap': '10px', 'flexWrap': 'wrap'}),
        card_acao,
    ])


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

    # Store para mensagem pendente (para preloader)
    dcc.Store(id='store-mensagem-pendente', data=None),

    # Store para candle atual (atualizado a 1s)
    dcc.Store(id='store-candle-atual', data={}),

    # Store do ativo selecionado
    dcc.Store(id='store-ativo', data='WIN'),
    dcc.Store(id='store-metricas', data={}),

    # Interval rápido — 1s: atualiza preço, candle atual, fluxo
    dcc.Interval(id='interval-live', interval=1_000, n_intervals=0),
    # Interval lento — 30s: reconstrói features completas e blocos pesados
    dcc.Interval(id='interval-slow', interval=30_000, n_intervals=0),

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
            *[html.Button(label, id=f'tab-{key}', n_clicks=0, style={
                'background': 'transparent', 'border': 'none',
                'borderBottom': f'2px solid {C["gold"]}' if key == 'lab' else '2px solid transparent',
                'color': C['text'] if key == 'lab' else C['text2'],
                'padding': '8px 20px', 'cursor': 'pointer',
                'fontFamily': FONTS['display'], 'fontSize': '12px',
                'fontWeight': '600' if key == 'lab' else '400',
                'letterSpacing': '0.06em',
            }) for label, key in [
                ('QUANT LAB', 'lab'),
                ('BACKTESTING', 'back'),
                ('DIÁRIO', 'diary'),
                ('CONFIGURAÇÕES', 'config'),
            ]],
        ], style={'display': 'flex', 'gap': '4px'}),

        # Status de conexão fixo no cabeçalho
        html.Div([
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
                                    className='btn-ativo',
                                    style=_estilo_botao_ativo('WIN', 'WIN')),
                        html.Button('WDO', id='btn-wdo', n_clicks=0,
                                    className='btn-ativo',
                                    style=_estilo_botao_ativo('WDO', 'WIN')),
                        html.Button('WIN × WDO', id='btn-correlacao', n_clicks=0,
                                    className='btn-ativo',
                                    style=_estilo_botao_ativo('CORRELACAO', 'WIN')),
                    ], style={'display': 'flex', 'gap': '8px'}),
                ], style={'flex': '1', 'padding': '0 24px'}),
            ], style={
                'display': 'flex', 'alignItems': 'center',
                'gap': '16px', 'flexWrap': 'wrap',
            }),
        ], style={
            'background': f'linear-gradient(135deg, {C["panel"]} 0%, #0E0E18 100%)',
            'border': f'1px solid {C["border"]}',
            'borderRadius': '14px', 'padding': '24px',
            'marginBottom': '24px',
        }),

        # MARKET VIEW
        html.Div(id='market-view-conteudo'),

        # PRICE ACTION
        html.Div(id='price-action-conteudo', style={'marginTop': '24px'}),

        # BIG PLAYERS
        html.Div(id='big-players-conteudo', style={'marginTop': '24px'}),

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

        # Input + botão — altura fixa 68px
        html.Div([
            dcc.Input(
                id='chat-input', type='text',
                placeholder='Preço ou pergunta...',
                debounce=False, n_submit=0,
                style={
                    'flex': '1',
                    'background': C['bg'],
                    'border': f'1px solid {C["border"]}',
                    'borderRight': 'none',
                    'borderRadius': '20px 0 0 20px',
                    'padding': '10px 16px',
                    'color': C['text'],
                    'fontFamily': FONTS['body'],
                    'fontSize': '13px',
                    'outline': 'none',
                }
            ),
            html.Button('▶', id='btn-chat-enviar', n_clicks=0, style={
                'background': C['gold'], 'color': C['bg'],
                'border': 'none',
                'borderRadius': '0 20px 20px 0',
                'padding': '10px 18px',
                'cursor': 'pointer',
                'fontWeight': '700', 'fontSize': '14px',
                'flexShrink': '0',
            }),
        ], style={
            'display': 'flex',
            'alignItems': 'center',
            'padding': '10px 12px',
            'height': '68px',
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
        'width': '420px', 'height': '520px',
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
    Input('chat-input', 'n_submit'),
    State('chat-input', 'value'),
    State('chat-historico', 'children'),
    State('store-ativo', 'data'),
    prevent_initial_call=True,
)
def enviar_mensagem_chat(n_clicks, n_submit, texto, historico_atual, ativo):
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
            'background': '#1A1A28', 'borderRadius': '10px 10px 10px 2px',
            'padding': '10px 14px', 'fontSize': '13px',
            'fontFamily': FONTS['body'], 'color': C['text'],
            'maxWidth': '90%', 'borderLeft': f'2px solid {C["gold"]}',
        }),
    ], id='msg-preloader', style={'display': 'flex', 'justifyContent': 'flex-start'})

    historico_com_preloader = (historico_atual or []) + [msg_usuario, preloader]

    # Chama a IA (bloqueante — resposta real vem aqui mesmo)
    with lock:
        bases = estado['bases']
    df = bases.get(ativo if ativo != 'CORRELACAO' else 'WIN')
    m = extrair_metricas(df) if df is not None else {}

    try:
        import ai_analyst
        resposta_bruta = ai_analyst.analisar_com_pergunta_raw(m, texto, ativo)

        if isinstance(resposta_bruta, dict) and resposta_bruta.get('erro_429'):
            # Rate limit — calcula timer de espera
            segundos_espera = resposta_bruta.get('retry_after', 60)
            msg_ia = html.Div([
                html.Div([
                    html.Span('⏱ ', style={'color': C['yellow']}),
                    html.Span(f'Limite de requisições atingido. Aguarde {segundos_espera}s antes de enviar novamente.',
                              style={'color': C['yellow']}),
                ], style={
                    'background': '#1A1A14', 'borderRadius': '10px 10px 10px 2px',
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
                    'background': '#1A1414', 'borderRadius': '10px 10px 10px 2px',
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
                    'background': '#1A1A28', 'borderRadius': '10px 10px 10px 2px',
                    'padding': '10px 14px', 'fontSize': '13px',
                    'fontFamily': FONTS['body'], 'color': C['text'],
                    'maxWidth': '90%', 'borderLeft': f'2px solid {C["gold"]}',
                }),
            ], style={'display': 'flex', 'justifyContent': 'flex-start'})

            ts = datetime.now().strftime('%H:%M:%S')
            status_str = f'{ts} · {tokens_usados} tokens ({pct}% do limite)'

    except Exception as e:
        msg_ia = html.Div([
            html.Div(f'⚠ Erro inesperado: {str(e)[:60]}',
                     style={'color': C['red'], 'fontSize': '13px',
                            'fontFamily': FONTS['body']}),
        ], style={'display': 'flex'})
        status_str = '⚠ Erro inesperado'

    novo_historico = (historico_atual or []) + [msg_usuario, msg_ia]

    return novo_historico, status_str, '', None, ''




# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  SUPER QUANT — Anticovardia")
    print("  Abrindo em: http://127.0.0.1:8050")
    print("=" * 60)
    app.run(debug=False, host='127.0.0.1', port=8050)
