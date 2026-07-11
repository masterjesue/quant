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
                'prob_bounce': 50.0, 'janela': 0, 'linha_atual': None}

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

        pesos.append(peso)
        rejeicoes.append(rej)
        agressoes.append(agr)
        impulsos.append(imp)

    pesos_arr = np.array(pesos)
    toques = len(idxs_touch)
    forca_toques = float(pesos_arr.sum()) if toques else 0.0
    rejeicao_media = float(np.average(rejeicoes, weights=pesos_arr)) if toques and pesos_arr.sum() > 0 else np.nan
    agressao_media = float(np.average(agressoes, weights=pesos_arr)) if toques and pesos_arr.sum() > 0 else np.nan
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
    for i in range(ini, n):
        atr_i = atrs[i]
        if np.isnan(atr_i) or atr_i <= 0:
            continue
        corpo_i = abs(closes[i] - opens[i])
        rompeu = (closes[i] < linha[i]) if e_fundo else (closes[i] > linha[i])
        if rompeu and corpo_i >= cfg.CORPO_MINIMO_ROMPIMENTO_ATR * atr_i:
            violado = True
        elif violado:
            recuperou = (closes[i] >= linha[i]) if e_fundo else (closes[i] <= linha[i])
            if recuperou:
                violado = False

    forte = (toques >= toques_min_forte) and not violado

    score = 50.0
    if toques:
        score += 10.0 if forte else -5.0
        score += min(forca_toques, 10) * 1.5
        if not np.isnan(rejeicao_media):
            score += rejeicao_media * 10
        if not np.isnan(agressao_media):
            score += agressao_media * 8  # já orientado favoravelmente (ver cálculo de `agr` acima)
        if not np.isnan(impulso_medio):
            score += min(impulso_medio, 2.0) * 3
    if violado:
        score -= 15.0
    prob_bounce = float(np.clip(score, cfg.SCORE_PROB_MIN, cfg.SCORE_PROB_MAX))

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
    atrs = df['atr'].values
    corpo = (df['close'] - df['open']).abs().values
    sombra_sup = df['sombra_sup_pct'].values if 'sombra_sup_pct' in df.columns else np.zeros(n)
    sombra_inf = df['sombra_inf_pct'].values if 'sombra_inf_pct' in df.columns else np.zeros(n)
    topo_forte_arr = df['topo_forte'].values if 'topo_forte' in df.columns else np.ones(n, dtype=bool)
    fundo_forte_arr = df['fundo_forte'].values if 'fundo_forte' in df.columns else np.ones(n, dtype=bool)
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
    N_IMP = cfg.N_CANDLES_IMPULSO

    for k in range(n_clusters):
        conf = membros_conf[k]
        origem = membros_origem[k]
        tipo = membros_tipo[k]
        ordem_tempo = np.argsort(conf)
        pesos, rejeicoes, agressoes, impulsos = [], [], [], []
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

            pesos.append(peso)
            rejeicoes.append(rej)
            agressoes.append(agr)
            impulsos.append(imp)

        pesos = np.array(pesos)
        forca_toques_zona[k] = pesos.sum()
        if pesos.sum() > 0:
            rejeicao_media_zona[k] = float(np.average(rejeicoes, weights=pesos))
            agressao_media_zona[k] = float(np.average(agressoes, weights=pesos))
            impulsos_arr = np.array(impulsos)
            validos_imp = ~np.isnan(impulsos_arr)
            if validos_imp.any():
                impulso_medio_zona[k] = float(np.average(impulsos_arr[validos_imp], weights=pesos[validos_imp]))

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
                'origem_k': k, 'idx_criacao': reteste_idx,
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
            pesos.append(peso)
            rejeicoes.append(rej)
            agressoes.append(agr)
            impulsos.append(imp)

        z['toques'] = len(cand)
        z['membros_conf'] = cand
        if pesos:
            pesos_arr = np.array(pesos)
            z['forca_toques'] = float(pesos_arr.sum())
            z['rejeicao_media'] = float(np.average(rejeicoes, weights=pesos_arr))
            z['agressao_media'] = float(np.average(agressoes, weights=pesos_arr))
            impulsos_arr = np.array(impulsos)
            validos_imp = ~np.isnan(impulsos_arr)
            z['impulso_medio'] = (float(np.average(impulsos_arr[validos_imp], weights=pesos_arr[validos_imp]))
                                   if validos_imp.any() else np.nan)
        else:
            z['forca_toques'] = 0.0
            z['rejeicao_media'] = np.nan
            z['agressao_media'] = np.nan
            z['impulso_medio'] = np.nan

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
    sr_abaixo_1..N). Combina nivel_forte, força de toques com decaimento,
    rejeição média da zona, distância em ATR, tendência/fase/saúde, status
    de bipolaridade, fluxo/delta recente + agressão média da zona, impulso
    médio da zona, e sinais simplificados de Big Players (absorção/volume
    anômalo, recalculados aqui de forma vetorizada — sem duplicar a lógica
    de UI de construir_big_players em dashboard.py).

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

            score = np.full(n, 50.0)
            valido = ~np.isnan(dist_atr)

            score += np.where(forte, cfg.PESO_NIVEL_FORTE, cfg.PESO_NIVEL_FRACO_PENALIDADE)

            # Força de toques com decaimento (não a contagem bruta) — satura em TOQUES_TETO_SCORE
            forca_p_score = np.where(np.isnan(forca_toques), np.minimum(toques, cfg.TOQUES_TETO_SCORE),
                                      np.minimum(forca_toques, cfg.TOQUES_TETO_SCORE))
            score += forca_p_score * cfg.PESO_TOQUE_UNIDADE

            # Rejeição média da zona reforça bounce
            score += np.nan_to_num(rejeicao_media, nan=0.0) * 10

            # Atenuação p/ nível muito distante
            longe = dist_atr > cfg.DIST_ATR_LONGE
            score = np.where(longe, 50 + (score - 50) * cfg.ATENUACAO_NIVEL_DISTANTE, score)

            # Tendência/fase/saúde (direcional)
            alta = tendencia == 'alta'
            baixa = tendencia == 'baixa'
            lateral = ~alta & ~baixa
            fase_inicio_meio = np.isin(fase, ['inicio', 'meio'])
            saude_forte = saude == 'forte'
            if eh_resistencia:
                score += np.where(alta & fase_inicio_meio, -cfg.PESO_TENDENCIA_FASE, 0)
                score += np.where(baixa & saude_forte, cfg.PESO_TENDENCIA_FASE, np.where(baixa, cfg.PESO_TENDENCIA_FASE / 2, 0))
            else:
                score += np.where(alta & saude_forte, cfg.PESO_TENDENCIA_FASE, np.where(alta, cfg.PESO_TENDENCIA_FASE / 2, 0))
                score += np.where(baixa & fase_inicio_meio, -cfg.PESO_TENDENCIA_FASE, 0)
            score += np.where(lateral, cfg.PESO_LATERAL_BOUNCE, 0)

            # Status de bipolaridade
            aguardando = np.isin(status, ['aguardando_pullback', 'aguardando_throwback'])
            rompida_sem_espelho = (status == 'rompida') & np.isnan(zona_espelho_id)
            score += np.where(aguardando, cfg.PESO_STATUS_AGUARDANDO_RETESTE, 0)
            score += np.where(rompida_sem_espelho, cfg.PESO_STATUS_ROMPIDA_SEM_ESPELHO, 0)

            # forca_herdada (zona espelho) — decaimento exponencial no tempo, nunca soma direta
            tem_herdada = ~np.isnan(forca_herdada)
            if tem_herdada.any():
                idade_espelho = np.where(tem_herdada, idade, np.nan)
                tau = max(cfg.IDADE_MINIMA_NIVEL_FORTE, 1)
                decaimento = np.exp(-np.nan_to_num(idade_espelho, nan=0.0) / tau)
                score += np.where(tem_herdada, forca_herdada * decaimento * 0.5, 0)

            # Fluxo/delta recente (só se disponível)
            delta_favor_rompimento = ((eh_resistencia) & (delta_acum > 0)) | ((~eh_resistencia) & (delta_acum < 0))
            tem_delta = ~np.isnan(delta_acum)
            score += np.where(tem_delta & delta_favor_rompimento, -cfg.PESO_FLUXO_DELTA, 0)
            score += np.where(tem_delta & ~delta_favor_rompimento, cfg.PESO_FLUXO_DELTA, 0)

            razao_favor_rompimento = ((eh_resistencia) & (razao_agr > 0.65)) | ((~eh_resistencia) & (razao_agr < 0.35))
            razao_favor_bounce = ((eh_resistencia) & (razao_agr < 0.35)) | ((~eh_resistencia) & (razao_agr > 0.65))
            score += np.where(razao_favor_rompimento, -cfg.PESO_FLUXO_RAZAO, 0)
            score += np.where(razao_favor_bounce, cfg.PESO_FLUXO_RAZAO, 0)

            # Agressão média histórica da zona (complementar ao fluxo recente)
            score += np.nan_to_num(agressao_media, nan=0.0) * cfg.PESO_FLUXO_DELTA

            # Impulso médio da zona — reforça a expectativa de reação também agora
            score += np.nan_to_num(np.clip(impulso_medio, 0, 2), nan=0.0) * 3

            # Big Players — bônus pequeno, nunca decide sozinho
            absorcao_favor_bounce = (eh_resistencia & absorcao_venda) | (~eh_resistencia & absorcao_compra)
            score += np.where(absorcao_favor_bounce, cfg.PESO_BIG_PLAYERS_ABSORCAO, 0)
            score += np.where(volume_anomalo, cfg.PESO_BIG_PLAYERS_VOLUME, 0)

            # Rompimento intradiário (sinal ao vivo, escopo do próprio pregão —
            # ver comentário em calcular_zonas_sr sobre por que isso é
            # diferente do status estrutural acima). "Rompeu hoje" ainda sem
            # reteste = momentum de rompimento em curso, penaliza bounce.
            # "Pullback confirmado hoje" = reteste já validado no próprio
            # pregão, o sinal mais forte e mais recente que temos — reforça
            # bounce (o nível, no papel atual, já provou que segura).
            rompimento_hoje = df.get(f'{pref}_rompimento_hoje', pd.Series(['nenhum'] * n)).values
            score += np.where(rompimento_hoje == 'rompeu_hoje', -cfg.PESO_ROMPIMENTO_INTRADAY, 0)
            score += np.where(rompimento_hoje == 'pullback_confirmado_hoje', cfg.PESO_ROMPIMENTO_INTRADAY_PULLBACK, 0)

            score = np.clip(score, cfg.SCORE_PROB_MIN, cfg.SCORE_PROB_MAX)
            df[f'{pref}_prob_bounce'] = np.where(valido, np.round(score, 1), np.nan)

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

    for nome, func in etapas:
        if verbose:
            print(f"  → {nome}...")
        df = func(df)

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

