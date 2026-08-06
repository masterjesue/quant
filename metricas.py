# =============================================================================
# metricas.py — Extração de métricas a partir da base de candles.
# =============================================================================
# Lógica pura (sem HTML/Dash) compartilhada entre os dois front-ends do
# Super Quant (dashboard.py — visual clássico, e dashboard_98.py — visual
# Windows 98) — extraído de dashboard.py pra evitar duplicar cálculo de
# métricas entre as duas interfaces. Também guarda utilidades de gráfico
# puras (sem `go.Figure`/paleta) compartilhadas entre `dashboard.py` e
# `diario_app_classico.py` — 2 processos Dash separados que já duplicam
# PALETA_GAUGE/FONTS/`_hex_para_rgba` por design (tokens de estilo, ver
# `_hex_para_rgba` em cada arquivo), mas não deveriam duplicar ALGORITMO
# (ver `segmentos_cor_por_sinal` abaixo).
# =============================================================================

import pandas as pd
import numpy as np

import config_backtest as cfg
import features


_SIMBOLO_MOEDA = {"BRL": "R$", "USD": "US$"}


def simbolo_moeda(moeda: str = "BRL") -> str:
    """`"R$"`/`"US$"` — símbolo isolado, pra rótulos de grupo/cabeçalho que não formatam um valor."""
    return _SIMBOLO_MOEDA.get(moeda, moeda)


def formatar_moeda(valor: float, moeda: str = "BRL", casas: int = 2, sinal: bool = True) -> str:
    """`"R$ +123.45"`/`"US$ -8.00"` — símbolo dinâmico por moeda (Fase 4 da
    integração cTrader, 2026-08-06: aba Estatísticas passou a poder filtrar
    por ativo, e ativos forex são nativos em USD, não BRL). Único ponto que
    formata valor monetário na aba Estatísticas do `dashboard.py` — os
    demais literais "R$" do arquivo (eixos de gráfico de outros painéis,
    cards fora da Estatísticas) são inerentemente WIN/WDO e ficam fora
    deste helper."""
    simbolo = _SIMBOLO_MOEDA.get(moeda, moeda)
    marca = "+" if sinal else ""
    return f"{simbolo} {valor:{marca}.{casas}f}"


def segmentos_cor_por_sinal(xs: list, ys: list) -> list:
    """Quebra uma série temporal em segmentos verde (>=0)/vermelho (<0),
    inserindo um ponto interpolado exatamente no cruzamento de zero — dá o
    efeito "linha muda de cor exatamente onde cruza o eixo" do equity
    curve estilo TradeZella (extraído de `dashboard.py::_grafico_evolucao_
    saldo`, 2026-08-04, pra reuso idêntico em `diario_app_classico.py`
    sem duplicar a interpolação). Retorna lista de `(sinal, xs_seg,
    ys_seg)`."""
    xs_num = pd.to_datetime(xs).astype('int64').astype(float)
    segmentos = []
    atual_x, atual_y = [xs[0]], [ys[0]]
    sinal_atual = 'pos' if ys[0] >= 0 else 'neg'
    for i in range(1, len(ys)):
        sinal = 'pos' if ys[i] >= 0 else 'neg'
        if sinal != sinal_atual:
            y_prev, y_cur = ys[i - 1], ys[i]
            frac = y_prev / (y_prev - y_cur) if (y_prev - y_cur) != 0 else 0.0
            x_cruz_num = xs_num[i - 1] + frac * (xs_num[i] - xs_num[i - 1])
            x_cruz = pd.Timestamp(int(x_cruz_num))
            atual_x.append(x_cruz); atual_y.append(0.0)
            segmentos.append((sinal_atual, atual_x, atual_y))
            atual_x, atual_y = [x_cruz], [0.0]
            sinal_atual = sinal
        atual_x.append(xs[i]); atual_y.append(ys[i])
    segmentos.append((sinal_atual, atual_x, atual_y))
    return segmentos


