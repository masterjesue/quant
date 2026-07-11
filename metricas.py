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
import features


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


def _json_safe(v):
    """Converte tipos numpy/NaN (que `extrair_metricas` produz por vir de um
    `pd.Series`) para tipos nativos Python — `json.dumps` não serializa
    `numpy.int64`/`numpy.bool_` diretamente, e NaN vira `None` em vez do
    literal inválido "NaN"."""
    if v is None:
        return None
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if f != f else f  # f != f detecta NaN
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return v


def montar_snapshot_diario(m: dict) -> dict | None:
    """
    Recorte JSON-safe de `extrair_metricas()` para gravar junto de um trade
    do Diário — captura o que o motor via no instante do save, sem I/O novo
    (reaproveita o dict `m` já calculado a cada tick de 1s). Escopo: só
    CAPTURA (direção quant→diário). Comparar isso contra `nivel_sr_referencia`
    marcado à mão é um passo futuro registrado no plano do módulo Diário, não
    implementado aqui.

    Recorte deliberado, não o dict inteiro: fora ficam o bloco de fluxo/tape
    (sensível ao segundo exato, sem relação com comparar níveis S/R) e os
    aliases legados de rank 1 (`sr_acima`/`sr_abaixo`, redundantes com
    `niveis_sr_*[0]`).
    """
    if not m:
        return None

    def _nivel(n: dict) -> dict:
        return {campo: n.get(campo) for campo in (
            'rank', 'preco', 'toques', 'idade', 'violado', 'forte',
            'dist_atr', 'zona_min', 'zona_max', 'status',
            'prob_bounce', 'rompimento_hoje',
        )}

    snapshot = {
        'preco': m.get('preco'), 'atr': m.get('atr'), 'amp_dia_atr': m.get('amp_dia_atr'),
        'tendencia': m.get('tendencia'), 'fase': m.get('fase'), 'saude': m.get('saude'),
        'tipo_candle': m.get('tipo_candle'),
        'niveis_sr_acima': [_nivel(n) for n in m.get('niveis_sr_acima', [])],
        'niveis_sr_abaixo': [_nivel(n) for n in m.get('niveis_sr_abaixo', [])],
        'pivo_imediato_acima': m.get('pivo_imediato_acima'),
        'pivo_imediato_abaixo': m.get('pivo_imediato_abaixo'),
        'buildup_ativo': m.get('buildup_ativo'), 'buildup_duracao': m.get('buildup_duracao'),
        'buildup_pct_atr': m.get('buildup_pct_atr'),
        'double_pressure': m.get('double_pressure'), 'dp_qualidade': m.get('dp_qualidade'),
        'score_forca': m.get('score_forca'), 'score_contexto': m.get('score_contexto'),
    }
    return _json_safe(snapshot)


def candles_para_grafico(df: pd.DataFrame, n: int = 300) -> list:
    """OHLC no formato que o Lightweight Charts espera (ver assets_98/
    lwchart.js e assets/lwchart.js — um gráfico em cada front-end, mesma
    extração de dado) — `time` em epoch segundos. `Timestamp.timestamp()`
    num datetime naive assume fuso LOCAL do sistema (mesmo comportamento do
    `datetime.timestamp()` padrão do Python) — correto aqui porque servidor
    e navegador rodam na mesma máquina (uso único, localhost)."""
    if df is None or df.empty:
        return []
    d = df.tail(n)
    return [
        {'time': int(pd.Timestamp(dt).timestamp()),
         'open': float(o), 'high': float(h), 'low': float(l), 'close': float(c)}
        for dt, o, h, l, c in zip(d['datetime'], d['open'], d['high'], d['low'], d['close'])
    ]


