# =============================================================================
# features.py — Cálculo do vetor de features para cada candle (v2)
# =============================================================================
# v2 (30/06/2026): reescrita dos gargalos de performance identificados ao
# rodar sobre 56.266 candles:
#   - calcular_sr: era O(n²) — clustering do zero a cada candle. Reescrito
#     para calcular clusters UMA vez e fazer lookup vetorizado.
#   - calcular_tendencia: era O(n²) — filtro de DataFrame de pivôs a cada
#     candle. Reescrito com índice de busca binária via searchsorted.
#   - detectar_pivos: loop interno é O(n*k) onde k=n_confirm (pequeno e
#     limitado), aceitável — mantido mas com acesso via arrays em vez de df.loc
# =============================================================================

import pandas as pd
import numpy as np
from datetime import time

import config_backtest as cfg


# =============================================================================
# BLOCO 1 — ANATOMIA DE CANDLE
# =============================================================================

def calcular_anatomia(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    amplitude = df['high'] - df['low']
    corpo     = (df['close'] - df['open']).abs()
    direcao   = np.where(df['close'] >= df['open'], 1, -1)

    sombra_sup = np.where(direcao == 1, df['high'] - df['close'], df['high'] - df['open'])
    sombra_inf = np.where(direcao == 1, df['open'] - df['low'],   df['close'] - df['low'])

    amp = amplitude.replace(0, np.nan)
    df['corpo_pct']      = (corpo / amp).fillna(0)
    df['sombra_sup_pct'] = (sombra_sup / amp).fillna(0)
    df['sombra_inf_pct'] = (sombra_inf / amp).fillna(0)
    df['direcao_candle'] = direcao
    df['amplitude_abs']  = amplitude

    def _tipo(row):
        c, ss, si, d = row['corpo_pct'], row['sombra_sup_pct'], row['sombra_inf_pct'], row['direcao_candle']
        if c >= 0.60: return 'forte_alta' if d == 1 else 'forte_baixa'
        if c <= 0.20:
            if si >= 0.50 and d == 1:  return 'pin_comprador'
            if ss >= 0.50 and d == -1: return 'pin_vendedor'
            return 'doji'
        return 'medio_alta' if d == 1 else 'medio_baixa'

    df['tipo_candle'] = df.apply(_tipo, axis=1)
    return df


# =============================================================================
# BLOCO 2 — ATR E TAMANHO RELATIVO
# =============================================================================

def calcular_atr(df: pd.DataFrame, periodo: int = cfg.ATR_PERIODO) -> pd.DataFrame:
    df = df.copy()
    prev_close = df['close'].shift(1)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - prev_close).abs(),
        (df['low']  - prev_close).abs()
    ], axis=1).max(axis=1)
    df['atr'] = tr.ewm(span=periodo, adjust=False).mean()
    df['amplitude_rel_atr'] = np.where(df['atr'] > 0, df['amplitude_abs'] / df['atr'], 0)
    df['tamanho_candle'] = pd.cut(
        df['amplitude_rel_atr'], bins=[-np.inf, 0.5, 1.5, np.inf],
        labels=['pequeno', 'normal', 'grande']
    )
    return df


# =============================================================================
# BLOCO 3 — PIVÔS POR ESTRUTURA DE MERCADO (metodologia B)
# Reescrito usando arrays numpy em vez de df.loc para cada acesso
# =============================================================================

def detectar_pivos(df: pd.DataFrame, n_confirm: int = 3) -> pd.DataFrame:
    df = df.copy().reset_index(drop=True)
    n = len(df)

    highs  = df['high'].values
    lows   = df['low'].values

    topo_simples  = np.zeros(n, dtype=bool)
    topo_forte    = np.zeros(n, dtype=bool)
    fundo_simples = np.zeros(n, dtype=bool)
    fundo_forte   = np.zeros(n, dtype=bool)
    preco_topo    = np.full(n, np.nan)
    preco_fundo   = np.full(n, np.nan)

    for i in range(1, n - n_confirm):
        # TOPO
        cmax = highs[i]
        clow = lows[i]
        ct = 0
        for j in range(i + 1, min(i + n_confirm + 1, n)):
            if highs[j] < cmax and lows[j] < clow:
                ct += 1
            else:
                break
        if ct >= 1:
            idx = i + ct
            if idx < n:
                topo_simples[idx] = True
                preco_topo[idx]   = cmax
                if ct >= n_confirm:
                    topo_forte[idx] = True

        # FUNDO
        cmin  = lows[i]
        chigh = highs[i]
        cf = 0
        for j in range(i + 1, min(i + n_confirm + 1, n)):
            if lows[j] > cmin and highs[j] > chigh:
                cf += 1
            else:
                break
        if cf >= 1:
            idx = i + cf
            if idx < n:
                fundo_simples[idx] = True
                preco_fundo[idx]   = cmin
                if cf >= n_confirm:
                    fundo_forte[idx] = True

    df['topo_simples']  = topo_simples
    df['topo_forte']    = topo_forte
    df['fundo_simples'] = fundo_simples
    df['fundo_forte']   = fundo_forte
    df['preco_topo']    = preco_topo
    df['preco_fundo']   = preco_fundo
    return df


def detectar_pivo_em_formacao(df: pd.DataFrame, n_confirm: int = 3) -> dict:
    """
    Espelha a lacuna que `detectar_pivos` deixa de propósito: seu loop
    principal roda só até `n - n_confirm` — os últimos `n_confirm` candles
    nunca são avaliados como origem de topo/fundo porque ainda não tiveram
    candles suficientes depois pra confirmar (virar simples/forte) ou
    invalidar (quebrar a sequência). Esta função varre exatamente essa
    cauda e devolve, para topo e fundo separadamente, o candidato mais
    EXTREMO (maior alta / menor baixa) que ainda está "vivo" — nenhum
    candle seguinte quebrou a condição de confirmação (mesmo teste E/E de
    detectar_pivos: alta E baixa maiores/menores). Usado pra responder "o
    preço atual (ex.: mínima do dia) já é um fundo validado, ou ainda está
    esperando confirmação?".

    Retorna {'topo': {...} | None, 'fundo': {...} | None}, cada um:
      {'idx_origem': int, 'preco': float, 'confirmadas': int, 'faltam': int}
    """
    n = len(df)
    if n < 2:
        return {'topo': None, 'fundo': None}

    highs = df['high'].values
    lows = df['low'].values

    inicio = max(1, n - n_confirm)
    melhor_topo = None
    melhor_fundo = None

    for i in range(inicio, n):
        # TOPO — mesmo teste de detectar_pivos, sem gravar em array
        cmax, clow = highs[i], lows[i]
        ct, vivo = 0, True
        for j in range(i + 1, n):
            if highs[j] < cmax and lows[j] < clow:
                ct += 1
            else:
                vivo = False
                break
        if vivo and ct < n_confirm:
            if melhor_topo is None or cmax > melhor_topo['preco']:
                melhor_topo = {'idx_origem': i, 'preco': float(cmax),
                               'confirmadas': ct, 'faltam': n_confirm - ct}

        # FUNDO
        cmin, chigh = lows[i], highs[i]
        cf, vivo = 0, True
        for j in range(i + 1, n):
            if lows[j] > cmin and highs[j] > chigh:
                cf += 1
            else:
                vivo = False
                break
        if vivo and cf < n_confirm:
            if melhor_fundo is None or cmin < melhor_fundo['preco']:
                melhor_fundo = {'idx_origem': i, 'preco': float(cmin),
                                'confirmadas': cf, 'faltam': n_confirm - cf}

    return {'topo': melhor_topo, 'fundo': melhor_fundo}


# =============================================================================
# BLOCO 4 — ESTADO E FASE DA TENDÊNCIA
# Reescrito: pré-computa índices de pivôs e usa searchsorted para lookup O(log n)
# =============================================================================

def calcular_tendencia(df: pd.DataFrame, janela_pivos: int = 6) -> pd.DataFrame:
    df = df.copy()
    n  = len(df)

    # Pré-computa arrays de pivôs
    topos_mask  = df['topo_simples'].values
    fundos_mask = df['fundo_simples'].values
    t_preco = df['preco_topo'].values
    f_preco = df['preco_fundo'].values
    dts     = df['datetime'].values

    # Índices onde há pivô (para busca binária por posição, não por datetime)
    t_idx = np.where(topos_mask)[0]
    f_idx = np.where(fundos_mask)[0]
    t_val = t_preco[t_idx]
    f_val = f_preco[f_idx]

    estados   = np.full(n, 'indefinido', dtype=object)
    fases     = np.full(n, 'indefinido', dtype=object)
    saudes    = np.full(n, 'indefinido', dtype=object)
    impulsos  = np.full(n, np.nan)
    pullbacks = np.full(n, np.nan)

    for i in range(n):
        # Quantos pivôs ocorreram ATÉ o candle i (por posição, não por datetime)
        nt = np.searchsorted(t_idx, i, side='right')
        nf = np.searchsorted(f_idx, i, side='right')

        if nt < 2 or nf < 2:
            continue

        tv = t_val[max(0, nt - janela_pivos):nt]
        fv = f_val[max(0, nf - janela_pivos):nf]

        if len(tv) < 2 or len(fv) < 2:
            continue

        topos_crescendo  = tv[-1] > tv[-2]
        fundos_crescendo = fv[-1] > fv[-2]
        topos_caindo     = tv[-1] < tv[-2]
        fundos_caindo    = fv[-1] < fv[-2]

        if topos_crescendo and fundos_crescendo:
            estado = 'alta'
        elif topos_caindo and fundos_caindo:
            estado = 'baixa'
        else:
            estado = 'lateral'

        estados[i] = estado

        if estado in ('alta', 'baixa'):
            imp = abs(tv[-1] - fv[-1])
            pb  = abs(tv[-1] - fv[-1])
            if len(tv) >= 2 and len(fv) >= 2:
                imp_ant = abs(tv[-2] - fv[-2])
                if imp_ant > 0:
                    prop = imp / imp_ant
                    impulsos[i]  = imp_ant
                    pullbacks[i] = imp

                    saudes[i] = 'fraca' if prop > 0.5 else ('forte' if prop < 0.3 else 'normal')

                    if len(tv) >= 3:
                        imp_rec = abs(tv[-1] - fv[-1])
                        imp_ant2 = abs(tv[-2] - fv[-2])
                        if estado == 'alta':
                            if imp_rec >= imp_ant2: fase = 'inicio' if tv[-1] == tv.max() else 'meio'
                            else: fase = 'exaustao'
                        else:
                            if imp_rec >= imp_ant2: fase = 'exaustao'
                            else: fase = 'inicio' if fv[-1] == fv.min() else 'meio'
                        fases[i] = fase

    df['estado_tendencia'] = estados
    df['fase_tendencia']   = fases
    df['saude_tendencia']  = saudes
    df['impulso_recente']  = impulsos
    df['pullback_recente'] = pullbacks
    return df



# (Substituem as versões com loop acima ao ser importado)

N_CONFIRM_PIVOS = 3  # espelha o default de detectar_pivos — usado p/ recuperar o candle de origem


