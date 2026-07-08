# =============================================================================
# metricas.py — Extração de métricas a partir da base de candles.
# =============================================================================
# Lógica pura (sem HTML/Dash) compartilhada entre os dois front-ends do
# Super Quant (dashboard.py — visual clássico, e dashboard_98.py — visual
# Windows 98) — extraído de dashboard.py pra evitar duplicar cálculo de
# métricas entre as duas interfaces.
# =============================================================================

import pandas as pd
import numpy as np

import config_backtest as cfg


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

    # Pivô imediato (tático, features.py::calcular_pivo_imediato) — o ÚLTIMO
    # fundo/topo confirmado, mesmo que ainda não tenha virado zona madura no
    # ranking acima (1 toque, sem reteste). Caso real que motivou (WIN,
    # 08/07/2026): o Mentor IA só enxergava a zona madura do ranking e
    # ignorava um fundo recém-formado mais perto do preço — a referência que
    # Jesué de fato monitora primeiro na leitura gráfica. Só é relevante
    # exibir quando NÃO está redundante com um dos níveis já rankeados acima
    # (`_ja_mapeado`). Degrada graciosamente se a coluna ainda não existir
    # (schema antigo).
    def _extrair_pivo_imediato(lado: str) -> dict | None:
        pref = f'pivo_imediato_{lado}'
        if pref not in snap.columns:
            return None
        preco = ultimo.get(pref, np.nan)
        if isinstance(preco, float) and np.isnan(preco):
            return None
        if _safe_bool(ultimo.get(f'{pref}_ja_mapeado', False)):
            return None
        return {
            'preco': preco,
            'idade': _safe_int(ultimo.get(f'{pref}_idade', 0)),
        }

    m['pivo_imediato_acima']  = _extrair_pivo_imediato('acima')
    m['pivo_imediato_abaixo'] = _extrair_pivo_imediato('abaixo')

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