def niveis_sr_para_grafico(m: dict) -> list:
    """Os mesmos níveis de S/R rankeados exibidos no Price Action (mais o
    pivô imediato, quando houver), no formato que assets_98/lwchart.js e
    assets/lwchart.js usam pra desenhar linhas horizontais
    (`createPriceLine`) — ver `desenharNiveisSR` em ambos. Reaproveita
    `m['niveis_sr_acima'/'abaixo']`, já calculado por `extrair_metricas`;
    não recalcula nada, só reformata pro gráfico."""
    def _valido(preco):
        return preco is not None and not (isinstance(preco, float) and np.isnan(preco))

    out = []
    for n in m.get('niveis_sr_acima', []):
        if _valido(n.get('preco')):
            out.append({'preco': float(n['preco']), 'tipo': 'resistencia',
                        'forte': bool(n.get('forte')), 'label': f"R{n.get('rank', '')}"})
    for n in m.get('niveis_sr_abaixo', []):
        if _valido(n.get('preco')):
            out.append({'preco': float(n['preco']), 'tipo': 'suporte',
                        'forte': bool(n.get('forte')), 'label': f"S{n.get('rank', '')}"})
    pivo_acima = m.get('pivo_imediato_acima')
    if pivo_acima and _valido(pivo_acima.get('preco')):
        out.append({'preco': float(pivo_acima['preco']), 'tipo': 'pivo',
                     'forte': False, 'label': 'Topo em formação'})
    pivo_abaixo = m.get('pivo_imediato_abaixo')
    if pivo_abaixo and _valido(pivo_abaixo.get('preco')):
        out.append({'preco': float(pivo_abaixo['preco']), 'tipo': 'pivo',
                     'forte': False, 'label': 'Fundo em formação'})
    return out


def _serie_mme21(df: pd.DataFrame) -> pd.Series:
    """EMA21 sobre o close — `adjust=False` é o cálculo recursivo padrão
    (cada valor pesa no anterior), o mesmo que qualquer plataforma de
    gráfico usa pra média móvel exponencial."""
    return df['close'].ewm(span=cfg.MME21_PERIODO, adjust=False).mean()


def mme21_para_grafico(df: pd.DataFrame, n: int = 300) -> list:
    """Série da MME21 no formato que o Lightweight Charts espera pra uma
    linha (`addLineSeries` + `setData`) — ver assets_98/lwchart.js e
    assets/lwchart.js. Mesma extração de tempo de `candles_para_grafico`,
    mas 1 valor por candle (não OHLC) — calcula a EMA sobre TODO o
    histórico disponível (a EMA "esquece" o passado bem distante sozinha
    via decaimento exponencial) e só serializa os últimos `n`."""
    if df is None or df.empty:
        return []
    mme = _serie_mme21(df)
    d = df[['datetime']].tail(n)
    valores = mme.tail(n)
    return [
        {'time': int(pd.Timestamp(dt).timestamp()), 'value': float(v)}
        for dt, v in zip(d['datetime'], valores)
    ]


def mme21_ponto_live(df: pd.DataFrame, preco_live: float = None) -> dict | None:
    """Só o ponto da MME21 no candle EM FORMAÇÃO — mesmo espírito do candle
    vivo em `candles_para_grafico(df, n=1)`: barato (não serializa a série
    inteira), aplicado no cliente via `.update()`, nunca `.setData()`. Se
    `preco_live` vier (preço em tempo real, candle ainda não fechou), a EMA
    do último ponto é recalculada com esse preço no lugar do close
    persistido — mesma ideia de "respirar" no tick de 1s que os candles
    já fazem."""
    if df is None or df.empty:
        return None
    mme = _serie_mme21(df)
    ultimo_dt = df['datetime'].iloc[-1]
    valor = float(mme.iloc[-1])
    if preco_live is not None and len(mme) > 1:
        alpha = 2.0 / (cfg.MME21_PERIODO + 1)
        ema_anterior = float(mme.iloc[-2])
        valor = alpha * preco_live + (1 - alpha) * ema_anterior
    return {'time': int(pd.Timestamp(ultimo_dt).timestamp()), 'value': valor}


def analisar_mme21(df: pd.DataFrame, tendencia: str = None) -> dict:
    """Leitura quantitativa da MME21 pra seção "Rastreador de Tendência" —
    mesma linguagem do Price Action (toques/força/probabilidade), via
    `features.analisar_linha_generica`, mas com a regra própria do Jesué
    pra médias móveis: testada e respeitada só 1x já conta como forte
    (`TOQUES_MINIMOS_MME21_FORTE=1`, mais permissivo que o S/R estático), e
    NUNCA operável em mercado lateral — a MME21 não é o operacional
    principal dele, só ajuda em dias de tendência sem entrada no price
    action puro."""
    if df is None or df.empty:
        return {'operavel': False, 'mensagem': 'Base de dados não encontrada.'}

    mme = _serie_mme21(df)
    analise = features.analisar_linha_generica(
        df, mme.values, janela=cfg.JANELA_RESPEITO_MME21,
        toques_min_forte=cfg.TOQUES_MINIMOS_MME21_FORTE,
    )

    lateral = (tendencia == 'lateral')
    analise['operavel'] = not lateral
    analise['mensagem'] = (
        'Mercado lateral — MME21 não deve ser operada aqui. Aguarde tendência definida.'
        if lateral else None
    )
    return analise