def _clusterizar_pivos_sr(df: pd.DataFrame, tolerancia_pts: float = None) -> dict | None:
    """
    Clustering de pivôs em ZONAS (Fase 1 — spec_suporte_resistencia_forte.md).

    Etapas:
      1. Coleta topos/fundos como pivôs. `detectar_pivos` registra cada pivô na
         posição de CONFIRMAÇÃO (idx = i + ct), não no candle do extremo em si.
         Para poder medir rejeição (wick ratio) e agressão no candle real do
         toque, recupera-se o índice de ORIGEM via busca retroativa limitada
         (no máx N_CONFIRM_PIVOS candles, comparando o preço exato copiado do
         extremo) — bounded, não é um scan da série inteira.
      2. Agrupa pivôs vizinhos (ordenados por preço) dentro de uma tolerância
         = TOLERANCIA_CLUSTER_SR_ATR × ATR mediano — a mesma zona resultante
         (zona_min/zona_max = extremos do grupo) já é a "largura baseada em
         volatilidade" da spec, sem precisar de uma segunda noção de largura
         imposta a priori.
      3. Preço de referência de cada zona = média ponderada por
         volume-no-toque × (1 + PESO_REJEICAO_NA_REFERENCIA × rejeição), com
         fallback em cascata (só volume → só rejeição → média simples) para
         nunca gerar NaN.
      4. Violação por FECHAMENTO (não pavio), calculada nos dois sentidos
         (alta/baixa) via np.maximum/minimum.accumulate reiniciado em cada
         início de zona + np.searchsorted — o reinício por zona evita que
         uma máxima/mínima histórica anterior à formação da zona contamine
         o resultado (bug sutil de usar o acumulado global sem reinício).

    Retorna None se não há pivôs suficientes, senão um dict com os arrays de
    zona e metadados de toque (reaproveitado por calcular_sr e calcular_zonas_sr
    — nunca recalculado do zero pelas duas funções separadamente).
    """
    n = len(df)
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    atrs = df['atr'].values

    t_mask = df['topo_simples'].values
    f_mask = df['fundo_simples'].values
    t_preco = df['preco_topo'].values
    f_preco = df['preco_fundo'].values

    t_idx = np.where(t_mask & ~np.isnan(t_preco))[0]
    f_idx = np.where(f_mask & ~np.isnan(f_preco))[0]

    # Limita o pool de pivôs a uma janela recente (JANELA_CLUSTERING_CANDLES)
    # em vez do histórico inteiro. Causa raiz diagnosticada em 2026-07-02: um
    # ativo que tendenciou por 2+ anos (WIN: 119.215→203.315 no histórico
    # completo, mas só 168.505→203.315 nos últimos ~60 dias) já tocou quase
    # todo nível de preço alguma hora — sem essa janela, o clustering "telha"
    # a faixa negociada inteira com clusters bem próximos (grade densa),
    # em vez de refletir só os níveis recentes que um trader realmente
    # desenha. Não é a tolerância que precisa mudar (já testado e descartado),
    # é limitar QUAIS toques entram no agrupamento.
    if cfg.JANELA_CLUSTERING_CANDLES and n > cfg.JANELA_CLUSTERING_CANDLES:
        corte = n - cfg.JANELA_CLUSTERING_CANDLES
        t_idx = t_idx[t_idx >= corte]
        f_idx = f_idx[f_idx >= corte]

    if len(t_idx) == 0 and len(f_idx) == 0:
        return None

    all_idx = np.concatenate([t_idx, f_idx])          # índice de CONFIRMAÇÃO (validade/idade, sem lookahead)
    all_preco = np.concatenate([t_preco[t_idx], f_preco[f_idx]])
    all_tipo = np.array((['topo'] * len(t_idx)) + (['fundo'] * len(f_idx)))

    # Índice de ORIGEM (candle real do extremo) via busca retroativa limitada
    origem_idx = np.empty(len(all_idx), dtype=int)
    for pos in range(len(all_idx)):
        idx = all_idx[pos]
        preco_alvo = all_preco[pos]
        serie = highs if all_tipo[pos] == 'topo' else lows
        achou = idx
        for back in range(0, N_CONFIRM_PIVOS + 1):
            j = idx - back
            if j < 0:
                break
            if serie[j] == preco_alvo:
                achou = j
                break
        origem_idx[pos] = achou

    atr_med = np.nanmedian(atrs)
    tol = tolerancia_pts if tolerancia_pts else (
        cfg.TOLERANCIA_CLUSTER_SR_ATR * atr_med if not np.isnan(atr_med) else 50)

    ordem = np.argsort(all_preco)
    precos_s = all_preco[ordem]
    conf_idx_s = all_idx[ordem]
    origem_s = origem_idx[ordem]
    tipo_s = all_tipo[ordem]

    sombra_sup = df['sombra_sup_pct'].values if 'sombra_sup_pct' in df.columns else np.zeros(n)
    sombra_inf = df['sombra_inf_pct'].values if 'sombra_inf_pct' in df.columns else np.zeros(n)
    tem_fluxo_arr = df['fluxo_disponivel'].values if 'fluxo_disponivel' in df.columns else np.zeros(n, dtype=bool)
    vol_c_arr = pd.to_numeric(df['vol_comprador'], errors='coerce').fillna(0).values if 'vol_comprador' in df.columns else np.zeros(n)
    vol_v_arr = pd.to_numeric(df['vol_vendedor'], errors='coerce').fillna(0).values if 'vol_vendedor' in df.columns else np.zeros(n)
    vol_total_arr = vol_c_arr + vol_v_arr

    cluster_centro, cluster_toques = [], []
    cluster_posmax, cluster_primeiro_idx = [], []
    cluster_validade_idx = []
    cluster_zona_min, cluster_zona_max = [], []
    cluster_membros_origem, cluster_membros_tipo, cluster_membros_conf = [], [], []

    i = 0
    while i < len(precos_s):
        grupo_p = [precos_s[i]]
        grupo_conf = [conf_idx_s[i]]
        grupo_origem = [origem_s[i]]
        grupo_tipo = [tipo_s[i]]
        j = i + 1
        while j < len(precos_s) and precos_s[j] - np.mean(grupo_p) <= tol:
            grupo_p.append(precos_s[j])
            grupo_conf.append(conf_idx_s[j])
            grupo_origem.append(origem_s[j])
            grupo_tipo.append(tipo_s[j])
            j += 1

        pesos = []
        for k in range(len(grupo_p)):
            oi = grupo_origem[k]
            rej = sombra_inf[oi] if grupo_tipo[k] == 'fundo' else sombra_sup[oi]
            vol = vol_total_arr[oi] if tem_fluxo_arr[oi] else np.nan
            if not np.isnan(vol) and vol > 0:
                peso = vol * (1 + cfg.PESO_REJEICAO_NA_REFERENCIA * rej)
            elif not np.isnan(rej):
                peso = 1 + cfg.PESO_REJEICAO_NA_REFERENCIA * rej
            else:
                peso = 1.0
            pesos.append(peso)
        pesos = np.array(pesos)
        precos_grupo = np.array(grupo_p)
        preco_ref = float(np.sum(precos_grupo * pesos) / pesos.sum()) if pesos.sum() > 0 else float(np.mean(grupo_p))

        conf_ordenado = sorted(grupo_conf)
        cluster_centro.append(preco_ref)
        cluster_toques.append(len(grupo_p))
        cluster_posmax.append(max(grupo_conf))
        cluster_primeiro_idx.append(min(grupo_conf))
        # Validade para ranking: 2º toque cronológico (1º toque = pivô isolado,
        # ainda não é "nível"; 2º toque confirma repetição — doutrina clássica
        # de S/R). NUNCA usar posmax (último toque de TODA a vida do cluster)
        # como gate de validade — um cluster que continua sendo retocado meses
        # depois teria posmax no FUTURO relativo a candles passados, ficando
        # incorretamente "inválido" bem depois de já ser um nível real e
        # testado (bug de lookahead invertido, encontrado na validação manual
        # do nível 5056 do WDO — via de mão dupla: exclui o nível certo e
        # promove clusters distantes cujos toques já se encerraram antes).
        cluster_validade_idx.append(conf_ordenado[1] if len(conf_ordenado) >= 2 else conf_ordenado[0])
        cluster_zona_min.append(min(grupo_p))
        cluster_zona_max.append(max(grupo_p))
        cluster_membros_origem.append(np.array(grupo_origem))
        cluster_membros_tipo.append(np.array(grupo_tipo))
        cluster_membros_conf.append(np.array(grupo_conf))
        i = j

    centros = np.array(cluster_centro)
    toques = np.array(cluster_toques)
    posmax = np.array(cluster_posmax)
    validade_idx = np.array(cluster_validade_idx)
    primeiro_idx = np.array(cluster_primeiro_idx)
    zona_min = np.array(cluster_zona_min)
    zona_max = np.array(cluster_zona_max)
    n_clusters = len(centros)

    # Violação por fechamento, nos dois sentidos, reiniciada no ÚLTIMO TOQUE
    # confirmado do cluster (posmax), não no primeiro (primeiro_idx).
    #
    # Por quê: um cluster de longa duração acumula toques ao longo de meses;
    # se a busca reiniciasse no primeiro toque, uma oscilação normal de preço
    # ocorrida ENTRE dois toques (ex.: um fechamento acima do centro em maio,
    # seguido de novos toques respeitando o nível em junho) seria erradamente
    # registrada como "violação" — mesmo o nível tendo continuado a segurar o
    # preço depois disso (reclamado pelos toques seguintes). Ancorar no ÚLTIMO
    # toque conhecido significa: só contamos como violação um fechamento além
    # do nível que NÃO foi seguido de nenhum novo toque confirmando o nível de
    # volta — a leitura correta de "ainda está intacto" enquanto o nível segue
    # sendo testado, e só de fato "violado" depois do seu último toque
    # conhecido. Encontrado e corrigido durante validação manual contra um
    # caso real (WDO ~5.055/5.056, resistência que segurou até 02/06/2026 e
    # rompeu em 03/06/2026 — a versão anterior, ancorada no primeiro toque,
    # apontava uma violação de 15/05/2026, uma oscilação intermediária já
    # reclamada por toques posteriores).
    idx_violacao_alta = np.full(n_clusters, -1, dtype=int)
    idx_violacao_baixa = np.full(n_clusters, -1, dtype=int)
    for k in range(n_clusters):
        s = posmax[k]
        c = centros[k]
        if s >= n - 1:
            continue
        sub_max = np.maximum.accumulate(closes[s:])
        pos = np.searchsorted(sub_max, c, side='right')
        if pos < len(sub_max):
            idx_violacao_alta[k] = s + pos
        sub_min_neg = -np.minimum.accumulate(closes[s:])
        pos2 = np.searchsorted(sub_min_neg, -c, side='right')
        if pos2 < len(sub_min_neg):
            idx_violacao_baixa[k] = s + pos2

    return {
        'n': n, 'tol': tol,
        'centros': centros, 'toques': toques, 'posmax': posmax, 'validade_idx': validade_idx,
        'primeiro_idx': primeiro_idx, 'zona_min': zona_min, 'zona_max': zona_max,
        'idx_violacao_alta': idx_violacao_alta, 'idx_violacao_baixa': idx_violacao_baixa,
        'membros_origem': cluster_membros_origem, 'membros_tipo': cluster_membros_tipo,
        'membros_conf': cluster_membros_conf,
    }


def _rankear_niveis_sr(closes, atrs, centros, toques, primeiro_idx, zona_min, zona_max,
                        validade_idx, idx_violacao_alta, idx_violacao_baixa, membros_conf, N):
    """
    Para cada candle, seleciona os N clusters de maior RELEVÂNCIA acima/abaixo
    do preço (dentre os já válidos em validade_idx < i — sem lookahead) e
    monta as colunas escalares por rank (preço/toques/idade/violado/forte/
    dist_atr/zona_min/zona_max). Compartilhado por calcular_sr (ranking
    inicial, só com os clusters "reais") e calcular_zonas_sr (re-ranking
    depois de incluir zonas espelho confirmadas por bipolaridade — ver B.6
    do plano).

    Critério de relevância (2026-07-08, substituiu "3 mais próximos" puro —
    ver nota em config_backtest.py): combina nivel_forte, toques (capado),
    recência do último toque confirmado ANTES do candle atual (sem
    lookahead — via searchsorted no índice de toques ordenado
    cronologicamente, não a ordenação por preço em que membros_conf é
    armazenado), violação e distância em ATR (agora um fator entre outros,
    não mais o único critério). Caso real que expôs o problema: dois
    clusters quase-gêmeos (11 toques cada, mesma idade, ambos parados desde
    a mesma data) a distâncias diferentes do preço — o mais próximo virava
    "#1" mesmo sendo idêntico em força ao mais distante, e um nível bem mais
    forte (22 toques, retestado no candle atual) ficava em #3 só por estar
    mais longe.
    """
    n = len(closes)
    rank_idx_acima = np.full((n, N), -1, dtype=int)
    rank_idx_abaixo = np.full((n, N), -1, dtype=int)
    lados = ('acima', 'abaixo')
    saida = {f'sr_{lado}_{r}{suf}': (np.zeros(n, dtype=bool) if suf in ('_violado', '_forte') else np.full(n, np.nan))
             for lado in lados for r in range(1, N + 1)
             for suf in ('', '_toques', '_idade', '_violado', '_forte', '_dist_atr', '_zona_min', '_zona_max')}

    # Último toque confirmado ATÉ CADA CANDLE (sem lookahead), por cluster —
    # pré-computado fora do loop principal via searchsorted vetorizado (um
    # cluster por vez, mas cada chamada resolve os n candles de uma vez em
    # C, não um loop Python por candle). membros_conf vem ordenado por PREÇO
    # (ordem de construção do clustering), não por tempo — precisa ordenar
    # antes de buscar.
    n_c = len(centros)
    idx_all = np.arange(n)
    ultimo_toque = np.full((n_c, n), -1, dtype=np.int64)
    for k in range(n_c):
        tk = membros_conf[k]
        if tk is None or len(tk) == 0:
            continue
        tk_sorted = np.sort(np.asarray(tk))
        pos = np.searchsorted(tk_sorted, idx_all, side='right')
        valido = pos > 0
        ultimo_toque[k, valido] = tk_sorted[pos[valido] - 1]

    for i in range(n):
        atr = atrs[i]
        if np.isnan(atr) or atr == 0:
            continue
        preco = closes[i]
        validos = validade_idx < i
        if not np.any(validos):
            continue
        idx_validos = np.where(validos)[0]
        cv = centros[idx_validos]

        for lado_acima in (True, False):
            mask = (cv > preco) if lado_acima else (cv <= preco)
            if not mask.any():
                continue
            cand = idx_validos[mask]
            dist_pts = (cv[mask] - preco) if lado_acima else (preco - cv[mask])
            dist_atr_arr = dist_pts / atr

            idade_arr = i - primeiro_idx[cand]
            idx_viol = idx_violacao_alta if lado_acima else idx_violacao_baixa
            viol_arr = (idx_viol[cand] != -1) & (idx_viol[cand] < i)
            forte_arr = (idade_arr >= cfg.IDADE_MINIMA_NIVEL_FORTE) & (toques[cand] >= cfg.TOQUES_MINIMOS_NIVEL_FORTE) & (~viol_arr)

            ultimo_toque_arr = ultimo_toque[cand, i]
            candles_desde_toque = np.where(ultimo_toque_arr >= 0, i - ultimo_toque_arr,
                                            cfg.JANELA_RECENCIA_RELEVANCIA_CANDLES)
            recencia_arr = np.clip(1 - candles_desde_toque / cfg.JANELA_RECENCIA_RELEVANCIA_CANDLES, 0, 1)

            toques_norm = np.minimum(toques[cand], cfg.TETO_TOQUES_RELEVANCIA) / cfg.TETO_TOQUES_RELEVANCIA
            dist_norm = np.minimum(dist_atr_arr / cfg.DIST_ATR_SATURACAO_RELEVANCIA, 1.0)

            relevancia = (cfg.PESO_FORTE_RELEVANCIA * forte_arr
                          + cfg.PESO_TOQUES_RELEVANCIA * toques_norm
                          + cfg.PESO_RECENCIA_RELEVANCIA * recencia_arr
                          - cfg.PENALIDADE_VIOLADO_RELEVANCIA * viol_arr
                          - cfg.PESO_DISTANCIA_RELEVANCIA * dist_norm)

            ordem_rel = np.argsort(-relevancia)[:N]
            lado_nome = 'acima' if lado_acima else 'abaixo'
            for rank, pos in enumerate(ordem_rel):
                k = cand[pos]
                pref = f'sr_{lado_nome}_{rank+1}'
                saida[pref][i] = centros[k]
                saida[f'{pref}_toques'][i] = toques[k]
                saida[f'{pref}_idade'][i] = idade_arr[pos]
                saida[f'{pref}_violado'][i] = bool(viol_arr[pos])
                saida[f'{pref}_forte'][i] = bool(forte_arr[pos])
                saida[f'{pref}_dist_atr'][i] = round(float(dist_atr_arr[pos]), 2)
                saida[f'{pref}_zona_min'][i] = zona_min[k]
                saida[f'{pref}_zona_max'][i] = zona_max[k]
                if lado_acima:
                    rank_idx_acima[i, rank] = k
                else:
                    rank_idx_abaixo[i, rank] = k

    return rank_idx_acima, rank_idx_abaixo, saida


def calcular_sr(df: pd.DataFrame, tolerancia_pts: float = None) -> pd.DataFrame:
    """
    S/R multi-nível (Fase 1 — spec_suporte_resistencia_forte.md): rastreia os
    N_NIVEIS_SR (default 3) níveis mais próximos de cada lado por candle,
    ordenados por proximidade (rank 1 = mais próximo), cada um com toques,
    idade, violado (por fechamento, ver _clusterizar_pivos_sr), nivel_forte
    (idade + toques + integridade) e distância em ATR.

    `nivel_forte` depende do PAPEL (resistência vs. suporte) porque a
    violação depende do papel — o mesmo cluster pode ser "forte" como
    suporte e "não forte" como resistência ao mesmo tempo, se só foi violado
    por um lado (role reversal). A contagem de toques usada aqui soma
    topos+fundos do cluster (simplificação de V1).

    Mantém sr_acima/sr_abaixo/dist_sr_*_atr/qualidade_sr_* como ALIAS do
    rank 1 — retrocompatibilidade com ai_analyst.py.

    Armazena os arrays de cluster em df.attrs['_clusters_sr'] e o mapeamento
    rank→cluster em df.attrs['_rank_idx_acima'/'_rank_idx_abaixo'] para que
    calcular_zonas_sr reaproveite sem recalcular o clustering do zero (esse
    ranking inicial é depois substituído por calcular_zonas_sr, que reroda
    o mesmo ranking incluindo zonas espelho de reversões confirmadas).
    """
    df = df.copy()
    n = len(df)
    N = cfg.N_NIVEIS_SR
    lados = ('acima', 'abaixo')

    for lado in lados:
        for r in range(1, N + 1):
            pref = f'sr_{lado}_{r}'
            df[f'{pref}'] = np.nan
            df[f'{pref}_toques'] = np.nan
            df[f'{pref}_idade'] = np.nan
            df[f'{pref}_violado'] = False
            df[f'{pref}_forte'] = False
            df[f'{pref}_dist_atr'] = np.nan
            df[f'{pref}_zona_min'] = np.nan
            df[f'{pref}_zona_max'] = np.nan
    for col in ['sr_acima', 'sr_abaixo', 'dist_sr_acima_atr', 'dist_sr_abaixo_atr',
                'qualidade_sr_acima', 'qualidade_sr_abaixo']:
        df[col] = np.nan

    clusters = _clusterizar_pivos_sr(df, tolerancia_pts)
    if clusters is None:
        df.attrs['_clusters_sr'] = None
        return df

    closes = df['close'].values
    atrs = df['atr'].values

    rank_idx_acima, rank_idx_abaixo, saida = _rankear_niveis_sr(
        closes, atrs, clusters['centros'], clusters['toques'], clusters['primeiro_idx'],
        clusters['zona_min'], clusters['zona_max'], clusters['validade_idx'],
        clusters['idx_violacao_alta'], clusters['idx_violacao_baixa'], clusters['membros_conf'], N)

    for col, arr in saida.items():
        df[col] = arr

    df['sr_acima'] = df['sr_acima_1']
    df['sr_abaixo'] = df['sr_abaixo_1']
    df['dist_sr_acima_atr'] = df['sr_acima_1_dist_atr']
    df['dist_sr_abaixo_atr'] = df['sr_abaixo_1_dist_atr']
    df['qualidade_sr_acima'] = df['sr_acima_1_toques']
    df['qualidade_sr_abaixo'] = df['sr_abaixo_1_toques']

    df.attrs['_clusters_sr'] = clusters
    df.attrs['_rank_idx_acima'] = rank_idx_acima
    df.attrs['_rank_idx_abaixo'] = rank_idx_abaixo
    return df