def extrair_metricas(df: pd.DataFrame, n_snapshot: int = cfg.JANELA_SNAPSHOT_CURTO_PRAZO) -> dict:
    """
    Extrai métricas do snapshot atual (últimos `n_snapshot` candles — 5
    pregões, ver `cfg.JANELA_SNAPSHOT_CURTO_PRAZO`).

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
        rank_idx_arr = df.attrs.get(f'_rank_idx_{lado}')  # usa o df PARÂMETRO
        # (histórico completo), não o `snap` truncado em .tail(n_snapshot) —
        # preservação de .attrs através de fatiamento/copy não deve ser
        # assumida (pandas trata isso como "experimental").
        for rank in range(1, cfg.N_NIVEIS_SR + 1):
            pref = f'sr_{lado}_{rank}'
            preco = ultimo.get(pref, np.nan)
            if pref not in snap.columns or (isinstance(preco, float) and np.isnan(preco)):
                continue
            cluster_id = None
            if rank_idx_arr is not None and len(rank_idx_arr):
                k = int(rank_idx_arr[-1, rank - 1])
                cluster_id = k if k != -1 else None
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
                'cluster_id': cluster_id,
                'bipolar': _info_bipolar(df, cluster_id),
                'regime': montar_regime(_detalhes_toques_cluster(df, cluster_id),
                                         _fronteira_regime_cluster(df, cluster_id)),
                # Blocos de força (2026-07-13, ver
                # especificacao_forca_zonas_price_action.md) — já calculados
                # por features.calcular_prob_niveis_sr, só expostos aqui.
                # Mesma nomenclatura de analisar_linha_generica (Traçados/
                # MME21), pra os cards reaproveitarem o mesmo código de render.
                'combustivel_score': ultimo.get(f'{pref}_combustivel_score', np.nan),
                'aproximacao_score': ultimo.get(f'{pref}_aproximacao_score', np.nan),
                'rejeicao_score': ultimo.get(f'{pref}_rejeicao_score', np.nan),
                'acao': {
                    'postura': ultimo.get(f'{pref}_postura'),
                    'magnitude_esperada': ultimo.get(f'{pref}_magnitude_esperada'),
                    'invalidacao': ultimo.get(f'{pref}_invalidacao', np.nan),
                },
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

    # Pivô em formação (tático, features.py::detectar_pivo_em_formacao) — o
    # candidato a topo/fundo mais recente que AINDA não teve chance de
    # confirmar (menos que N_CONFIRM_PIVOS candles depois dele). Responde
    # "esse preço (ex.: mínima do dia) já é um fundo validado, ou ainda está
    # esperando confirmação?" — pergunta distinta de pivo_imediato (que só
    # mostra pivôs JÁ confirmados).
    _pivo_formacao = features.detectar_pivo_em_formacao(df)
    m['pivo_em_formacao_acima']  = _pivo_formacao.get('topo')
    m['pivo_em_formacao_abaixo'] = _pivo_formacao.get('fundo')

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


def _tracado_snapshot(t: dict) -> dict:
    """Recorte JSON-safe de 1 traçado já analisado (`analisar_tracados`),
    pro snapshot do Diário — blocos de força COMPLETOS (não só prob_bounce
    — pedido explícito do Jesué, Fase 3.2 do Modo Aprendizagem: "o quant
    tem que detalhar mais"), pra `avaliar_aderencia_tracados` comparar
    depois contra `nivel_sr_referencia`."""
    acao = t.get('acao') or {}
    return {
        'id_tracado': t.get('id_tracado'), 'preco': t.get('linha_atual'),
        'tipo': t.get('tipo'), 'toques': t.get('toques'), 'forte': t.get('forte'),
        'violado': t.get('violado'), 'prob_bounce': t.get('prob_bounce'),
        'combustivel_score': t.get('combustivel_score'),
        'aproximacao_score': t.get('aproximacao_score'),
        'rejeicao_score': t.get('rejeicao_score'),
        'postura': acao.get('postura'), 'magnitude_esperada': acao.get('magnitude_esperada'),
        'alvo_pontos': acao.get('alvo_pontos'), 'stop_pontos': acao.get('stop_pontos'),
    }


def montar_snapshot_diario(m: dict, tracados_analisados: list = None) -> dict | None:
    """
    Recorte JSON-safe de `extrair_metricas()` (+ traçados analisados) para
    gravar junto de um trade do Diário — captura o que o motor via no
    instante do save, sem I/O novo (reaproveita `m` já calculado + os
    traçados que o CHAMADOR já tem em mãos naquele momento). Escopo: só
    CAPTURA (direção quant→diário) — comparar isso contra
    `nivel_sr_referencia` marcado à mão é `diario_trade.
    avaliar_aderencia_tracados`, chamado à parte, sob demanda.

    `tracados_analisados` (2026-07-21, Fase 3.2): lista vinda de
    `metricas.analisar_tracados(df_ativo, tracados.listar_tracados(ativo))`
    — substitui os antigos `niveis_sr_acima`/`niveis_sr_abaixo` (motor
    automático desativado, Modo Aprendizagem.txt — ficariam sempre vazios).
    Snapshots salvos ANTES desta mudança não têm a chave `tracados` —
    `avaliar_aderencia_tracados` já trata isso como "não elegível pra
    comparação", mesmo raciocínio de sempre pra dado migrado/antigo.

    Recorte deliberado, não o dict inteiro: fora fica o bloco de fluxo/tape
    (sensível ao segundo exato, sem relação com comparar níveis S/R).
    """
    if not m:
        return None

    snapshot = {
        'preco': m.get('preco'), 'atr': m.get('atr'), 'amp_dia_atr': m.get('amp_dia_atr'),
        'tendencia': m.get('tendencia'), 'fase': m.get('fase'), 'saude': m.get('saude'),
        'tipo_candle': m.get('tipo_candle'),
        'tracados': [_tracado_snapshot(t) for t in (tracados_analisados or [])],
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


def _unwrap(valor):
    """Desembrulha um `features._SemDeepcopy` (estrutura grande de detalhe
    por toque, protegida do deepcopy que `pandas.NDFrame.__finalize__`
    dispara em quase toda operação — ver a classe em features.py). Passa
    através qualquer outro valor sem mudança."""
    return valor.valor if hasattr(valor, 'valor') else valor


def _info_bipolar(df: pd.DataFrame, cluster_id) -> dict | None:
    """
    Reconstrói a história de bipolaridade (rompimento válido → reteste
    confirmado → zona espelho com papel invertido) de um cluster do motor
    automático, sem re-rodar clustering — só lê o que
    `features.calcular_zonas_sr` já expôs via `df.attrs`
    (`_clusters_sr`/`_zonas_espelho`/`_zona_espelho_id_por_cluster`).
    Funciona tanto pra um cluster ORIGEM (que já rompeu e gerou um espelho)
    quanto pro próprio cluster ESPELHO (que sabe de onde veio). Retorna
    `None` se não houver dado disponível (schema antigo, cluster sem
    reversão, ou `cluster_id` ausente).
    """
    if cluster_id is None:
        return None
    try:
        cluster_id = int(cluster_id)
    except (TypeError, ValueError):
        return None
    if cluster_id < 0:
        return None

    clusters = df.attrs.get('_clusters_sr')
    zonas_espelho = _unwrap(df.attrs.get('_zonas_espelho'))
    zona_espelho_id_por_cluster = df.attrs.get('_zona_espelho_id_por_cluster')
    if clusters is None or zonas_espelho is None or zona_espelho_id_por_cluster is None:
        return None

    n_clusters = len(clusters.get('centros', []))
    dts = df['datetime'].values

    def _fmt(z, papel, zona_origem_id, zona_espelho_id_val):
        return {
            'papel_no_cruzamento': papel,
            'zona_origem_id': int(zona_origem_id),
            'zona_espelho_id': int(zona_espelho_id_val),
            'idx_rompimento': int(z['idx_rompimento']),
            'timestamp_rompimento': pd.Timestamp(dts[int(z['idx_rompimento'])]).isoformat(),
            'idx_reteste': int(z['idx_criacao']),
            'timestamp_reteste': pd.Timestamp(dts[int(z['idx_criacao'])]).isoformat(),
            'forca_herdada': float(z['forca_herdada']),
        }

    if cluster_id >= n_clusters:
        idx_novo = cluster_id - n_clusters
        if idx_novo < 0 or idx_novo >= len(zonas_espelho):
            return None
        z = zonas_espelho[idx_novo]
        return _fmt(z, 'espelho', z['origem_k'], cluster_id)

    if cluster_id >= len(zona_espelho_id_por_cluster):
        return None
    espelho_id = zona_espelho_id_por_cluster[cluster_id]
    if espelho_id is None or int(espelho_id) == -1:
        return None
    idx_novo = int(espelho_id) - n_clusters
    if idx_novo < 0 or idx_novo >= len(zonas_espelho):
        return None
    z = zonas_espelho[idx_novo]
    return _fmt(z, 'origem', cluster_id, espelho_id)


def _detalhes_toques_cluster(df: pd.DataFrame, cluster_id) -> list:
    """Detalhe por toque (data/hora, tipo de pivô, rejeição/agressão/
    impulso/peso) de um cluster do motor automático — clusters originais
    via `_clusters_sr['detalhe_toques_zona']`, espelhos via
    `_zonas_espelho[...]['detalhe_toques']`. Lista vazia se não houver
    dado (schema antigo ou cluster_id ausente)."""
    if cluster_id is None:
        return []
    cluster_id = int(cluster_id)
    clusters = df.attrs.get('_clusters_sr')
    if clusters is None:
        return []
    n_clusters = len(clusters.get('centros', []))
    if cluster_id < n_clusters:
        detalhe_por_cluster = _unwrap(clusters.get('detalhe_toques_zona'))
        if detalhe_por_cluster is None or cluster_id >= len(detalhe_por_cluster):
            return []
        return detalhe_por_cluster[cluster_id]
    zonas_espelho = _unwrap(df.attrs.get('_zonas_espelho'))
    if zonas_espelho is None:
        return []
    idx_novo = cluster_id - n_clusters
    if idx_novo < 0 or idx_novo >= len(zonas_espelho):
        return []
    return zonas_espelho[idx_novo].get('detalhe_toques', [])


def _fronteira_regime_cluster(df: pd.DataFrame, cluster_id) -> list:
    """Monta um `eventos_regime` (mesmo shape de
    `features.analisar_linha_generica`) de 1 evento a partir da última
    transição registrada em `_zonas_sr_status_timeline[cluster_id]` — lista
    vazia se o cluster nunca mudou de status (sempre 'ativa', regime
    único)."""
    if cluster_id is None:
        return []
    cluster_id = int(cluster_id)
    status_timeline = df.attrs.get('_zonas_sr_status_timeline')
    if not status_timeline or cluster_id >= len(status_timeline):
        return []
    tl = status_timeline[cluster_id]
    if len(tl) < 2:
        return []
    idx_ultimo = int(tl[-1][0])
    dts = df['datetime'].values
    return [{'idx': idx_ultimo, 'timestamp': pd.Timestamp(dts[idx_ultimo]).isoformat()}]


def localizar_zona_automatica(df: pd.DataFrame, preco: float, tolerancia_atr: float = None) -> dict | None:
    """
    Cruzamento traçado manual ↔ zona automática: verifica se `preco` cai
    dentro (± folga) de alguma zona já catalogada por
    `features.calcular_zonas_sr`, e devolve sua história de bipolaridade
    (via `_info_bipolar`), se houver. `None` se nada coincidir — comum,
    já que o clustering automático olha só ~23 dias
    (`JANELA_CLUSTERING_CANDLES`), bem menos que o histórico completo que
    os traçados enxergam (`janela=len(df)`).
    """
    clusters = df.attrs.get('_clusters_sr')
    if clusters is None:
        return None
    centros = clusters.get('centros')
    zona_min = clusters.get('zona_min')
    zona_max = clusters.get('zona_max')
    toques = clusters.get('toques')
    if centros is None or len(centros) == 0:
        return None

    tol_mult = tolerancia_atr if tolerancia_atr is not None else cfg.TOLERANCIA_CRUZAMENTO_TRACADO_ATR
    atr_med = float(np.nanmedian(df['atr'].values)) if 'atr' in df.columns else np.nan
    folga = tol_mult * atr_med if not np.isnan(atr_med) else 0.0

    candidatos = [k for k in range(len(centros))
                  if (zona_min[k] - folga) <= preco <= (zona_max[k] + folga)]
    if not candidatos:
        return None
    melhor_k = min(candidatos, key=lambda k: abs(preco - centros[k]))

    status_timeline = df.attrs.get('_zonas_sr_status_timeline')
    status_atual = 'ativa'
    if status_timeline and melhor_k < len(status_timeline):
        status_atual = status_timeline[melhor_k][-1][1]

    return {
        'cluster_id': int(melhor_k),
        'zona_min': float(zona_min[melhor_k]),
        'zona_max': float(zona_max[melhor_k]),
        'toques': int(toques[melhor_k]),
        'status': status_atual,
        'bipolar': _info_bipolar(df, melhor_k),
    }


def montar_regime(detalhes_toques: list, eventos_regime: list = None,
                   cruzamento_automatico: dict = None) -> dict:
    """
    Agrega `detalhes_toques` (shape de `features.analisar_linha_generica`
    ou de `_detalhes_toques_cluster`) em blocos REGIME_ATUAL/REGIME_ANTIGO
    — puramente numérico, a leitura em texto fica por conta de cada
    dashboard (mesmo split de sempre entre fatos aqui e prosa lá).

    A fronteira entre os regimes vem do último evento em `eventos_regime`
    (shape `{'idx','timestamp',...}`, de `analisar_linha_generica` ou de
    `_fronteira_regime_cluster`) — a MENOS que `cruzamento_automatico`
    traga uma bipolaridade confirmada pelo motor automático (rompimento +
    reteste reais, não só a flag simples de `violado`), que PREVALECE por
    ser a história de verdade, não uma aproximação.

    Cada bloco ganha `toques`, `toques_fortes` (só os com
    `tipo_pivo=='forte'`) e `forte` — critério corrigido (2026-07-13):
    exige toques dentro do PRÓPRIO bloco (nunca o histórico inteiro) E
    pelo menos `TOQUES_FORTES_MINIMOS_TRACADO` toques com confirmação de 3
    candles, não só toques fracos de 1 candle (achado real: sem isso,
    qualquer nível dentro de uma consolidação longa vinha sempre "FORTE").
    """
    detalhes_toques = detalhes_toques or []
    eventos_regime = eventos_regime or []

    fronteira_idx = eventos_regime[-1]['idx'] if eventos_regime else None
    fronteira_timestamp = eventos_regime[-1]['timestamp'] if eventos_regime else None

    bip = cruzamento_automatico.get('bipolar') if cruzamento_automatico else None
    if bip:
        fronteira_idx = bip['idx_reteste']
        fronteira_timestamp = bip['timestamp_reteste']

    def _bloco(lista):
        toques = len(lista)
        toques_fortes = sum(1 for d in lista if d.get('tipo_pivo') == 'forte')
        forte = (toques >= cfg.TOQUES_MINIMOS_TRACADO_MANUAL_FORTE and
                 toques_fortes >= cfg.TOQUES_FORTES_MINIMOS_TRACADO)
        pesos = [d['peso'] for d in lista if d.get('peso') is not None]
        forca_toques = round(float(sum(pesos)), 2) if pesos else 0.0
        rejeicoes = [d['rejeicao'] for d in lista if d.get('rejeicao') is not None]
        rejeicao_media = (round(float(np.average(rejeicoes, weights=pesos)), 3)
                           if pesos and rejeicoes else None)
        detalhes_ordenados = sorted(lista, key=lambda d: d['idx'], reverse=True)
        return {
            'toques': toques,
            'toques_fortes': toques_fortes,
            'forte': bool(forte),
            'forca_toques': forca_toques,
            'rejeicao_media': rejeicao_media,
            'detalhes': detalhes_ordenados[:cfg.MAX_TOQUES_TIMELINE_EXIBIDOS],
        }

    if fronteira_idx is None:
        return {
            'tem_divisao': False,
            'fronteira_idx': None, 'fronteira_timestamp': None,
            'regime_atual': _bloco(detalhes_toques), 'regime_antigo': None,
        }

    atuais = [d for d in detalhes_toques if d['idx'] >= fronteira_idx]
    antigos = [d for d in detalhes_toques if d['idx'] < fronteira_idx]
    return {
        'tem_divisao': True,
        'fronteira_idx': int(fronteira_idx),
        'fronteira_timestamp': fronteira_timestamp,
        'regime_atual': _bloco(atuais),
        'regime_antigo': _bloco(antigos),
    }


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


def _classificar_tendencia_serie(valores: np.ndarray, atrs: np.ndarray, janela: int) -> str:
    """Classifica 'alta'/'baixa'/'lateral' pelo deslocamento LÍQUIDO da
    série (MME21) do início ao fim da janela, normalizado pelo ATR médio do
    período — mesma doutrina de limiar-em-múltiplos-de-ATR do resto do
    motor (`cfg.LIMIAR_TENDENCIA_RASTREADOR_ATR`), nunca z-score. Usada por
    `analisar_rastreador_tendencia` pra dar a CADA tier sua própria leitura
    (ver comentário de `cfg.LIMIAR_TENDENCIA_RASTREADOR_ATR` — corrige o
    bug real de 2026-07-20 onde um único booleano global decidia por
    todos os 5 cards)."""
    n = len(valores)
    janela = min(janela, n)
    if janela < 2:
        return 'lateral'
    ini = n - janela
    delta = float(valores[-1] - valores[ini])
    atr_medio = float(np.nanmean(atrs[ini:])) if len(atrs[ini:]) else np.nan
    if np.isnan(atr_medio) or atr_medio <= 0:
        return 'lateral'
    desloc_atr = delta / atr_medio
    if desloc_atr >= cfg.LIMIAR_TENDENCIA_RASTREADOR_ATR:
        return 'alta'
    if desloc_atr <= -cfg.LIMIAR_TENDENCIA_RASTREADOR_ATR:
        return 'baixa'
    return 'lateral'


def analisar_rastreador_tendencia(df: pd.DataFrame) -> dict:
    """
    Seção "Rastreador de Tendência" (Modo Aprendizagem.txt, 2026-07-20) —
    retorna `{'tiers': {nivel: {...}}, 'mme21': {...}}`:

    - `tiers`: 5 leituras de DIREÇÃO por escala (Maior/Média/Menor/Micro,
      ver `cfg.NIVEIS_RASTREADOR_TENDENCIA`, + "Atual" =
      `cfg.JANELA_TENDENCIA_ATUAL` candles) — cada uma SÓ
      `{'linha_atual', 'tendencia' ('alta'/'baixa'/'lateral'), 'janela'}`,
      via `_classificar_tendencia_serie` (deslocamento líquido da MME21
      na janela do tier, normalizado por ATR).
    - `mme21`: 1 leitura ÚNICA de toques/força/probabilidade da MME21 (
      mesmo motor de sempre, `features.analisar_linha_generica`), na sua
      própria janela natural `cfg.JANELA_RESPEITO_MME21` — NÃO repetida
      por tier.

    **Correção de arquitetura 2026-07-21** (achado real do Jesué): a
    versão anterior rodava o motor de TOQUES da MME21 (toques/força/
    prob_bounce/leitura) em CADA um dos 5 tiers, produzindo textos como
    "MME21 testada e respeitada nos últimos 565 candles" — sem sentido
    pra uma média rápida (equivaleria a dizer que ficou 5 pregões inteiros
    sem ser cruzada). A spec original já separava os dois conceitos:
    "Traga a MME21 pra essa seção da MESMA FORMA que já mostra" (1 bloco,
    doutrina antiga de `analisar_mme21`) + "Tendência Atual" (mais um
    rótulo de DIREÇÃO, não outra rodada do motor de toques).

    A MME21 é calculada UMA VEZ sobre `df` inteiro (nunca truncado por
    nível/janela — EMA é recursiva, cold-start dá valor errado) — `tiers`
    e `mme21` compartilham a mesma série `_serie_mme21(df)`.
    """
    if df is None or df.empty:
        vazio_tier = {'linha_atual': None, 'tendencia': None}
        vazio_mme21 = {'linha_atual': None, 'operavel': False, 'mensagem': 'Base de dados não encontrada.'}
        return {
            'tiers': {nivel: dict(vazio_tier) for nivel in (*cfg.NIVEIS_RASTREADOR_TENDENCIA, 'atual')},
            'mme21': vazio_mme21,
        }

    mme = _serie_mme21(df)
    atrs = pd.to_numeric(df['atr'], errors='coerce').values if 'atr' in df.columns else np.full(len(df), np.nan)
    linha_atual = float(mme.values[-1])
    mensagem_lateral = 'Mercado lateral — MME21 não deve ser operada aqui. Aguarde tendência definida.'

    tiers = {}
    for nivel, janela in {**cfg.NIVEIS_RASTREADOR_TENDENCIA, 'atual': cfg.JANELA_TENDENCIA_ATUAL}.items():
        tiers[nivel] = {
            'linha_atual': linha_atual,
            'tendencia': _classificar_tendencia_serie(mme.values, atrs, janela),
            'janela': janela,
        }

    mme21_analise = features.analisar_linha_generica(
        df, mme.values, janela=cfg.JANELA_RESPEITO_MME21,
        toques_min_forte=cfg.TOQUES_MINIMOS_MME21_FORTE,
    )
    tendencia_mme21 = _classificar_tendencia_serie(mme.values, atrs, cfg.JANELA_RESPEITO_MME21)
    lateral_mme21 = (tendencia_mme21 == 'lateral')
    mme21_analise['operavel'] = not lateral_mme21
    mme21_analise['mensagem'] = mensagem_lateral if lateral_mme21 else None

    return {'tiers': tiers, 'mme21': mme21_analise}


def analisar_tracados(df: pd.DataFrame, tracados: pd.DataFrame) -> list:
    """
    Leitura quantitativa (toques/força/probabilidade) de cada traçado
    manual do usuário — mesmo motor de `analisar_mme21`
    (`features.analisar_linha_generica`), mas com a doutrina do S/R
    ESTÁTICO, não a da MME21: `TOQUES_MINIMOS_TRACADO_MANUAL_FORTE` (=3,
    igual ao S/R automático) em vez do limiar mais permissivo da MME21
    (=1), e `janela=len(df)` (histórico INTEIRO disponível) em vez de uma
    janela curta — um nível marcado à mão pelo Jesué é candidato a nível
    ESTRUTURAL, mesma doutrina do motor automático (`calcular_zonas_sr`
    também analisa o histórico inteiro, não uma janela fixa).

    Só `tipo='horizontal'` é suportado por enquanto — outros tipos (ver
    ROADMAP em tracados.py) são ignorados aqui, não fazem a UI quebrar, só
    não aparecem na seção Estudos até serem implementados.

    Retorna uma lista de dicts — cada um é o resultado de
    `analisar_linha_generica` mesclado com `id_tracado`/`cor`, pra a UI
    conseguir renderizar o card e linkar o botão de apagar de volta ao
    traçado certo.
    """
    if df is None or df.empty or tracados is None or tracados.empty:
        return []

    resultados = []
    for _, tracado in tracados.iterrows():
        if tracado['tipo'] != 'horizontal':
            continue  # 'trendline'/'canal'/'figura' — ver ROADMAP em tracados.py
        preco = float(tracado['preco'])
        linha = np.full(len(df), preco)

        analise = features.analisar_linha_generica(
            df, linha, janela=len(df),
            toques_min_forte=cfg.TOQUES_MINIMOS_TRACADO_MANUAL_FORTE,
            tolerancia_atr=cfg.TOLERANCIA_TRACADO_MANUAL_ATR,
        )
        # Cruzamento com o motor automático (bipolaridade real, se o traçado
        # coincidir com uma zona já rompida+retestada) + resumo por regime —
        # ver montar_regime. O badge FORTE do card usa
        # regime['regime_atual']['forte'], não mais o `forte` agregado
        # acima (achado real 2026-07-13: níveis dentro de uma consolidação
        # longa vinham sempre FORTE contando o histórico inteiro).
        cruzamento = localizar_zona_automatica(df, preco)
        analise['cruzamento_automatico'] = cruzamento
        analise['regime'] = montar_regime(analise['detalhes_toques'], analise['eventos_regime'],
                                           cruzamento_automatico=cruzamento)
        analise['id_tracado'] = tracado['id_tracado']
        analise['cor'] = tracado.get('cor')
        resultados.append(analise)
    return resultados