def _peso_toque(n_ordem: int, volume_relativo: float | None, forte: bool = True) -> float:
    """
    Peso de força de um toque — sobe até o pico (TOQUES_PICO_FORCA) e CAI
    depois (liquidez consumida, spec 2.3), a menos que o volume desse toque
    seja excepcional (REFORCO_VOLUME_MAX atenua a penalidade, nunca a anula).
    Nunca satura tipo decay^n (que só decresce desde o 1º toque) — precisa
    ter formato de pico e queda, conforme a doutrina.

    `forte` (2026-07-08): um toque só entra no catálogo se já houve alguma
    reversão (detectar_pivos exige isso — nunca é "preço passando reto"),
    mas a CONFIRMAÇÃO dessa reversão varia: `topo_forte`/`fundo_forte` (ct
    >= 3 candles seguidos confirmando) é mais convincente que um toque
    "simples" (ct=1-2). Antes os dois pesavam igual no forca_toques_zona —
    agora um toque fraco (forte=False) tem o peso reduzido por
    PESO_TOQUE_FRACO_MULTIPLICADOR, nunca zerado (é uma reversão real,
    só menos sustentada).
    """
    if n_ordem <= cfg.TOQUES_PICO_FORCA:
        peso = cfg.PESO_TOQUE_INICIAL + cfg.INCREMENTO_TOQUE_ATE_PICO * (n_ordem - 1)
    else:
        peso_pico = cfg.PESO_TOQUE_INICIAL + cfg.INCREMENTO_TOQUE_ATE_PICO * (cfg.TOQUES_PICO_FORCA - 1)
        penalidade = cfg.PESO_TOQUE_PENALIDADE_EXCESSO * (n_ordem - cfg.TOQUES_PICO_FORCA)
        atenuacao = min(cfg.REFORCO_VOLUME_MAX, max(0.0, (volume_relativo or 0.0) - 1.0))
        peso = max(cfg.PESO_TOQUE_MINIMO, peso_pico - penalidade * (1 - atenuacao))
    if not forte:
        peso *= cfg.PESO_TOQUE_FRACO_MULTIPLICADOR
    return peso


class _SemDeepcopy:
    """Contêiner opaco pro `copy.deepcopy`: embrulha uma estrutura grande de
    dicts/listas (detalhe de toque por cluster/zona-espelho) sem impedir a
    leitura normal (`.valor`). `pandas.NDFrame.__finalize__` chama
    `deepcopy(df.attrs)` em quase TODA operação (`.tail()`, `df[col]`,
    filtro booleano, etc.) — pra propagar attrs pro objeto novo. Clonar
    milhares de dicts pequenos a cada tick de 1s (`metricas.extrair_
    metricas`, chamado o tempo todo pelo dashboard) é caro demais pra dado
    que nunca é mutado depois de calculado (medido: ~250ms/chamada extras
    sem isso, contra ~1-2ms com). `__deepcopy__` retornando `self` faz o
    `copy` module parar de recursar exatamente aqui, sem afetar o resto de
    `df.attrs` (que continua sendo copiado normalmente)."""
    __slots__ = ('valor',)

    def __init__(self, valor):
        self.valor = valor

    def __deepcopy__(self, memo):
        return self


def _ts_iso(valor) -> str:
    """Converte um valor de `df['datetime']` (numpy.datetime64/Timestamp)
    pro formato ISO — usado nos detalhes de toque/regime que viram JSON
    pro dashboard."""
    return pd.Timestamp(valor).isoformat()


# =============================================================================
# BLOCOS DE FORÇA (combustível / aproximação / rejeição) — 2026-07-13
# ver especificacao_forca_zonas_price_action.md. Helpers puros (escalar OU
# vetorizado conforme o parâmetro), reaproveitados tanto por calcular_zonas_sr
# (zonas automáticas) quanto por analisar_linha_generica (traçados/MME21) —
# mesmo espírito de _peso_toque acima.
# =============================================================================

def _decaimento_idade(idade_candles):
    """Bloco 1.2 — decaimento gradual de combustível por idade, aplicado a
    QUALQUER zona (diferente de forca_herdada, que só existe pra zona
    espelho). 1.0 = recém-formada (fuel preservado), cai pela metade a cada
    MEIA_VIDA_COMBUSTIVEL_IDADE candles, independente de toques. Aceita
    escalar ou array — quem chama decide."""
    meia_vida = max(cfg.MEIA_VIDA_COMBUSTIVEL_IDADE, 1)
    idade = np.nan_to_num(np.asarray(idade_candles, dtype=float), nan=0.0)
    return np.exp(-np.log(2) * np.clip(idade, 0, None) / meia_vida)


def _forca_origem(idx_origem, highs, lows, atrs) -> dict:
    """Bloco 1.3/1.4 — proxy de intensidade/limpeza no candle de origem do
    nível, na ausência de tape confiável em WIN/WDO: `intensidade_origem`
    (range do candle de origem vs. ATR — impulso forte deixa mais ordens
    represadas) e `base_limpa` (quão estreita foi a formação nos
    JANELA_BASE_LIMPA_CANDLES candles ANTERIORES à origem — base rápida e
    concentrada sugere ordens concentradas; base larga e demorada sugere
    ordens diluídas). Ambos em [0, 1], 0.5 = neutro (dado insuficiente)."""
    n = len(highs)
    if idx_origem is None or idx_origem < 0 or idx_origem >= n:
        return {'intensidade_origem': 0.5, 'base_limpa': 0.5}

    atr_o = atrs[idx_origem]
    if np.isnan(atr_o) or atr_o <= 0:
        intensidade = 0.5
    else:
        range_o = highs[idx_origem] - lows[idx_origem]
        intensidade = float(np.clip(range_o / atr_o, 0, 3) / 3)

    ini_base = max(0, idx_origem - cfg.JANELA_BASE_LIMPA_CANDLES)
    if idx_origem - ini_base < 2:
        base_limpa = 0.5
    else:
        h_base = highs[ini_base:idx_origem]
        l_base = lows[ini_base:idx_origem]
        atr_medio_base = float(np.nanmean(atrs[ini_base:idx_origem]))
        if atr_medio_base > 0:
            largura_base = float(np.max(h_base) - np.min(l_base))
            base_limpa = float(np.clip(1.0 - largura_base / (atr_medio_base * 3), 0, 1))
        else:
            base_limpa = 0.5

    return {'intensidade_origem': round(intensidade, 3), 'base_limpa': round(base_limpa, 3)}


def _profundidade_mitigacao(preco_extremo: float, zona_min: float, zona_max: float, e_fundo: bool) -> float:
    """Bloco 1.6 — fração do range da zona penetrada NESSE toque (0 = tocou
    a borda de fora pra dentro, preservando combustível; >=1 = atravessou a
    zona inteira). Um toque raso consome menos liquidez represada que uma
    penetração profunda."""
    largura = zona_max - zona_min
    if largura <= 0:
        return 0.0
    pen = (zona_max - preco_extremo) / largura if e_fundo else (preco_extremo - zona_min) / largura
    return float(np.clip(pen, 0.0, 1.5))


def _pureza_corpo(idx: int, opens, closes, highs, lows) -> float:
    """Bloco 3 — corpo/range do candle de rejeição (0 = doji/indecisão,
    1 = corpo ocupa o candle inteiro = decisão de fluxo clara)."""
    rng = highs[idx] - lows[idx]
    if rng <= 0:
        return 0.5
    return float(np.clip(abs(closes[idx] - opens[idx]) / rng, 0.0, 1.0))


def _retracoes_na_saida(idx_toque: int, highs, lows, zona_min: float, zona_max: float, n_candles: int) -> int:
    """Bloco 3 — quantas vezes o preço reentrou na zona durante a janela de
    saída (mesma N_CANDLES_IMPULSO já usada pro impulso pós-toque). Cada
    reentrada dilui a força do sinal de rejeição, mesmo que o toque tenha
    tecnicamente "confirmado"."""
    n = len(highs)
    ini = idx_toque + 1
    fim = min(idx_toque + 1 + n_candles, n)
    if fim <= ini:
        return 0
    h, l = highs[ini:fim], lows[ini:fim]
    dentro = (h >= zona_min) & (l <= zona_max)
    return int(np.sum(dentro))


def _analisar_aproximacao(idx_toque: int, highs, lows, opens, closes, atrs,
                           fundo_simples, topo_simples, e_fundo: bool) -> dict:
    """Bloco 2 — qualidade da perna de PREÇO que leva até o toque (candles
    ANTES do toque, não o próprio candle de rejeição — isso é Bloco 3).
    Peça inteiramente nova (não existia versão anterior). Retorna
    `{'score': float, 'fatores': {...}}` — fatores expostos pra manter
    rastreabilidade (seção 8 da spec, "nada de black box")."""
    n = len(closes)
    ini = max(0, idx_toque - cfg.JANELA_APROXIMACAO_CANDLES)
    m = idx_toque - ini
    if m < 4:
        return {'score': 50.0, 'fatores': {}}

    h, l = highs[ini:idx_toque], lows[ini:idx_toque]
    o, c = opens[ini:idx_toque], closes[ini:idx_toque]
    a = atrs[ini:idx_toque]
    atr_medio = float(np.nanmean(a)) if np.any(~np.isnan(a)) else np.nan
    atr_ref = atr_medio if (not np.isnan(atr_medio) and atr_medio > 0) else 1.0
    ranges = h - l
    corpos = np.abs(c - o)
    meio = (h + l) / 2.0

    # 1) esforço x resultado — eficiência = distância líquida / soma dos
    # ranges percorridos; exaustão = perda de eficiência da 1ª pra 2ª metade
    # da perna (esforço crescendo sem resultado equivalente)
    esforco = float(np.sum(ranges))
    resultado = float(abs(c[-1] - c[0]))
    eficiencia = resultado / esforco if esforco > 0 else 0.0
    metade = m // 2
    if metade >= 2:
        esforco_1 = float(np.sum(ranges[:metade])) or 1e-9
        esforco_2 = float(np.sum(ranges[metade:])) or 1e-9
        efic_1 = abs(c[metade - 1] - c[0]) / esforco_1
        efic_2 = abs(c[-1] - c[metade]) / esforco_2
        exaustao = float(np.clip(efic_1 - efic_2, 0.0, 1.0))
    else:
        exaustao = 0.0

    # 2) estrutura impulsiva (poucos candles de corpo grande) vs. corretiva
    limiar_corpo = cfg.LIMIAR_CORPO_DECISIVO_ATR * atr_ref
    frac_decisiva = float(np.mean(corpos >= limiar_corpo))

    # 3) canal (regressão linear sobre o preço médio) vs. errático, e
    # "esticada" = desvio final além do canal normal (excesso especulativo)
    x = np.arange(m, dtype=float)
    if m >= 3 and np.std(meio) > 0:
        coef = np.polyfit(x, meio, 1)
        pred = np.polyval(coef, x)
        ss_res = float(np.sum((meio - pred) ** 2))
        ss_tot = float(np.sum((meio - np.mean(meio)) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        esticada = bool(abs(meio[-1] - pred[-1]) > cfg.LIMIAR_ESTICADA_CANAL_ATR * atr_ref)
    else:
        r2, esticada = 0.0, False
    canal_definido = r2 >= cfg.LIMIAR_CANAL_R2

    # 4) correção em tempo (candles "parados") vs. em preço (retração já
    # sofrida sobre o deslocamento máximo da perna) — reportados separado,
    # nunca combinados (exigência explícita da spec)
    deslocamento_max = float(np.max(np.abs(meio - meio[0])))
    correcao_preco = float(abs(meio[-1] - meio[0]) / deslocamento_max) if deslocamento_max > 0 else 1.0
    parado = np.abs(np.diff(meio)) < (0.15 * atr_ref)
    correcao_tempo = float(np.mean(parado)) if len(parado) else 0.0

    # 5) estrutura de topos/fundos intermediários — reusa os pivôs simples
    # já confirmados (mesmo fractal de detectar_pivos). Numa aproximação de
    # ALTA rumo a resistência, o que importa são os FUNDOS intermediários
    # (precisam subir); numa de BAIXA rumo a suporte, os TOPOS (precisam cair).
    if e_fundo:
        marca, precos_marca = topo_simples[ini:idx_toque], h
    else:
        marca, precos_marca = fundo_simples[ini:idx_toque], l
    idxs_marca = np.where(marca)[0]
    if len(idxs_marca) >= 2:
        serie = precos_marca[idxs_marca]
        diffs = np.diff(serie)
        a_favor = float(np.mean(diffs < 0)) if e_fundo else float(np.mean(diffs > 0))
    else:
        a_favor = 0.5

    # 6) velocidade da última perna — aceleração (clímax) vs. desaceleração
    K = min(cfg.K_VELOCIDADE_ULTIMA_PERNA, m // 2)
    if K >= 2 and m >= 2 * K:
        range_final = float(np.mean(ranges[-K:]))
        range_anterior = float(np.mean(ranges[-2 * K:-K]))
        aceleracao = (range_final / range_anterior) if range_anterior > 0 else 1.0
    else:
        aceleracao = 1.0

    score = 50.0
    score += float(np.clip(eficiencia - 0.5, -0.5, 0.5)) * 20.0
    score += exaustao * cfg.PESO_ESFORCO_RESULTADO
    score += (frac_decisiva - 0.5) * 2 * cfg.PESO_ESTRUTURA_IMPULSIVA
    score += cfg.PESO_CANAL_DEFINIDO if canal_definido else 0.0
    score += cfg.PESO_CANAL_ESTICADO if esticada else 0.0
    score += (a_favor - 0.5) * 2 * cfg.PESO_ESTRUTURA_TOPOS_FUNDOS_APROXIMACAO
    score += cfg.PESO_VELOCIDADE_ULTIMA_PERNA if aceleracao > 1.3 else 0.0
    score = float(np.clip(score, 0.0, 100.0))

    return {
        'score': round(score, 1),
        'fatores': {
            'eficiencia': round(eficiencia, 3),
            'exaustao': round(exaustao, 3),
            'estrutura_impulsiva': round(frac_decisiva, 3),
            'canal_r2': round(r2, 3),
            'esticada': esticada,
            'correcao_tempo': round(correcao_tempo, 3),
            'correcao_preco': round(correcao_preco, 3),
            'estrutura_topos_fundos': round(a_favor, 3),
            'aceleracao_ultima_perna': round(float(aceleracao), 3),
        },
    }


def _compor_combustivel_score(forca_toques, decaimento_idade, profundidade_media,
                               intensidade_origem, base_limpa):
    """Bloco 1 (combustível) composto — vetorizado. `forca_toques` já vem
    ponderada (_peso_toque, pico-e-queda); satura via tanh (nunca corta
    abruptamente como o antigo TOQUES_TETO_SCORE=6, que igualava 6, 11, 14 e
    84 toques). `decaimento_idade` já vem de `_decaimento_idade` (1=fresh,
    ->0=velha)."""
    forca = np.nan_to_num(np.asarray(forca_toques, dtype=float), nan=0.0)
    score = np.full(forca.shape, 50.0)
    score -= np.tanh(forca / cfg.ESCALA_FORCA_TOQUES_COMBUSTIVEL) * cfg.PESO_FORCA_TOQUES_COMBUSTIVEL
    score += (np.asarray(decaimento_idade, dtype=float) - 0.5) * 2 * cfg.PESO_IDADE_COMBUSTIVEL
    score += np.nan_to_num(np.asarray(profundidade_media, dtype=float), nan=0.0) * cfg.PESO_PROFUNDIDADE_MITIGACAO
    score += np.nan_to_num(np.asarray(intensidade_origem, dtype=float), nan=0.5) * cfg.PESO_INTENSIDADE_ORIGEM
    score += np.nan_to_num(np.asarray(base_limpa, dtype=float), nan=0.5) * cfg.PESO_BASE_LIMPA
    return np.clip(score, 0.0, 100.0)


def _compor_rejeicao_score(rejeicao_media, agressao_media, impulso_medio,
                            pureza_corpo_media, retracoes_media):
    """Bloco 3 (rejeição) composto — vetorizado. Refina o que já existia
    (rejeição por wick, agressão via delta, impulso pós-toque) com pureza do
    corpo e nº de retrações durante a saída."""
    rej = np.nan_to_num(np.asarray(rejeicao_media, dtype=float), nan=0.0)
    score = np.full(rej.shape, 50.0)
    score += rej * 20.0
    score += np.nan_to_num(np.asarray(agressao_media, dtype=float), nan=0.0) * 15.0
    score += np.clip(np.nan_to_num(np.asarray(impulso_medio, dtype=float), nan=0.0), -2, 2) * 8.0
    score += (np.nan_to_num(np.asarray(pureza_corpo_media, dtype=float), nan=0.5) - 0.5) * 2 * cfg.PESO_PUREZA_CORPO
    score += np.nan_to_num(np.asarray(retracoes_media, dtype=float), nan=0.0) * cfg.PESO_RETRACOES_SAIDA
    return np.clip(score, 0.0, 100.0)


def montar_acao(combustivel: float, aproximacao: float, rejeicao: float,
                 eh_resistencia: bool, zona_min: float, zona_max: float) -> dict:
    """Camada de saída — 'ação' (seção 6.3 da spec): postura SEMPRE via regra
    explícita combinando os 3 blocos (nunca texto livre), magnitude esperada
    derivada do combustível (zona fresh = mais percurso potencial), e nível
    de invalidação (só preenchido quando a postura favorece entrada) —
    reaproveita a mesma definição de zona usada pelo rompimento estrutural,
    não inventa um conceito novo."""
    media = (combustivel + aproximacao + rejeicao) / 3.0
    piso = min(combustivel, aproximacao, rejeicao)
    if media >= cfg.LIMIAR_POSTURA_FAVORECE and piso >= cfg.LIMIAR_POSTURA_AGUARDAR:
        postura = 'favorece_entrada'
    elif media >= cfg.LIMIAR_POSTURA_AGUARDAR:
        postura = 'aguardar_confirmacao'
    elif media >= cfg.LIMIAR_POSTURA_FRACO:
        postura = 'sinal_fraco'
    else:
        postura = 'evitar'

    if combustivel <= cfg.MAGNITUDE_COMBUSTIVEL_CURTA_MAX:
        magnitude = 'curta'
    elif combustivel <= cfg.MAGNITUDE_COMBUSTIVEL_MODERADA_MAX:
        magnitude = 'moderada'
    else:
        magnitude = 'longa'

    invalidacao = None
    if postura == 'favorece_entrada':
        invalidacao = float(zona_max) if eh_resistencia else float(zona_min)

    return {'postura': postura, 'magnitude_esperada': magnitude, 'invalidacao': invalidacao}


def analisar_linha_generica(df: pd.DataFrame, linha, janela: int,
                             toques_min_forte: int = None) -> dict:
    """
    Leitura quantitativa (toques/força/probabilidade) de uma linha
    qualquer — mesma linguagem do motor de S/R estático
    (calcular_zonas_sr/calcular_prob_niveis_sr: toques ponderados via
    _peso_toque, rejeição por wick, agressão via delta, impulso pós-toque,
    score 50±ajustes clipado), mas pra uma linha que muda de valor POR
    CANDLE (média móvel, trendline desenhada à mão) em vez de um nível
    fixo — por isso não reaproveita _clusterizar_pivos_sr/_rankear_niveis_sr
    diretamente (aquelas comparam contra um centro/zona ESCALAR).

    `linha` precisa ter o mesmo comprimento de `df` — valor da linha em
    cada candle (broadcast de um escalar pro caso de S/R horizontal, série
    de EMA, ou interpolação linear de uma trendline de 2 pontos).

    Só analisa os últimos `janela` candles (regra do Jesué pra MME21: "se
    foi respeitada nos últimos 20 candles"). Reaproveita as colunas já
    computadas pelo pipeline de features em lote (pivôs simples/fortes,
    sombra_sup/inf_pct, atr, delta, vol_comprador/vendedor,
    fluxo_disponivel) — roda direto sobre o DataFrame já carregado em
    memória, sem re-rodar o backtest nem recalcular pivôs.
    """
    n = len(df)
    if n == 0:
        return {'tipo': 'indefinido', 'toques': 0, 'forca_toques': 0.0,
                'forte': False, 'violado': False, 'rejeicao_media': None,
                'agressao_media': None, 'impulso_medio': None,
                'prob_bounce': 50.0, 'janela': 0, 'linha_atual': None,
                'pivo_em_formacao': None, 'eventos_regime': [],
                'regime_atual_desde_idx': None, 'detalhes_toques': [],
                'combustivel_score': 50.0, 'aproximacao_score': 50.0,
                'rejeicao_score': 50.0, 'acao': None}

    toques_min_forte = toques_min_forte or cfg.TOQUES_MINIMOS_NIVEL_FORTE
    janela = min(janela, n)
    ini = n - janela

    linha = np.asarray(linha, dtype=float)
    if len(linha) != n:
        raise ValueError('linha precisa ter o mesmo comprimento de df')

    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    atrs = df['atr'].values
    sombra_sup = df['sombra_sup_pct'].values if 'sombra_sup_pct' in df.columns else np.zeros(n)
    sombra_inf = df['sombra_inf_pct'].values if 'sombra_inf_pct' in df.columns else np.zeros(n)
    topo_simples = df['topo_simples'].values if 'topo_simples' in df.columns else np.zeros(n, dtype=bool)
    fundo_simples = df['fundo_simples'].values if 'fundo_simples' in df.columns else np.zeros(n, dtype=bool)
    topo_forte_arr = df['topo_forte'].values if 'topo_forte' in df.columns else np.zeros(n, dtype=bool)
    fundo_forte_arr = df['fundo_forte'].values if 'fundo_forte' in df.columns else np.zeros(n, dtype=bool)
    tem_fluxo_arr = df['fluxo_disponivel'].values if 'fluxo_disponivel' in df.columns else np.zeros(n, dtype=bool)
    delta_arr = pd.to_numeric(df['delta'], errors='coerce').fillna(0).values if 'delta' in df.columns else np.zeros(n)
    vol_c_arr = pd.to_numeric(df['vol_comprador'], errors='coerce').fillna(0).values if 'vol_comprador' in df.columns else np.zeros(n)
    vol_v_arr = pd.to_numeric(df['vol_vendedor'], errors='coerce').fillna(0).values if 'vol_vendedor' in df.columns else np.zeros(n)
    vol_total_arr = vol_c_arr + vol_v_arr
    vol_media20 = pd.Series(vol_total_arr).rolling(20, min_periods=1).mean().values

    preco_atual = closes[-1]
    linha_atual = linha[-1]
    tipo = 'suporte' if linha_atual <= preco_atual else 'resistencia'
    e_fundo = (tipo == 'suporte')  # suporte é testado por fundos (bounce de baixo pra cima)

    # --- toques: pivôs confirmados (fractal, já computados por detectar_pivos)
    # dentro da janela, próximos da linha NAQUELE candle — não de um valor fixo ---
    idxs_touch = []
    for i in range(ini, n):
        pivo_aqui = fundo_simples[i] if e_fundo else topo_simples[i]
        if not pivo_aqui:
            continue
        atr_i = atrs[i]
        if np.isnan(atr_i) or atr_i <= 0:
            continue
        preco_pivo = lows[i] if e_fundo else highs[i]
        if abs(preco_pivo - linha[i]) <= atr_i * cfg.TOLERANCIA_LINHA_GENERICA_ATR:
            idxs_touch.append(i)

    N_IMP = cfg.N_CANDLES_IMPULSO
    pesos, rejeicoes, agressoes, impulsos = [], [], [], []
    profundidades, purezas, retracoes_lst, aproximacoes = [], [], [], []
    detalhes_toques = []
    for ordem_n, idx in enumerate(idxs_touch, start=1):
        rej = sombra_inf[idx] if e_fundo else sombra_sup[idx]
        vol_i = vol_total_arr[idx] if tem_fluxo_arr[idx] else np.nan
        vol_rel = (vol_i / vol_media20[idx]) if (not np.isnan(vol_i) and vol_media20[idx] > 0) else None
        forte_toque = bool(fundo_forte_arr[idx] if e_fundo else topo_forte_arr[idx])
        peso = _peso_toque(ordem_n, vol_rel, forte_toque)

        if tem_fluxo_arr[idx] and vol_total_arr[idx] > 0:
            delta_norm = delta_arr[idx] / vol_total_arr[idx]
            agr = float(np.clip(delta_norm if e_fundo else -delta_norm, -1, 1))
        else:
            agr = 0.0

        idx_alvo = min(idx + N_IMP, n - 1)
        atr_idx = atrs[idx] if (not np.isnan(atrs[idx]) and atrs[idx] > 0) else np.nan
        imp = (abs(closes[idx_alvo] - closes[idx]) / (atr_idx * N_IMP)) if not np.isnan(atr_idx) else np.nan

        # "Zona" de uma linha genérica é a banda de tolerância em torno do
        # valor da linha NAQUELE candle (mesma banda usada pra detectar o
        # toque acima) — reaproveita os mesmos helpers do S/R automático.
        preco_extremo = lows[idx] if e_fundo else highs[idx]
        tol_idx = atr_idx * cfg.TOLERANCIA_LINHA_GENERICA_ATR if not np.isnan(atr_idx) else 0.0
        z_min_idx, z_max_idx = linha[idx] - tol_idx, linha[idx] + tol_idx
        profundidade = _profundidade_mitigacao(preco_extremo, z_min_idx, z_max_idx, e_fundo) if tol_idx > 0 else 0.0
        pureza = _pureza_corpo(idx, opens, closes, highs, lows)
        retr = _retracoes_na_saida(idx, highs, lows, z_min_idx, z_max_idx, N_IMP) if tol_idx > 0 else 0
        aprox = _analisar_aproximacao(idx, highs, lows, opens, closes, atrs, fundo_simples, topo_simples, e_fundo)

        pesos.append(peso)
        rejeicoes.append(rej)
        agressoes.append(agr)
        impulsos.append(imp)
        profundidades.append(profundidade)
        purezas.append(pureza)
        retracoes_lst.append(retr)
        aproximacoes.append(aprox['score'])
        detalhes_toques.append({
            'idx': int(idx),
            'timestamp': _ts_iso(df['datetime'].values[idx]),
            'preco': float(preco_extremo),
            'tipo_pivo': 'forte' if forte_toque else 'simples',
            'rejeicao': round(float(rej), 3),
            'agressao': round(float(agr), 3),
            'impulso': None if np.isnan(imp) else round(float(imp), 3),
            'peso': round(float(peso), 3),
            'profundidade': round(profundidade, 3), 'pureza_corpo': round(pureza, 3),
            'retracoes_saida': retr, 'aproximacao': aprox,
        })

    pesos_arr = np.array(pesos)
    toques = len(idxs_touch)
    forca_toques = float(pesos_arr.sum()) if toques else 0.0
    rejeicao_media = float(np.average(rejeicoes, weights=pesos_arr)) if toques and pesos_arr.sum() > 0 else np.nan
    agressao_media = float(np.average(agressoes, weights=pesos_arr)) if toques and pesos_arr.sum() > 0 else np.nan
    if toques and pesos_arr.sum() > 0:
        profundidade_media = float(np.average(profundidades, weights=pesos_arr))
        pureza_corpo_media = float(np.average(purezas, weights=pesos_arr))
        retracoes_media = float(np.average(retracoes_lst, weights=pesos_arr))
        aproximacao_media = float(np.average(aproximacoes, weights=pesos_arr))
    else:
        profundidade_media = np.nan
        pureza_corpo_media = np.nan
        retracoes_media = np.nan
        aproximacao_media = np.nan
    if toques:
        impulsos_arr = np.array(impulsos)
        validos_imp = ~np.isnan(impulsos_arr)
        impulso_medio = float(np.average(impulsos_arr[validos_imp], weights=pesos_arr[validos_imp])) if validos_imp.any() else np.nan
    else:
        impulso_medio = np.nan

    # --- violado: fechamento cruzou a linha (contra o papel dela) com corpo
    # mínimo, e ainda não retomou — estado avaliado cronologicamente pra
    # refletir a situação ATUAL (fim da janela), não qualquer cruzamento
    # isolado no meio do caminho (mesmo espírito do wick-sweep do S/R
    # estático, sem a máquina de estados completa de pullback/throwback). ---
    violado = False
    eventos_regime = []
    for i in range(ini, n):
        atr_i = atrs[i]
        if np.isnan(atr_i) or atr_i <= 0:
            continue
        corpo_i = abs(closes[i] - opens[i])
        rompeu = (closes[i] < linha[i]) if e_fundo else (closes[i] > linha[i])
        if rompeu and corpo_i >= cfg.CORPO_MINIMO_ROMPIMENTO_ATR * atr_i:
            if not violado:
                eventos_regime.append({'idx': int(i), 'timestamp': _ts_iso(df['datetime'].values[i]),
                                        'tipo': 'rompimento', 'preco_fechamento': float(closes[i])})
            violado = True
        elif violado:
            recuperou = (closes[i] >= linha[i]) if e_fundo else (closes[i] <= linha[i])
            if recuperou:
                eventos_regime.append({'idx': int(i), 'timestamp': _ts_iso(df['datetime'].values[i]),
                                        'tipo': 'recuperacao', 'preco_fechamento': float(closes[i])})
                violado = False

    regime_atual_desde_idx = eventos_regime[-1]['idx'] if eventos_regime else None
    for d in detalhes_toques:
        if regime_atual_desde_idx is None:
            d['regime'] = 'unico'
        else:
            d['regime'] = 'atual' if d['idx'] >= regime_atual_desde_idx else 'antigo'

    # --- pivô em formação: o preço mais recente do lado certo (fundo pra
    # suporte, topo pra resistência) ainda pode não ter confirmação
    # suficiente pra contar como toque de verdade — ver detectar_pivo_em_formacao ---
    pivo_em_formacao = None
    candidato_formacao = detectar_pivo_em_formacao(df).get('fundo' if e_fundo else 'topo')
    if candidato_formacao is not None:
        idx_o = candidato_formacao['idx_origem']
        atr_o = atrs[idx_o]
        if not np.isnan(atr_o) and atr_o > 0 and \
                abs(candidato_formacao['preco'] - linha[idx_o]) <= atr_o * cfg.TOLERANCIA_LINHA_GENERICA_ATR:
            pivo_em_formacao = {
                'tipo_pivo': 'fundo' if e_fundo else 'topo',
                'idx_origem': int(idx_o),
                'timestamp_origem': _ts_iso(df['datetime'].values[idx_o]),
                'preco': candidato_formacao['preco'],
                'confirmadas': candidato_formacao['confirmadas'],
                'faltam': candidato_formacao['faltam'],
            }

    forte = (toques >= toques_min_forte) and not violado

    # --- Blocos de força (2026-07-13, mesma decomposição do S/R automático
    # — ver calcular_prob_niveis_sr). Uma linha genérica não tem um "candle
    # de origem" natural como um pivô automático (o traçado é desenhado à
    # mão) — usa o 1º toque registrado como proxy de origem, e "idade" como
    # candles desde esse 1º toque (0 = ainda sem toque, tratado como fresh). ---
    idx_origem_proxy = idxs_touch[0] if toques else None
    forca_origem_linha = _forca_origem(idx_origem_proxy, highs, lows, atrs) if idx_origem_proxy is not None \
        else {'intensidade_origem': 0.5, 'base_limpa': 0.5}
    idade_linha = (n - 1 - idx_origem_proxy) if idx_origem_proxy is not None else 0
    decaimento_idade_linha = float(_decaimento_idade(idade_linha))

    combustivel = float(_compor_combustivel_score(
        np.array([forca_toques]), np.array([decaimento_idade_linha]), np.array([profundidade_media]),
        np.array([forca_origem_linha['intensidade_origem']]), np.array([forca_origem_linha['base_limpa']]))[0])
    aproximacao = float(aproximacao_media) if not np.isnan(aproximacao_media) else 50.0
    rejeicao = float(_compor_rejeicao_score(
        np.array([rejeicao_media]), np.array([agressao_media]), np.array([impulso_medio]),
        np.array([pureza_corpo_media]), np.array([retracoes_media]))[0])

    score = 50.0
    score += (combustivel - 50) / 50 * cfg.AMPLITUDE_BLOCO_COMBUSTIVEL
    score += (aproximacao - 50) / 50 * cfg.AMPLITUDE_BLOCO_APROXIMACAO
    score += (rejeicao - 50) / 50 * cfg.AMPLITUDE_BLOCO_REJEICAO
    score += 10.0 if (toques and forte) else (-5.0 if toques else 0.0)
    if violado:
        score -= 15.0
    prob_bounce = float(np.clip(score, cfg.SCORE_PROB_MIN, cfg.SCORE_PROB_MAX))

    tol_atual = atrs[-1] * cfg.TOLERANCIA_LINHA_GENERICA_ATR if not np.isnan(atrs[-1]) else 0.0
    eh_resistencia_linha = not e_fundo
    acao = montar_acao(combustivel, aproximacao, rejeicao, eh_resistencia_linha,
                        linha_atual - tol_atual, linha_atual + tol_atual)

    return {
        'tipo': tipo,
        'toques': toques,
        'forca_toques': round(forca_toques, 2),
        'forte': bool(forte),
        'violado': bool(violado),
        'rejeicao_media': None if np.isnan(rejeicao_media) else round(rejeicao_media, 3),
        'agressao_media': None if np.isnan(agressao_media) else round(agressao_media, 3),
        'impulso_medio': None if np.isnan(impulso_medio) else round(impulso_medio, 3),
        'prob_bounce': round(prob_bounce, 1),
        'janela': janela,
        'linha_atual': float(linha_atual),
        'pivo_em_formacao': pivo_em_formacao,
        'eventos_regime': eventos_regime,
        'regime_atual_desde_idx': regime_atual_desde_idx,
        'detalhes_toques': detalhes_toques,
        'combustivel_score': round(combustivel, 1),
        'aproximacao_score': round(aproximacao, 1),
        'rejeicao_score': round(rejeicao, 1),
        'acao': acao,
    }


def calcular_zonas_sr(df: pd.DataFrame) -> pd.DataFrame:
    """
    Catálogo de zonas (Fase 1, núcleo operacional — spec 2.2 a 2.10 de
    spec_suporte_resistencia_forte.md): para cada zona de calcular_sr,
    calcula rejeição por toque (wick ratio), força de toques com decaimento
    (pico e queda), impulso pós-toque, agressão no momento do toque, e roda
    a máquina de estados de bipolaridade (rompimento válido por fechamento
    → checagem de wick-sweep → aguardando pullback/throwback → reteste
    confirmado → zona espelho com papel invertido).

    O catálogo é reconstruído em memória a cada rodada de
    calcular_todos_features — não é persistido em disco (determinístico a
    partir do histórico de candles, como pivôs/clusters já são hoje).

    A zona espelho criada após reversão confirmada é incluída no re-ranking
    (fica visível como candidata a nível) e acumula seus PRÓPRIOS toques a
    partir da criação (pivôs confirmados dentro da mesma faixa de preço,
    do tipo compatível com o papel invertido — ver bloco "zonas espelho
    acumulam toques próprios" abaixo, corrigido em 2026-07-04; até então
    era uma limitação v1 documentada: toques=0 pra sempre). Ela também
    carrega uma `forca_herdada` da zona-mãe, decaída no tempo (ver
    calcular_prob_niveis_sr) — os toques próprios se somam a essa herança,
    não a substituem.

    Reaproveita os arrays de cluster já calculados por calcular_sr (via
    df.attrs['_clusters_sr']) — nunca recalcula o clustering do zero.
    """
    df = df.copy()
    n = len(df)
    N = cfg.N_NIVEIS_SR
    lados = ('acima', 'abaixo')

    for lado in lados:
        for r in range(1, N + 1):
            pref = f'sr_{lado}_{r}'
            df[f'{pref}_status'] = 'ativa'
            df[f'{pref}_rejeicao_media'] = np.nan
            df[f'{pref}_agressao_media'] = np.nan
            df[f'{pref}_impulso_medio'] = np.nan
            df[f'{pref}_forca_toques'] = np.nan
            df[f'{pref}_forca_herdada'] = np.nan
            df[f'{pref}_zona_espelho_id'] = np.nan
            df[f'{pref}_rompimento_hoje'] = 'nenhum'

    clusters = df.attrs.get('_clusters_sr')
    if clusters is None:
        clusters = _clusterizar_pivos_sr(df)
    if clusters is None:
        return df

    centros_orig = clusters['centros']
    toques_orig = clusters['toques']
    posmax_orig = clusters['posmax']
    validade_idx_orig = clusters['validade_idx']
    primeiro_idx_orig = clusters['primeiro_idx']
    zona_min_orig = clusters['zona_min']
    zona_max_orig = clusters['zona_max']
    idx_violacao_alta_orig = clusters['idx_violacao_alta']
    idx_violacao_baixa_orig = clusters['idx_violacao_baixa']
    membros_origem = clusters['membros_origem']
    membros_tipo = clusters['membros_tipo']
    membros_conf = clusters['membros_conf']
    n_clusters = len(centros_orig)

    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    atrs = df['atr'].values
    corpo = (df['close'] - df['open']).abs().values
    sombra_sup = df['sombra_sup_pct'].values if 'sombra_sup_pct' in df.columns else np.zeros(n)
    sombra_inf = df['sombra_inf_pct'].values if 'sombra_inf_pct' in df.columns else np.zeros(n)
    topo_forte_arr = df['topo_forte'].values if 'topo_forte' in df.columns else np.ones(n, dtype=bool)
    fundo_forte_arr = df['fundo_forte'].values if 'fundo_forte' in df.columns else np.ones(n, dtype=bool)
    topo_simples_arr = df['topo_simples'].values if 'topo_simples' in df.columns else np.zeros(n, dtype=bool)
    fundo_simples_arr = df['fundo_simples'].values if 'fundo_simples' in df.columns else np.zeros(n, dtype=bool)
    tem_fluxo_arr = df['fluxo_disponivel'].values if 'fluxo_disponivel' in df.columns else np.zeros(n, dtype=bool)
    delta_arr = pd.to_numeric(df['delta'], errors='coerce').fillna(0).values if 'delta' in df.columns else np.zeros(n)
    vol_c_arr = pd.to_numeric(df['vol_comprador'], errors='coerce').fillna(0).values if 'vol_comprador' in df.columns else np.zeros(n)
    vol_v_arr = pd.to_numeric(df['vol_vendedor'], errors='coerce').fillna(0).values if 'vol_vendedor' in df.columns else np.zeros(n)
    vol_total_arr = vol_c_arr + vol_v_arr
    vol_media20 = pd.Series(vol_total_arr).rolling(20, min_periods=1).mean().values

    # --- B.2/B.3/B.4/B.5: agregados por cluster (uma vez por cluster, não por candle) ---
    forca_toques_zona = np.zeros(n_clusters)
    rejeicao_media_zona = np.full(n_clusters, np.nan)
    agressao_media_zona = np.full(n_clusters, np.nan)
    impulso_medio_zona = np.full(n_clusters, np.nan)
    profundidade_media_zona = np.full(n_clusters, np.nan)
    pureza_corpo_media_zona = np.full(n_clusters, np.nan)
    retracoes_media_zona = np.full(n_clusters, np.nan)
    aproximacao_media_zona = np.full(n_clusters, np.nan)
    intensidade_origem_zona = np.full(n_clusters, np.nan)
    base_limpa_zona = np.full(n_clusters, np.nan)
    detalhe_toques_zona = [[] for _ in range(n_clusters)]
    N_IMP = cfg.N_CANDLES_IMPULSO

    for k in range(n_clusters):
        conf = membros_conf[k]
        origem = membros_origem[k]
        tipo = membros_tipo[k]
        ordem_tempo = np.argsort(conf)
        pesos, rejeicoes, agressoes, impulsos = [], [], [], []
        profundidades, purezas, retracoes_lst, aproximacoes = [], [], [], []
        forca_origem_zona = _forca_origem(int(primeiro_idx_orig[k]), highs, lows, atrs)
        intensidade_origem_zona[k] = forca_origem_zona['intensidade_origem']
        base_limpa_zona[k] = forca_origem_zona['base_limpa']
        z_min, z_max = zona_min_orig[k], zona_max_orig[k]
        for ordem_n, pos in enumerate(ordem_tempo, start=1):
            oi = origem[pos]
            e_fundo = tipo[pos] == 'fundo'
            ci = conf[pos]
            rej = sombra_inf[oi] if e_fundo else sombra_sup[oi]
            vol_oi = vol_total_arr[oi] if tem_fluxo_arr[oi] else np.nan
            vol_rel = (vol_oi / vol_media20[oi]) if (not np.isnan(vol_oi) and vol_media20[oi] > 0) else None
            forte_toque = bool(fundo_forte_arr[ci] if e_fundo else topo_forte_arr[ci])
            peso = _peso_toque(ordem_n, vol_rel, forte_toque)

            if tem_fluxo_arr[oi] and vol_total_arr[oi] > 0:
                delta_norm = delta_arr[oi] / vol_total_arr[oi]
                agr = float(np.clip(delta_norm if e_fundo else -delta_norm, -1, 1))
            else:
                agr = 0.0

            idx_alvo = min(oi + N_IMP, n - 1)
            atr_oi = atrs[oi] if (not np.isnan(atrs[oi]) and atrs[oi] > 0) else np.nan
            imp = (abs(closes[idx_alvo] - closes[oi]) / (atr_oi * N_IMP)) if not np.isnan(atr_oi) else np.nan

            preco_extremo = lows[oi] if e_fundo else highs[oi]
            profundidade = _profundidade_mitigacao(preco_extremo, z_min, z_max, e_fundo)
            pureza = _pureza_corpo(oi, opens, closes, highs, lows)
            retr = _retracoes_na_saida(oi, highs, lows, z_min, z_max, N_IMP)
            aprox = _analisar_aproximacao(oi, highs, lows, opens, closes, atrs,
                                           fundo_simples_arr, topo_simples_arr, e_fundo)

            pesos.append(peso)
            rejeicoes.append(rej)
            agressoes.append(agr)
            impulsos.append(imp)
            profundidades.append(profundidade)
            purezas.append(pureza)
            retracoes_lst.append(retr)
            aproximacoes.append(aprox['score'])
            detalhe_toques_zona[k].append({
                'idx': int(ci), 'idx_origem': int(oi),
                'timestamp': _ts_iso(df['datetime'].values[ci]),
                'preco': float(preco_extremo),
                'tipo_pivo': 'forte' if forte_toque else 'simples',
                'rejeicao': round(float(rej), 3), 'agressao': round(float(agr), 3),
                'impulso': None if np.isnan(imp) else round(float(imp), 3),
                'peso': round(float(peso), 3),
                'profundidade': round(profundidade, 3), 'pureza_corpo': round(pureza, 3),
                'retracoes_saida': retr, 'aproximacao': aprox,
            })

        pesos = np.array(pesos)
        forca_toques_zona[k] = pesos.sum()
        if pesos.sum() > 0:
            rejeicao_media_zona[k] = float(np.average(rejeicoes, weights=pesos))
            agressao_media_zona[k] = float(np.average(agressoes, weights=pesos))
            profundidade_media_zona[k] = float(np.average(profundidades, weights=pesos))
            pureza_corpo_media_zona[k] = float(np.average(purezas, weights=pesos))
            retracoes_media_zona[k] = float(np.average(retracoes_lst, weights=pesos))
            aproximacao_media_zona[k] = float(np.average(aproximacoes, weights=pesos))
            impulsos_arr = np.array(impulsos)
            validos_imp = ~np.isnan(impulsos_arr)
            if validos_imp.any():
                impulso_medio_zona[k] = float(np.average(impulsos_arr[validos_imp], weights=pesos[validos_imp]))

    clusters['detalhe_toques_zona'] = _SemDeepcopy(detalhe_toques_zona)

    # --- B.6: máquina de estados de bipolaridade (uma vez por cluster,
    # janela limitada — O(n_zonas × JANELA_MAX_RETESTE), não O(n²)) ---
    status_timeline = [[(int(primeiro_idx_orig[k]), 'ativa')] for k in range(n_clusters)]
    zona_espelho_id = np.full(n_clusters, -1, dtype=int)
    tipo_atual = []
    for k in range(n_clusters):
        n_topo = int(np.sum(membros_tipo[k] == 'topo'))
        n_fundo = len(membros_tipo[k]) - n_topo
        tipo_atual.append('resistencia' if n_topo >= n_fundo else 'suporte')

    def _corpo_ok(idx):
        return not np.isnan(atrs[idx]) and atrs[idx] > 0 and corpo[idx] > cfg.CORPO_MINIMO_ROMPIMENTO_ATR * atrs[idx]

    # --- B.6b: sinal de rompimento INTRADIÁRIO (escopo diferente do FSM
    # estrutural acima) — responde "esse nível já rompeu HOJE, dentro do
    # próprio pregão?", não "esse nível já foi abandonado pra sempre?".
    # Caso real que motivou isso (2026-07-02, WIN 173.570, 17/06/2026): o
    # preço rompeu a zona de manhã, testou, seguiu — só que à tarde do
    # MESMO DIA fechou de volta abaixo da zona inteira, então o FSM
    # estrutural (que olha o histórico completo) nunca considera aquilo
    # "o" rompimento. Do ponto de vista de quem operou de manhã, foi um
    # rompimento+pullback real e válido. As duas perguntas são legítimas e
    # coexistem — o FSM estrutural continua servindo pra contexto ("essa
    # região ainda importa?"), este sinal serve de gatilho ao vivo ("rompeu
    # agora, dentro do pregão de hoje?"). Reseta a cada novo dia de pregão.
    dias_arr = df['datetime'].dt.date.values
    status_timeline_intraday = [[(int(primeiro_idx_orig[k]), 'nenhum')] for k in range(n_clusters)]
    for k in range(n_clusters):
        c_min, c_max = zona_min_orig[k], zona_max_orig[k]
        start = int(primeiro_idx_orig[k])
        dia_atual = None
        rompeu_hoje = False
        pullback_hoje = False
        tipo_rompimento = None
        for j in range(start, n):
            d = dias_arr[j]
            if d != dia_atual:
                # reset SEMPRE que o dia muda, mesmo que o dia anterior não
                # tenha tido nenhum evento — senão a timeline (usada por
                # searchsorted) herda o último status de um dia muito
                # anterior em vez de voltar pra 'nenhum' no dia novo.
                dia_atual = d
                status_timeline_intraday[k].append((j, 'nenhum'))
                rompeu_hoje = False
                pullback_hoje = False
                tipo_rompimento = None
            if not rompeu_hoje:
                if closes[j] > c_max and _corpo_ok(j):
                    rompeu_hoje, pullback_hoje, tipo_rompimento = True, False, 'alta'
                    status_timeline_intraday[k].append((j, 'rompeu_hoje'))
                elif closes[j] < c_min and _corpo_ok(j):
                    rompeu_hoje, pullback_hoje, tipo_rompimento = True, False, 'baixa'
                    status_timeline_intraday[k].append((j, 'rompeu_hoje'))
            elif not pullback_hoje:
                # ainda não confirmou reteste — se o preço já fecha de volta
                # pra dentro/além da zona (rompimento falso, tipo o caso real
                # WDO 5.231,50 de 02/07: candle 18 rompe, candle 20 fecha de
                # volta abaixo com a mesma força), desarma e permite um novo
                # ciclo rompeu_hoje mais tarde no MESMO dia — sem isso, o
                # sinal ficava travado no primeiro rompimento (mesmo que
                # falso/ruído de notícia) pelo resto do pregão.
                invalidou = (closes[j] <= c_max) if tipo_rompimento == 'alta' else (closes[j] >= c_min)
                if invalidou:
                    rompeu_hoje, tipo_rompimento = False, None
                    status_timeline_intraday[k].append((j, 'nenhum'))
                else:
                    tocou = lows[j] <= c_max and highs[j] >= c_min
                    if tocou:
                        rej = sombra_inf[j] if tipo_rompimento == 'alta' else sombra_sup[j]
                        if rej > cfg.LIMIAR_REJEICAO_RETESTE:
                            pullback_hoje = True
                            status_timeline_intraday[k].append((j, 'pullback_confirmado_hoje'))
            else:
                # reteste já confirmado — se o preço reverter de vez (fechar
                # do lado oposto ao papel atual da zona, abandonando-a por
                # completo), rearma pra permitir detectar um rompimento novo
                # e genuinamente diferente mais tarde no mesmo dia.
                reverteu = (closes[j] < c_min) if tipo_rompimento == 'alta' else (closes[j] > c_max)
                if reverteu:
                    rompeu_hoje, pullback_hoje, tipo_rompimento = False, False, None
                    status_timeline_intraday[k].append((j, 'nenhum'))

    timeline_idx_intra = [np.array([t[0] for t in tl]) for tl in status_timeline_intraday]
    timeline_status_intra = [np.array([t[1] for t in tl]) for tl in status_timeline_intraday]

    def _status_intraday_no_candle(k, i):
        if k >= n_clusters:
            return 'nenhum'  # zonas espelho ainda não têm sinal intraday próprio (v1)
        idxs = timeline_idx_intra[k]
        pos = np.searchsorted(idxs, i, side='right') - 1
        return timeline_status_intra[k][pos] if pos >= 0 else 'nenhum'

    novas_zonas = []
    for k in range(n_clusters):
        # Busca de rompimento ancorada no ÚLTIMO toque conhecido (posmax), não
        # no primeiro — mesma correção aplicada à violação simples em
        # _clusterizar_pivos_sr (ver comentário lá): enquanto o cluster ainda
        # está sendo retocado/respeitado, qualquer excursão além da zona é
        # considerada "reclamada" pelos toques seguintes, não um rompimento
        # real. Só procuramos rompimento genuíno depois do último toque
        # confirmado, quando não há mais nenhum toque posterior para
        # "reclamar" a zona de volta.
        s = int(posmax_orig[k])
        c_min, c_max = zona_min_orig[k], zona_max_orig[k]
        tipo_k = tipo_atual[k]
        # Agressão (delta/volume) não é mais gate obrigatório aqui — em M5 o
        # delta de um único candle é ruidoso e pode ficar fraco mesmo em
        # rompimentos reais e sustentados (caso real WIN 17/06/2026, corpo
        # grande e fechamento decisivo além da zona, delta < limiar). Corpo
        # mínimo + fechamento além da zona já confirmam o rompimento; a
        # agressão da zona entra como reforço no score de C
        # (calcular_prob_niveis_sr), não como bloqueio da detecção.
        rompimento_idx = None
        for j in range(s + 1, n):
            if tipo_k == 'resistencia' and closes[j] > c_max and _corpo_ok(j):
                rompimento_idx = j
                break
            if tipo_k == 'suporte' and closes[j] < c_min and _corpo_ok(j):
                rompimento_idx = j
                break
        if rompimento_idx is None:
            continue

        j = rompimento_idx
        fim_sweep = min(j + cfg.JANELA_INVALIDACAO_WICK_SWEEP, n - 1)
        voltou = any(c_min <= closes[jj] <= c_max for jj in range(j + 1, fim_sweep + 1))
        if voltou:
            continue  # violina — zona segue ativa/intacta

        status_timeline[k].append((j, 'aguardando_pullback' if tipo_k == 'suporte' else 'aguardando_throwback'))

        janela_fim = min(j + cfg.JANELA_MAX_RETESTE, n - 1)
        reteste_idx = None
        for jj in range(j + 1, janela_fim + 1):
            if not (lows[jj] <= c_max and highs[jj] >= c_min):
                continue
            rej_jj = sombra_inf[jj] if tipo_k == 'resistencia' else sombra_sup[jj]
            if rej_jj <= cfg.LIMIAR_REJEICAO_RETESTE:
                continue
            fim_cont = min(jj + cfg.JANELA_CONTINUIDADE_POS_RETESTE, n - 1)
            continuou = closes[fim_cont] < c_min if tipo_k == 'suporte' else closes[fim_cont] > c_max
            if continuou:
                reteste_idx = jj
                break

        if reteste_idx is not None:
            status_timeline[k].append((reteste_idx, 'rompida'))
            novas_zonas.append({
                'origem_k': k, 'idx_criacao': reteste_idx, 'idx_rompimento': j,
                'preco_referencia': centros_orig[k], 'zona_min': c_min, 'zona_max': c_max,
                'forca_herdada': forca_toques_zona[k],
            })
        else:
            status_timeline[k].append((janela_fim, 'rompida'))

    # --- B.7: zonas espelho acumulam toques próprios (2026-07-04) ---
    # Antes, uma zona espelho nascia com toques=0 pra sempre — só a
    # forca_herdada (decaída) da zona-mãe entrava no score, e novos toques
    # reais que ela recebesse depois de criada eram ignorados (limitação v1
    # documentada). Agora procuramos, para cada zona espelho, seus próprios
    # pivôs confirmados (topo/fundo) que caem dentro da mesma faixa de
    # preço herdada da zona-mãe e ocorrem DEPOIS de idx_criacao, do tipo
    # compatível com o papel invertido (resistência rompida vira suporte →
    # procura fundos; suporte rompido vira resistência → procura topos).
    # Mesmo peso/decaimento (_peso_toque) e mesmas métricas (rejeição/
    # agressão/impulso) do bloco B.2-B.5 acima, reaplicado só a esse
    # subconjunto — não reabre o clustering, só estende o mesmo cálculo.
    t_mask_full = df['topo_simples'].values
    f_mask_full = df['fundo_simples'].values
    t_preco_full = df['preco_topo'].values
    f_preco_full = df['preco_fundo'].values
    t_idx_full = np.where(t_mask_full & ~np.isnan(t_preco_full))[0]
    f_idx_full = np.where(f_mask_full & ~np.isnan(f_preco_full))[0]

    def _origem_pivo_espelho(idx, tipo):
        serie = highs if tipo == 'topo' else lows
        preco_alvo = t_preco_full[idx] if tipo == 'topo' else f_preco_full[idx]
        achou = idx
        for back in range(0, N_CONFIRM_PIVOS + 1):
            j = idx - back
            if j < 0:
                break
            if serie[j] == preco_alvo:
                achou = j
                break
        return achou

    for z in novas_zonas:
        tipo_espelho = 'fundo' if tipo_atual[z['origem_k']] == 'resistencia' else 'topo'
        idx_pool = f_idx_full if tipo_espelho == 'fundo' else t_idx_full
        preco_pool = f_preco_full if tipo_espelho == 'fundo' else t_preco_full
        c_min_z, c_max_z = z['zona_min'], z['zona_max']
        cand = idx_pool[(idx_pool > z['idx_criacao']) &
                         (preco_pool[idx_pool] >= c_min_z) & (preco_pool[idx_pool] <= c_max_z)]

        pesos, rejeicoes, agressoes, impulsos = [], [], [], []
        profundidades, purezas, retracoes_lst, aproximacoes = [], [], [], []
        detalhe_toques_espelho = []
        e_fundo_espelho = (tipo_espelho == 'fundo')
        forca_origem_espelho = _forca_origem(int(z['idx_criacao']), highs, lows, atrs)
        for ordem_n, conf_i in enumerate(cand, start=1):
            oi = _origem_pivo_espelho(conf_i, tipo_espelho)
            rej = sombra_inf[oi] if tipo_espelho == 'fundo' else sombra_sup[oi]
            vol_oi = vol_total_arr[oi] if tem_fluxo_arr[oi] else np.nan
            vol_rel = (vol_oi / vol_media20[oi]) if (not np.isnan(vol_oi) and vol_media20[oi] > 0) else None
            forte_toque = bool(fundo_forte_arr[conf_i] if tipo_espelho == 'fundo' else topo_forte_arr[conf_i])
            peso = _peso_toque(ordem_n, vol_rel, forte_toque)
            if tem_fluxo_arr[oi] and vol_total_arr[oi] > 0:
                delta_norm = delta_arr[oi] / vol_total_arr[oi]
                agr = float(np.clip(delta_norm if tipo_espelho == 'fundo' else -delta_norm, -1, 1))
            else:
                agr = 0.0
            idx_alvo = min(oi + N_IMP, n - 1)
            atr_oi = atrs[oi] if (not np.isnan(atrs[oi]) and atrs[oi] > 0) else np.nan
            imp = (abs(closes[idx_alvo] - closes[oi]) / (atr_oi * N_IMP)) if not np.isnan(atr_oi) else np.nan

            preco_extremo = lows[oi] if tipo_espelho == 'fundo' else highs[oi]
            profundidade = _profundidade_mitigacao(preco_extremo, c_min_z, c_max_z, e_fundo_espelho)
            pureza = _pureza_corpo(oi, opens, closes, highs, lows)
            retr = _retracoes_na_saida(oi, highs, lows, c_min_z, c_max_z, N_IMP)
            aprox = _analisar_aproximacao(oi, highs, lows, opens, closes, atrs,
                                           fundo_simples_arr, topo_simples_arr, e_fundo_espelho)

            pesos.append(peso)
            rejeicoes.append(rej)
            agressoes.append(agr)
            impulsos.append(imp)
            profundidades.append(profundidade)
            purezas.append(pureza)
            retracoes_lst.append(retr)
            aproximacoes.append(aprox['score'])
            detalhe_toques_espelho.append({
                'idx': int(conf_i), 'idx_origem': int(oi),
                'timestamp': _ts_iso(df['datetime'].values[conf_i]),
                'preco': float(preco_extremo),
                'tipo_pivo': 'forte' if forte_toque else 'simples',
                'rejeicao': round(float(rej), 3), 'agressao': round(float(agr), 3),
                'impulso': None if np.isnan(imp) else round(float(imp), 3),
                'peso': round(float(peso), 3),
                'profundidade': round(profundidade, 3), 'pureza_corpo': round(pureza, 3),
                'retracoes_saida': retr, 'aproximacao': aprox,
            })

        z['toques'] = len(cand)
        z['membros_conf'] = cand
        z['detalhe_toques'] = detalhe_toques_espelho
        z['intensidade_origem'] = forca_origem_espelho['intensidade_origem']
        z['base_limpa'] = forca_origem_espelho['base_limpa']
        if pesos:
            pesos_arr = np.array(pesos)
            z['forca_toques'] = float(pesos_arr.sum())
            z['rejeicao_media'] = float(np.average(rejeicoes, weights=pesos_arr))
            z['agressao_media'] = float(np.average(agressoes, weights=pesos_arr))
            z['profundidade_media'] = float(np.average(profundidades, weights=pesos_arr))
            z['pureza_corpo_media'] = float(np.average(purezas, weights=pesos_arr))
            z['retracoes_media'] = float(np.average(retracoes_lst, weights=pesos_arr))
            z['aproximacao_media'] = float(np.average(aproximacoes, weights=pesos_arr))
            impulsos_arr = np.array(impulsos)
            validos_imp = ~np.isnan(impulsos_arr)
            z['impulso_medio'] = (float(np.average(impulsos_arr[validos_imp], weights=pesos_arr[validos_imp]))
                                   if validos_imp.any() else np.nan)
        else:
            z['forca_toques'] = 0.0
            z['rejeicao_media'] = np.nan
            z['agressao_media'] = np.nan
            z['impulso_medio'] = np.nan
            z['profundidade_media'] = np.nan
            z['pureza_corpo_media'] = np.nan
            z['retracoes_media'] = np.nan
            z['aproximacao_media'] = np.nan

    # --- Anexa zonas espelho como candidatas adicionais ao re-ranking ---
    n_espelhos = len(novas_zonas)
    forca_herdada_full = np.full(n_clusters, np.nan)
    idade_espelho_full = np.full(n_clusters, np.nan)
    if n_espelhos:
        centros = np.concatenate([centros_orig, [z['preco_referencia'] for z in novas_zonas]])
        toques = np.concatenate([toques_orig, [z['toques'] for z in novas_zonas]])
        primeiro_idx = np.concatenate([primeiro_idx_orig, [z['idx_criacao'] for z in novas_zonas]])
        # validade: 2º toque p/ clusters originais (evita lookahead — ver
        # _clusterizar_pivos_sr), idx_criacao p/ zonas espelho (evento novo,
        # sem lookahead a evitar ali).
        validade_idx = np.concatenate([validade_idx_orig, [z['idx_criacao'] for z in novas_zonas]])
        zona_min = np.concatenate([zona_min_orig, [z['zona_min'] for z in novas_zonas]])
        zona_max = np.concatenate([zona_max_orig, [z['zona_max'] for z in novas_zonas]])
        idx_violacao_alta = np.concatenate([idx_violacao_alta_orig, [-1] * n_espelhos])
        idx_violacao_baixa = np.concatenate([idx_violacao_baixa_orig, [-1] * n_espelhos])
        forca_toques_zona_full = np.concatenate([forca_toques_zona, [z['forca_toques'] for z in novas_zonas]])
        rejeicao_media_zona_full = np.concatenate([rejeicao_media_zona, [z['rejeicao_media'] for z in novas_zonas]])
        agressao_media_zona_full = np.concatenate([agressao_media_zona, [z['agressao_media'] for z in novas_zonas]])
        impulso_medio_zona_full = np.concatenate([impulso_medio_zona, [z['impulso_medio'] for z in novas_zonas]])
        profundidade_media_zona_full = np.concatenate([profundidade_media_zona, [z['profundidade_media'] for z in novas_zonas]])
        pureza_corpo_media_zona_full = np.concatenate([pureza_corpo_media_zona, [z['pureza_corpo_media'] for z in novas_zonas]])
        retracoes_media_zona_full = np.concatenate([retracoes_media_zona, [z['retracoes_media'] for z in novas_zonas]])
        aproximacao_media_zona_full = np.concatenate([aproximacao_media_zona, [z['aproximacao_media'] for z in novas_zonas]])
        intensidade_origem_zona_full = np.concatenate([intensidade_origem_zona, [z['intensidade_origem'] for z in novas_zonas]])
        base_limpa_zona_full = np.concatenate([base_limpa_zona, [z['base_limpa'] for z in novas_zonas]])
        membros_conf_full = membros_conf + [z['membros_conf'] for z in novas_zonas]
        for idx_novo, z in enumerate(novas_zonas):
            k_origem, k_novo = z['origem_k'], n_clusters + idx_novo
            zona_espelho_id[k_origem] = k_novo
            status_timeline.append([(z['idx_criacao'], 'ativa')])
            forca_herdada_full = np.append(forca_herdada_full, z['forca_herdada'])
            idade_espelho_full = np.append(idade_espelho_full, np.nan)  # calculado por candle abaixo
    else:
        centros, toques = centros_orig, toques_orig
        primeiro_idx, validade_idx = primeiro_idx_orig, validade_idx_orig
        zona_min, zona_max = zona_min_orig, zona_max_orig
        idx_violacao_alta, idx_violacao_baixa = idx_violacao_alta_orig, idx_violacao_baixa_orig
        forca_toques_zona_full = forca_toques_zona
        rejeicao_media_zona_full = rejeicao_media_zona
        agressao_media_zona_full = agressao_media_zona
        impulso_medio_zona_full = impulso_medio_zona
        profundidade_media_zona_full = profundidade_media_zona
        pureza_corpo_media_zona_full = pureza_corpo_media_zona
        retracoes_media_zona_full = retracoes_media_zona
        aproximacao_media_zona_full = aproximacao_media_zona
        intensidade_origem_zona_full = intensidade_origem_zona
        base_limpa_zona_full = base_limpa_zona
        membros_conf_full = membros_conf

    idx_criacao_por_cluster = primeiro_idx  # p/ cálculo de idade do espelho por candle

    # --- Re-ranking incluindo zonas espelho (substitui o ranking inicial de calcular_sr) ---
    rank_idx_acima, rank_idx_abaixo, saida_rank = _rankear_niveis_sr(
        closes, atrs, centros, toques, primeiro_idx, zona_min, zona_max,
        validade_idx, idx_violacao_alta, idx_violacao_baixa, membros_conf_full, N)
    for col, arr in saida_rank.items():
        df[col] = arr
    df['sr_acima'] = df['sr_acima_1']
    df['sr_abaixo'] = df['sr_abaixo_1']
    df['dist_sr_acima_atr'] = df['sr_acima_1_dist_atr']
    df['dist_sr_abaixo_atr'] = df['sr_abaixo_1_dist_atr']
    df['qualidade_sr_acima'] = df['sr_acima_1_toques']
    df['qualidade_sr_abaixo'] = df['sr_abaixo_1_toques']

    # Timeline por cluster (searchsorted) — só clusters originais têm transições reais
    timeline_idx = [np.array([t[0] for t in tl]) for tl in status_timeline]
    timeline_status = [np.array([t[1] for t in tl]) for tl in status_timeline]

    def _status_no_candle(k, i):
        idxs = timeline_idx[k]
        pos = np.searchsorted(idxs, i, side='right') - 1
        return timeline_status[k][pos] if pos >= 0 else 'ativa'

    saida2 = {}
    for lado in lados:
        for r in range(1, N + 1):
            pref = f'sr_{lado}_{r}'
            saida2[f'{pref}_status'] = np.full(n, 'ativa', dtype=object)
            saida2[f'{pref}_rejeicao_media'] = np.full(n, np.nan)
            saida2[f'{pref}_agressao_media'] = np.full(n, np.nan)
            saida2[f'{pref}_impulso_medio'] = np.full(n, np.nan)
            saida2[f'{pref}_forca_toques'] = np.full(n, np.nan)
            saida2[f'{pref}_forca_herdada'] = np.full(n, np.nan)
            saida2[f'{pref}_zona_espelho_id'] = np.full(n, np.nan)
            saida2[f'{pref}_rompimento_hoje'] = np.full(n, 'nenhum', dtype=object)
            saida2[f'{pref}_profundidade_media'] = np.full(n, np.nan)
            saida2[f'{pref}_pureza_corpo_media'] = np.full(n, np.nan)
            saida2[f'{pref}_retracoes_media'] = np.full(n, np.nan)
            saida2[f'{pref}_aproximacao_score'] = np.full(n, np.nan)
            saida2[f'{pref}_intensidade_origem'] = np.full(n, np.nan)
            saida2[f'{pref}_base_limpa'] = np.full(n, np.nan)

    for i in range(n):
        for r in range(N):
            for lado, rank_idx in (('acima', rank_idx_acima), ('abaixo', rank_idx_abaixo)):
                k = rank_idx[i, r]
                if k == -1:
                    continue
                pref = f'sr_{lado}_{r+1}'
                saida2[f'{pref}_status'][i] = _status_no_candle(k, i)
                saida2[f'{pref}_rompimento_hoje'][i] = _status_intraday_no_candle(k, i)
                saida2[f'{pref}_rejeicao_media'][i] = rejeicao_media_zona_full[k]
                saida2[f'{pref}_agressao_media'][i] = agressao_media_zona_full[k]
                saida2[f'{pref}_impulso_medio'][i] = impulso_medio_zona_full[k]
                saida2[f'{pref}_forca_toques'][i] = forca_toques_zona_full[k]
                saida2[f'{pref}_profundidade_media'][i] = profundidade_media_zona_full[k]
                saida2[f'{pref}_pureza_corpo_media'][i] = pureza_corpo_media_zona_full[k]
                saida2[f'{pref}_retracoes_media'][i] = retracoes_media_zona_full[k]
                saida2[f'{pref}_aproximacao_score'][i] = aproximacao_media_zona_full[k]
                saida2[f'{pref}_intensidade_origem'][i] = intensidade_origem_zona_full[k]
                saida2[f'{pref}_base_limpa'][i] = base_limpa_zona_full[k]
                if k < n_clusters and zona_espelho_id[k] != -1:
                    saida2[f'{pref}_zona_espelho_id'][i] = zona_espelho_id[k]
                elif n_espelhos and k >= n_clusters:
                    saida2[f'{pref}_forca_herdada'][i] = forca_herdada_full[k]

    for col, arr in saida2.items():
        df[col] = arr

    df.attrs['_clusters_sr'] = clusters  # mantém consistente para etapas seguintes
    df.attrs['_rank_idx_acima'] = rank_idx_acima
    df.attrs['_rank_idx_abaixo'] = rank_idx_abaixo
    df.attrs['_zonas_sr_status_timeline'] = status_timeline
    df.attrs['_zonas_espelho'] = _SemDeepcopy(novas_zonas)
    df.attrs['_zona_espelho_id_por_cluster'] = zona_espelho_id
    return df


def calcular_pivo_imediato(df: pd.DataFrame) -> pd.DataFrame:
    """
    Referência tática de curtíssimo prazo — DISTINTA do ranking de zonas
    fortes (calcular_sr/calcular_zonas_sr). Caso real que motivou (WIN,
    08/07/2026): o Mentor IA usava só o suporte mais próximo do RANKING
    (172.362, zona madura mas parada há dias) e ignorava um fundo que tinha
    acabado de imprimir bem mais perto do preço (172.275, 1º toque, ainda
    sem reteste) — o nível que Jesué de fato monitora primeiro na leitura
    gráfica. Um fundo/topo isolado nunca vira cluster "válido" pro ranking
    até o 2º toque (`validade_idx` em `_clusterizar_pivos_sr`) — essa função
    preenche exatamente essa lacuna, sem alterar o ranking de zonas fortes:
    apenas o ÚLTIMO fundo/topo confirmado (ffill causal, sem lookahead —
    `preco_fundo`/`preco_topo` já são gravados na posição de confirmação),
    válido enquanto ainda estiver do lado certo do preço (fundo < close,
    topo > close).

    `_ja_mapeado`: True se esse preço já cai dentro da zona (zona_min-
    zona_max) de algum dos N_NIVEIS_SR já rankeados do mesmo lado — nesse
    caso é redundante exibir separadamente (já aparece no card da zona
    forte). Roda DEPOIS de calcular_zonas_sr (precisa das zonas já
    rankeadas para essa checagem).
    """
    df = df.copy()
    n = len(df)
    N = cfg.N_NIVEIS_SR
    idx_arr = np.arange(n)
    closes = df['close'].values

    fundo_preco = df['preco_fundo'].where(df['fundo_simples']).ffill().values
    fundo_idx = pd.Series(np.where(df['fundo_simples'].values, idx_arr, np.nan)).ffill().values
    topo_preco = df['preco_topo'].where(df['topo_simples']).ffill().values
    topo_idx = pd.Series(np.where(df['topo_simples'].values, idx_arr, np.nan)).ffill().values

    abaixo_valido = ~np.isnan(fundo_preco) & (fundo_preco < closes)
    acima_valido = ~np.isnan(topo_preco) & (topo_preco > closes)

    df['pivo_imediato_abaixo'] = np.where(abaixo_valido, fundo_preco, np.nan)
    df['pivo_imediato_abaixo_idade'] = np.where(abaixo_valido, idx_arr - fundo_idx, np.nan)
    df['pivo_imediato_acima'] = np.where(acima_valido, topo_preco, np.nan)
    df['pivo_imediato_acima_idade'] = np.where(acima_valido, idx_arr - topo_idx, np.nan)

    ja_mapeado_abaixo = np.zeros(n, dtype=bool)
    ja_mapeado_acima = np.zeros(n, dtype=bool)
    for r in range(1, N + 1):
        zmin = df.get(f'sr_abaixo_{r}_zona_min')
        zmax = df.get(f'sr_abaixo_{r}_zona_max')
        if zmin is not None:
            dentro = (df['pivo_imediato_abaixo'] >= zmin) & (df['pivo_imediato_abaixo'] <= zmax)
            ja_mapeado_abaixo |= dentro.fillna(False).values
        zmin_a = df.get(f'sr_acima_{r}_zona_min')
        zmax_a = df.get(f'sr_acima_{r}_zona_max')
        if zmin_a is not None:
            dentro_a = (df['pivo_imediato_acima'] >= zmin_a) & (df['pivo_imediato_acima'] <= zmax_a)
            ja_mapeado_acima |= dentro_a.fillna(False).values
    df['pivo_imediato_abaixo_ja_mapeado'] = ja_mapeado_abaixo
    df['pivo_imediato_acima_ja_mapeado'] = ja_mapeado_acima

    return df


def calcular_prob_niveis_sr(df: pd.DataFrame) -> pd.DataFrame:
    """
    Score heurístico (0-100%) de probabilidade de BOUNCE (reação/respeito ao
    nível) em oposição a ROMPIMENTO, por nível rankeado (sr_acima_1..N,
    sr_abaixo_1..N) — reescrito em 2026-07-13 (ver
    especificacao_forca_zonas_price_action.md) pra compor 3 perguntas
    distintas em vez de um score cego:

    - `combustivel_score` (Bloco 1, `_compor_combustivel_score`): frescor da
      zona — força de toques (satura via tanh, não mais um teto raso que
      igualava 6/11/14/84 toques), decaimento por IDADE (agora aplicado a
      QUALQUER zona, não só espelho), profundidade média de mitigação,
      intensidade/limpeza na origem.
    - `aproximacao_score` (Bloco 2, `_analisar_aproximacao`, já agregado por
      calcular_zonas_sr): qualidade da perna de preço que levou aos toques
      — esforço×resultado, estrutura impulsiva/corretiva, canal, correção
      tempo/preço, estrutura de topos/fundos intermediários, velocidade da
      última perna. Peça inteiramente nova, não existia antes desta entrega.
    - `rejeicao_score` (Bloco 3, `_compor_rejeicao_score`): qualidade da
      reação no toque em si — rejeição por wick, agressão, impulso
      pós-toque, pureza do corpo, retrações na saída, e (deliberadamente
      dobrados aqui pra dentro, não mais soltos por candle — ver achado da
      investigação) alinhamento com tendência/fase/saúde, fluxo/delta
      recente e sinais de Big Players.

    Cada bloco é uma coluna própria (`{pref}_combustivel_score`,
    `{pref}_aproximacao_score`, `{pref}_rejeicao_score`) pra manter
    rastreabilidade (seção 8 da spec, "nada de black box") — o
    `{pref}_prob_bounce` final combina os 3 (proporcional ao desvio de 50)
    mais ajustes que não são responsabilidade de nenhum bloco específico
    (força estrutural `nivel_forte`, atenuação por distância, status de
    bipolaridade, rompimento intradiário) — estes últimos preservam a
    mesma forma de antes, incluindo os pesos já calibrados empiricamente
    (PESO_ROMPIMENTO_INTRADAY*, ver CHANGELOG.md).

    IMPORTANTE: é uma heurística de engenharia documentada, não uma
    probabilidade estatística calibrada — por isso o resultado é sempre
    limitado a [SCORE_PROB_MIN, SCORE_PROB_MAX] (nunca 0% ou 100%), alinhado
    à analogia meteorológica do prompt do Mentor IA (nunca apresentar
    probabilidade como certeza).

    Roda no FINAL do pipeline porque depende de colunas produzidas por
    calcular_fluxo_agregado/calcular_buildup_e_double_pressure (fluxo
    acumulado, razão de agressão, double pressure), que rodam depois de
    calcular_sr/calcular_zonas_sr em calcular_todos_features.
    """
    df = df.copy()
    n = len(df)
    N = cfg.N_NIVEIS_SR
    lados = ('acima', 'abaixo')

    tendencia = df['estado_tendencia'].values if 'estado_tendencia' in df.columns else np.full(n, 'indefinido', dtype=object)
    fase = df['fase_tendencia'].values if 'fase_tendencia' in df.columns else np.full(n, 'indefinido', dtype=object)
    saude = df['saude_tendencia'].values if 'saude_tendencia' in df.columns else np.full(n, 'indefinido', dtype=object)
    delta_acum = pd.to_numeric(df['delta_acumulado_20'], errors='coerce').values if 'delta_acumulado_20' in df.columns else np.full(n, np.nan)
    razao_agr = pd.to_numeric(df['razao_agressao'], errors='coerce').values if 'razao_agressao' in df.columns else np.full(n, np.nan)

    # Big Players simplificado — absorção (candle contra a direção do delta) e
    # volume anômalo (>2 desvios padrão), via rolling — mesmo espírito de
    # construir_big_players, mas vetorizado e sem lógica de HTML.
    direcao = df['direcao_candle'].values if 'direcao_candle' in df.columns else np.zeros(n)
    delta_raw = pd.to_numeric(df['delta'], errors='coerce').fillna(0).values if 'delta' in df.columns else np.zeros(n)
    vol_c = pd.to_numeric(df['vol_comprador'], errors='coerce').fillna(0).values if 'vol_comprador' in df.columns else np.zeros(n)
    vol_v = pd.to_numeric(df['vol_vendedor'], errors='coerce').fillna(0).values if 'vol_vendedor' in df.columns else np.zeros(n)
    vol_total = vol_c + vol_v
    absorcao_compra = (direcao == -1) & (delta_raw > 0)
    absorcao_venda = (direcao == 1) & (delta_raw < 0)
    vol_s = pd.Series(vol_total)
    media_vol = vol_s.rolling(500, min_periods=20).mean()
    std_vol = vol_s.rolling(500, min_periods=20).std()
    volume_anomalo = (vol_s > (media_vol + 2 * std_vol)).fillna(False).values

    for lado in lados:
        eh_resistencia = (lado == 'acima')
        for r in range(1, N + 1):
            pref = f'sr_{lado}_{r}'
            if f'{pref}_forte' not in df.columns:
                df[f'{pref}_prob_bounce'] = np.nan
                df[f'{pref}_combustivel_score'] = np.nan
                df[f'{pref}_aproximacao_score'] = np.nan
                df[f'{pref}_rejeicao_score'] = np.nan
                df[f'{pref}_postura'] = None
                df[f'{pref}_magnitude_esperada'] = None
                df[f'{pref}_invalidacao'] = np.nan
                continue

            toques = pd.to_numeric(df[f'{pref}_toques'], errors='coerce').fillna(0).values
            forte = df[f'{pref}_forte'].values.astype(bool)
            dist_atr = pd.to_numeric(df[f'{pref}_dist_atr'], errors='coerce').values
            forca_toques = pd.to_numeric(df.get(f'{pref}_forca_toques', np.nan), errors='coerce').values
            rejeicao_media = pd.to_numeric(df.get(f'{pref}_rejeicao_media', np.nan), errors='coerce').values
            agressao_media = pd.to_numeric(df.get(f'{pref}_agressao_media', np.nan), errors='coerce').values
            impulso_medio = pd.to_numeric(df.get(f'{pref}_impulso_medio', np.nan), errors='coerce').values
            status = df.get(f'{pref}_status', pd.Series(['ativa'] * n)).values
            forca_herdada = pd.to_numeric(df.get(f'{pref}_forca_herdada', np.nan), errors='coerce').values
            idade = pd.to_numeric(df.get(f'{pref}_idade', np.nan), errors='coerce').values
            zona_espelho_id = df.get(f'{pref}_zona_espelho_id', pd.Series([np.nan] * n)).values
            profundidade_media = pd.to_numeric(df.get(f'{pref}_profundidade_media', np.nan), errors='coerce').values
            pureza_corpo_media = pd.to_numeric(df.get(f'{pref}_pureza_corpo_media', np.nan), errors='coerce').values
            retracoes_media = pd.to_numeric(df.get(f'{pref}_retracoes_media', np.nan), errors='coerce').values
            aproximacao_bruto = pd.to_numeric(df.get(f'{pref}_aproximacao_score', np.nan), errors='coerce').values
            intensidade_origem = pd.to_numeric(df.get(f'{pref}_intensidade_origem', np.nan), errors='coerce').values
            base_limpa = pd.to_numeric(df.get(f'{pref}_base_limpa', np.nan), errors='coerce').values
            zona_min = pd.to_numeric(df.get(f'{pref}_zona_min', np.nan), errors='coerce').values
            zona_max = pd.to_numeric(df.get(f'{pref}_zona_max', np.nan), errors='coerce').values

            valido = ~np.isnan(dist_atr)

            # --- Bloco 1: combustível de liquidez ---
            decaimento_idade = _decaimento_idade(idade)
            combustivel = _compor_combustivel_score(forca_toques, decaimento_idade, profundidade_media,
                                                     intensidade_origem, base_limpa)
            # forca_herdada (zona espelho) — decaimento exponencial no tempo,
            # agora dobrado pra dentro do combustível (é literalmente fuel
            # herdado da zona-mãe, não um termo solto)
            tem_herdada = ~np.isnan(forca_herdada)
            if tem_herdada.any():
                idade_espelho = np.where(tem_herdada, idade, np.nan)
                tau = max(cfg.IDADE_MINIMA_NIVEL_FORTE, 1)
                decaimento_herdada = np.exp(-np.nan_to_num(idade_espelho, nan=0.0) / tau)
                combustivel = np.clip(combustivel + np.where(tem_herdada, forca_herdada * decaimento_herdada * 0.5, 0), 0, 100)

            # --- Bloco 2: qualidade da aproximação (já agregado por zona em calcular_zonas_sr) ---
            aproximacao = np.nan_to_num(aproximacao_bruto, nan=50.0)

            # --- Bloco 3: qualidade da rejeição no toque — refina o que já
            # existia (wick/agressão/impulso) com pureza de corpo e
            # retrações, e dobra pra dentro os termos que antes eram
            # compartilhados por candle (tendência/fase/saúde, fluxo/delta,
            # Big Players) — causa raiz do bug de "score idêntico entre
            # zonas diferentes" (ver docstring acima e especificação).
            rejeicao = _compor_rejeicao_score(rejeicao_media, agressao_media, impulso_medio,
                                               pureza_corpo_media, retracoes_media)

            alta = tendencia == 'alta'
            baixa = tendencia == 'baixa'
            lateral = ~alta & ~baixa
            fase_inicio_meio = np.isin(fase, ['inicio', 'meio'])
            saude_forte = saude == 'forte'
            alinhamento = np.zeros(n)
            if eh_resistencia:
                alinhamento += np.where(alta & fase_inicio_meio, -cfg.PESO_TENDENCIA_FASE, 0)
                alinhamento += np.where(baixa & saude_forte, cfg.PESO_TENDENCIA_FASE, np.where(baixa, cfg.PESO_TENDENCIA_FASE / 2, 0))
            else:
                alinhamento += np.where(alta & saude_forte, cfg.PESO_TENDENCIA_FASE, np.where(alta, cfg.PESO_TENDENCIA_FASE / 2, 0))
                alinhamento += np.where(baixa & fase_inicio_meio, -cfg.PESO_TENDENCIA_FASE, 0)
            alinhamento += np.where(lateral, cfg.PESO_LATERAL_BOUNCE, 0)

            delta_favor_rompimento = ((eh_resistencia) & (delta_acum > 0)) | ((~eh_resistencia) & (delta_acum < 0))
            tem_delta = ~np.isnan(delta_acum)
            alinhamento += np.where(tem_delta & delta_favor_rompimento, -cfg.PESO_FLUXO_DELTA, 0)
            alinhamento += np.where(tem_delta & ~delta_favor_rompimento, cfg.PESO_FLUXO_DELTA, 0)

            razao_favor_rompimento = ((eh_resistencia) & (razao_agr > 0.65)) | ((~eh_resistencia) & (razao_agr < 0.35))
            razao_favor_bounce = ((eh_resistencia) & (razao_agr < 0.35)) | ((~eh_resistencia) & (razao_agr > 0.65))
            alinhamento += np.where(razao_favor_rompimento, -cfg.PESO_FLUXO_RAZAO, 0)
            alinhamento += np.where(razao_favor_bounce, cfg.PESO_FLUXO_RAZAO, 0)

            absorcao_favor_bounce = (eh_resistencia & absorcao_venda) | (~eh_resistencia & absorcao_compra)
            alinhamento += np.where(absorcao_favor_bounce, cfg.PESO_BIG_PLAYERS_ABSORCAO, 0)
            alinhamento += np.where(volume_anomalo, cfg.PESO_BIG_PLAYERS_VOLUME, 0)

            rejeicao = np.clip(rejeicao + alinhamento, 0, 100)

            df[f'{pref}_combustivel_score'] = np.where(valido, np.round(combustivel, 1), np.nan)
            df[f'{pref}_aproximacao_score'] = np.where(valido, np.round(aproximacao, 1), np.nan)
            df[f'{pref}_rejeicao_score'] = np.where(valido, np.round(rejeicao, 1), np.nan)

            # --- Combinação final + ajustes que não pertencem a nenhum bloco específico ---
            score = np.full(n, 50.0)
            score += (combustivel - 50) / 50 * cfg.AMPLITUDE_BLOCO_COMBUSTIVEL
            score += (aproximacao - 50) / 50 * cfg.AMPLITUDE_BLOCO_APROXIMACAO
            score += (rejeicao - 50) / 50 * cfg.AMPLITUDE_BLOCO_REJEICAO

            score += np.where(forte, cfg.PESO_NIVEL_FORTE, cfg.PESO_NIVEL_FRACO_PENALIDADE)

            longe = dist_atr > cfg.DIST_ATR_LONGE
            score = np.where(longe, 50 + (score - 50) * cfg.ATENUACAO_NIVEL_DISTANTE, score)

            aguardando = np.isin(status, ['aguardando_pullback', 'aguardando_throwback'])
            rompida_sem_espelho = (status == 'rompida') & np.isnan(zona_espelho_id)
            score += np.where(aguardando, cfg.PESO_STATUS_AGUARDANDO_RETESTE, 0)
            score += np.where(rompida_sem_espelho, cfg.PESO_STATUS_ROMPIDA_SEM_ESPELHO, 0)

            # Rompimento intradiário (sinal ao vivo, escopo do próprio pregão —
            # ver comentário em calcular_zonas_sr sobre por que isso é
            # diferente do status estrutural acima). Pesos CALIBRADOS
            # empiricamente (ver CHANGELOG.md) — mantidos fora dos blocos,
            # aplicados diretamente ao score final.
            rompimento_hoje = df.get(f'{pref}_rompimento_hoje', pd.Series(['nenhum'] * n)).values
            score += np.where(rompimento_hoje == 'rompeu_hoje', -cfg.PESO_ROMPIMENTO_INTRADAY, 0)
            score += np.where(rompimento_hoje == 'pullback_confirmado_hoje', cfg.PESO_ROMPIMENTO_INTRADAY_PULLBACK, 0)

            score = np.clip(score, cfg.SCORE_PROB_MIN, cfg.SCORE_PROB_MAX)
            df[f'{pref}_prob_bounce'] = np.where(valido, np.round(score, 1), np.nan)

            # --- Camada de ação (postura/magnitude/invalidação) — por candle,
            # via montar_acao (regra explícita, nunca texto livre) ---
            posturas = np.full(n, None, dtype=object)
            magnitudes = np.full(n, None, dtype=object)
            invalidacoes = np.full(n, np.nan)
            idx_validos = np.where(valido)[0]
            for i in idx_validos:
                acao = montar_acao(float(combustivel[i]), float(aproximacao[i]), float(rejeicao[i]),
                                    eh_resistencia, float(zona_min[i]), float(zona_max[i]))
                posturas[i] = acao['postura']
                magnitudes[i] = acao['magnitude_esperada']
                invalidacoes[i] = acao['invalidacao'] if acao['invalidacao'] is not None else np.nan
            df[f'{pref}_postura'] = posturas
            df[f'{pref}_magnitude_esperada'] = magnitudes
            df[f'{pref}_invalidacao'] = invalidacoes

    return df


# =============================================================================
# BLOCO 6 — BUILD-UP E DOUBLE PRESSURE
# =============================================================================

def calcular_buildup_e_double_pressure(df: pd.DataFrame, janela: int = 8) -> pd.DataFrame:
    """
    Build-up (compressão de volatilidade) e double pressure — vetorizado.

    Reescrito em 2026-07-04: existiam DUAS definições desta função no
    arquivo (bug de duplicação) — a segunda (esta) sobrescrevia a primeira
    silenciosamente e era a única de fato executada. Ela usava
    `df.loc[i, col] = valor` dentro de um loop Python por candle (mais um
    loop interno de até 30 iterações por candle, para `buildup_duracao`) —
    `.loc` escalar em loop é uma das operações mais caras do pandas, e essa
    função sozinha chegava a ~16-19s sobre ~56-57 mil candles, de longe o
    maior gargalo do pipeline de features (ver CHANGELOG.md). A primeira
    definição (morta, nunca executada) já era vetorizada mas tinha
    semântica sutilmente diferente (média incluindo o candle atual, duração
    como streak da média em vez de streak por candle individual) — por
    segurança, esta reescrita preserva EXATAMENTE a semântica da versão que
    estava de fato rodando (a segunda), não a da versão morta.
    """
    df = df.copy()
    n = len(df)

    amplitudes = (df['high'] - df['low']).values
    atrs       = df['atr'].values
    direcoes   = df['direcao_candle'].values
    tem_fluxo  = df['fluxo_disponivel'].values if 'fluxo_disponivel' in df.columns else np.zeros(n, dtype=bool)
    deltas     = pd.to_numeric(df['delta'], errors='coerce').values if 'delta' in df.columns else np.full(n, np.nan)
    sombra_sup = df['sombra_sup_pct'].values
    sombra_inf = df['sombra_inf_pct'].values

    # Só calcula a partir do candle `janela` (antes disso, fica nos defaults
    # abaixo) e só onde o ATR do próprio candle é válido — mesmo gate do
    # loop original (`if isnan(atr) or atr == 0: continue`).
    atr_valido = ~np.isnan(atrs) & (atrs != 0)
    i_valido = atr_valido & (np.arange(n) >= janela)

    # Buildup: média das `janela` amplitudes ANTERIORES ao candle atual
    # (não inclui o candle i) / ATR do candle atual — rolling(janela) na
    # posição i-1 cobre exatamente os índices [i-janela, i-1], daí o shift.
    with np.errstate(divide='ignore', invalid='ignore'):
        amp_roll_excl = pd.Series(amplitudes).rolling(janela).mean().shift(1).values
        pct_atr = amp_roll_excl / atrs

    buildup_ativo   = i_valido & (pct_atr <= 0.5)
    buildup_pct_atr = np.where(i_valido, np.round(pct_atr, 3), np.nan)

    # Duração: candles consecutivos ANTERIORES individualmente comprimidos
    # (amplitude <= 0.5×ATR do próprio candle), capada em 30 — streak via
    # groupby-cumsum (equivalente ao loop de busca retroativa, sem Python
    # por linha).
    comprimido = ~np.isnan(atrs) & (atrs > 0) & (amplitudes <= 0.5 * atrs)
    grupo_comp = (~comprimido).cumsum()
    streak_comp = pd.Series(comprimido.astype(int)).groupby(grupo_comp).cumsum().values
    streak_comp = np.minimum(streak_comp, 30)
    duracao = np.zeros(n, dtype=int)
    duracao[1:] = streak_comp[:-1]  # duracao[i] = streak comprimido terminando em i-1
    duracao = np.where(i_valido, duracao, 0)

    # Double pressure — mesmo branch do loop original: usa fluxo real
    # quando disponível, senão o proxy de "fechou no extremo" via sombra.
    tem_delta_valido = tem_fluxo & ~np.isnan(deltas)
    dp_fluxo = tem_delta_valido & (((direcoes == 1) & (deltas > 0)) | ((direcoes == -1) & (deltas < 0)))
    dp_proxy = (~tem_delta_valido) & (((direcoes == 1) & (sombra_sup < 0.15)) | ((direcoes == -1) & (sombra_inf < 0.15)))
    dp_arr = i_valido & (dp_fluxo | dp_proxy)

    # Streak de double_pressure — mesma técnica de groupby-cumsum.
    grupo_dp = (~dp_arr).cumsum()
    streak = pd.Series(dp_arr.astype(int)).groupby(grupo_dp).cumsum().values
    streak = np.where(dp_arr, streak, 0)

    df['buildup_ativo']   = buildup_ativo
    df['buildup_duracao'] = duracao
    df['buildup_pct_atr'] = buildup_pct_atr
    df['double_pressure'] = dp_arr
    df['dp_streak']       = streak
    df['dp_qualidade'] = pd.cut(
        streak, bins=[-1, 0, 2, 4, np.inf],
        labels=['indefinido', 'baixa', 'media', 'alta']
    )

    return df


# =============================================================================
# BLOCO 7 — TEMPORAL
# =============================================================================

def calcular_temporal(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    def _janela(dt):
        t = dt.time() if hasattr(dt, 'time') else dt
        for nome, (inicio_str, fim_str) in cfg.JANELAS_PREGAO.items():
            h_i, m_i = map(int, inicio_str.split(':'))
            h_f, m_f = map(int, fim_str.split(':'))
            if time(h_i, m_i) <= t < time(h_f, m_f):
                return nome
        return 'fora_pregao'

    df['janela_pregao'] = df['datetime'].apply(_janela)
    df['dia_semana']    = pd.to_datetime(df['datetime']).dt.day_name()
    return df


# =============================================================================
# BLOCO 8 — FLUXO AGREGADO
# =============================================================================

def calcular_fluxo_agregado(df: pd.DataFrame, janela: int = 20) -> pd.DataFrame:
    df = df.copy()

    tem_fluxo = df['fluxo_disponivel'] == True if 'fluxo_disponivel' in df.columns else pd.Series([False]*len(df))
    delta = pd.to_numeric(df['delta'], errors='coerce')
    vol_c = pd.to_numeric(df.get('vol_comprador', pd.Series([np.nan]*len(df))), errors='coerce')
    vol_v = pd.to_numeric(df.get('vol_vendedor',  pd.Series([np.nan]*len(df))), errors='coerce')

    # Rolling onde há fluxo — eficiente com pandas
    delta_roll = delta.where(tem_fluxo)
    df['delta_acumulado_20'] = delta_roll.rolling(janela, min_periods=1).sum()
    df['delta_acumulado_20'] = df['delta_acumulado_20'].where(tem_fluxo)

    total = vol_c + vol_v
    razao = (vol_c / total).where(total > 0)
    df['razao_agressao'] = razao.where(tem_fluxo).round(3)

    return df


# =============================================================================
# PIPELINE COMPLETO
# =============================================================================

def calcular_todos_features(df: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    n = len(df)
    if verbose:
        print(f"[features] Calculando features para {n} candles...")

    etapas = [
        ("Anatomia de candle",          calcular_anatomia),
        ("ATR e tamanho relativo",       calcular_atr),
        ("Pivôs estruturais",           detectar_pivos),
        ("Tendência e fase",            calcular_tendencia),
        ("S/R por clustering",          calcular_sr),
        ("Zonas S/R e bipolaridade",    calcular_zonas_sr),
        ("Pivô imediato (tático)",      calcular_pivo_imediato),
        ("Build-up e double pressure",  calcular_buildup_e_double_pressure),
        ("Temporal",                    calcular_temporal),
        ("Fluxo agregado",              calcular_fluxo_agregado),
        ("Score de probabilidade S/R",  calcular_prob_niveis_sr),
    ]

    _attrs_zonas_sr = {}
    for nome, func in etapas:
        if verbose:
            print(f"  → {nome}...")
        df = func(df)
        if nome == "Zonas S/R e bipolaridade":
            _attrs_zonas_sr = dict(df.attrs)  # snapshot — ver reaplicação abaixo

    # Salvaguarda: propagação de df.attrs através de operações do pandas é
    # "experimental" e não garantida em toda versão/etapa — reaplica
    # explicitamente por garantia, caso alguma etapa seguinte não preserve.
    df.attrs.update(_attrs_zonas_sr)

    if verbose:
        features_adicionadas = [c for c in df.columns
                                if c not in ['datetime','open','high','low','close','fonte']]
        print(f"[features] Concluído. {len(features_adicionadas)} features calculadas.")

    return df


# =============================================================================
# TESTE STANDALONE
# =============================================================================

if __name__ == '__main__':
    import persistencia
    import time as _time

    print("=" * 90)
    print("  TESTE STANDALONE — features.py v2")
    print("=" * 90)

    df = persistencia.carregar_candles('WIN')
    if df is None:
        print("Rode setup_inicial('WIN') primeiro.")
    else:
        print(f"Base carregada: {len(df)} candles")
        t0 = _time.time()
        df_features = calcular_todos_features(df)
        t1 = _time.time()
        print(f"\nTempo total: {t1-t0:.1f}s")
        print(df_features[['datetime','tipo_candle','estado_tendencia',
                            'fase_tendencia','buildup_ativo','double_pressure',
                            'dp_qualidade']].tail(10).to_string(index=False))

        print("\n" + "=" * 90)
        print("  S/R MULTI-NÍVEL (Fase 1) — últimos 15 candles")
        print("=" * 90)
        import config_backtest as _cfg
        cols_inspecao = ['datetime', 'close']
        for lado in ('acima', 'abaixo'):
            for r in range(1, _cfg.N_NIVEIS_SR + 1):
                pref = f'sr_{lado}_{r}'
                cols_inspecao += [pref, f'{pref}_toques', f'{pref}_idade',
                                  f'{pref}_violado', f'{pref}_forte', f'{pref}_status',
                                  f'{pref}_prob_bounce']
        cols_existentes = [c for c in cols_inspecao if c in df_features.columns]
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', 220)
        print(df_features[cols_existentes].tail(15).to_string(index=False))

        print("\n" + "=" * 90)
        print("  ZONAS COM STATUS != 'ativa' no último candle (bipolaridade)")
        print("=" * 90)
        ultimo = df_features.iloc[-1]
        achou_alguma = False
        for lado in ('acima', 'abaixo'):
            for r in range(1, _cfg.N_NIVEIS_SR + 1):
                pref = f'sr_{lado}_{r}'
                status = ultimo.get(f'{pref}_status', 'ativa')
                if status != 'ativa':
                    achou_alguma = True
                    print(f"  {pref}: preço={ultimo.get(pref):.0f} status={status} "
                          f"toques={ultimo.get(f'{pref}_toques')} "
                          f"zona=[{ultimo.get(f'{pref}_zona_min'):.0f};{ultimo.get(f'{pref}_zona_max'):.0f}] "
                          f"espelho_id={ultimo.get(f'{pref}_zona_espelho_id')}")
        if not achou_alguma:
            print("  Nenhuma zona com status != 'ativa' no candle mais recente.")

