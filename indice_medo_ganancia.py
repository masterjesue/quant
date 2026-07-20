# =============================================================================
# indice_medo_ganancia.py — Índice de Medo e Ganância do mercado (adaptado)
# =============================================================================
# Inspirado no Fear & Greed Index da CNN (7 indicadores, mercado americano:
# momentum vs média de 125 dias, força/amplitude de preço via milhares de
# ações, put/call ratio, VIX vs média de 50 dias, safe-haven Treasury vs
# ações, spread de junk bonds) — mas ADAPTADO e REDUZIDO pra 3 fatores, os
# únicos construíveis só com o que este projeto já tem (WIN/WDO via MT5, ver
# `config_backtest.ATIVOS`). Os outros 4 indicadores originais exigiriam
# fonte de dado nova (assinatura de market data B3, feed de opções) — fora
# de escopo (Fase F do plano em
# C:\Users\jesue\.claude\plans\t-tulo-super-quant-eager-knuth.md).
#
# "Primeiro palpite, não calibrado" — mesmo espírito da Escala Hawkins e da
# Aderência S/R do Diário: nunca apresentar como certeza, é uma leitura
# aproximada de tendência, não um indicador oficial equivalente ao da CNN.
# =============================================================================

import numpy as np
import pandas as pd

import diario_trade

JANELA_MOMENTUM_DIAS = 21       # ~1 mês de pregões (proxy do "125-day" da CNN, reduzido pro tamanho da amostra)
JANELA_VOLATILIDADE_DIAS = 50   # mesma janela conceitual do "VIX vs média de 50 dias"
JANELA_SEGURANCA_DIAS = 20      # retorno relativo WDO x WIN

_Z_CLIP = 2.0  # clip do z-score antes de mapear pra 0-100


def _fechamentos_diarios(df: pd.DataFrame) -> pd.Series:
    """Resample de candles M5 pra fechamento diário — não existe hoje
    nenhuma agregação diária de candle bruto no projeto (só de trades, via
    `data_op` no Diário)."""
    return df.set_index('datetime')['close'].resample('D').last().dropna()


def _atr_diario(df: pd.DataFrame) -> pd.Series:
    """Média diária da coluna `atr` (já calculada por candle em
    `features.py::calcular_atr`) — reaproveita o dado existente, não
    recalcula do zero."""
    return df.set_index('datetime')['atr'].resample('D').mean().dropna()


def _zscore_para_0_100(valor_atual: float, serie_historica: pd.Series, inverter: bool = False) -> float | None:
    """
    (valor_atual - média) / desvio padrão da série histórica, clipado em
    ±`_Z_CLIP` e mapeado linear pra 0-100 — mesma lógica declarada da CNN:
    "mede o quanto cada indicador se desvia da própria média, comparado ao
    quanto ele costuma desviar". `inverter=True` pra indicadores onde
    "acima da média" é sinal de MEDO, não de ganância (ex. volatilidade).
    `None` se não houver histórico suficiente (desvio padrão zero/NaN) —
    nunca inventa um score sem base estatística.
    """
    if serie_historica is None or len(serie_historica) < 5:
        return None
    media = serie_historica.mean()
    desvio = serie_historica.std()
    if not np.isfinite(desvio) or desvio == 0:
        return None
    z = (valor_atual - media) / desvio
    if inverter:
        z = -z
    z = max(-_Z_CLIP, min(_Z_CLIP, z))
    return (z + _Z_CLIP) / (2 * _Z_CLIP) * 100


def calcular_indice_medo_ganancia(df_win: pd.DataFrame | None, df_wdo: pd.DataFrame | None) -> dict | None:
    """
    Índice de Medo e Ganância ADAPTADO (3 fatores, não os 7 da CNN — ver
    cabeçalho do módulo). Cada sub-score é 0-100 via z-score contra a
    própria janela histórica; o composto é a média simples dos sub-scores
    disponíveis (peso igual, mesmo princípio da CNN). Retorna `None` se não
    houver candle suficiente pra nenhum sub-score (sem dado, sem índice —
    nunca inventar valor).

    Retorna dict com: sub-scores disponíveis (`momentum`, `volatilidade`,
    `seguranca`, cada um 0-100), `valor` (composto 0-100), `nivel_nome` e
    `cor` (via `diario_trade.classificar_sentimento_mercado` — mesma
    linguagem de faixas do slider por-trade da Fase D).
    """
    sub_scores = {}

    tem_win = df_win is not None and not df_win.empty and 'atr' in df_win.columns
    tem_wdo = df_wdo is not None and not df_wdo.empty

    # 1. Momentum: fechamento diário do WIN vs sua própria média móvel de N dias.
    if tem_win:
        fechamentos = _fechamentos_diarios(df_win)
        if len(fechamentos) > JANELA_MOMENTUM_DIAS:
            media_movel = fechamentos.rolling(JANELA_MOMENTUM_DIAS).mean()
            distancia = (fechamentos - media_movel).dropna()
            if len(distancia) > 1:
                score = _zscore_para_0_100(distancia.iloc[-1], distancia.iloc[:-1])
                if score is not None:
                    sub_scores['momentum'] = round(score, 1)

    # 2. Volatilidade: ATR diário do WIN vs sua própria média histórica
    #    (invertido: ATR ACIMA da média = mais medo, ao contrário dos outros
    #    2 sub-scores onde "acima da média" é ganância).
    if tem_win:
        atr_diario = _atr_diario(df_win)
        if len(atr_diario) > JANELA_VOLATILIDADE_DIAS:
            score = _zscore_para_0_100(atr_diario.iloc[-1], atr_diario.iloc[:-1], inverter=True)
            if score is not None:
                sub_scores['volatilidade'] = round(score, 1)

    # 3. Segurança/fuga pro dólar: retorno relativo WDO x WIN numa janela de
    #    N dias — dólar subindo enquanto ações caem é o comportamento
    #    clássico de fuga pra segurança no Brasil (adaptação; não existe
    #    indicador oficial BR equivalente ao "Treasury vs ações" da CNN).
    #    Invertido: WDO superando WIN = medo.
    if tem_win and tem_wdo:
        fech_win = _fechamentos_diarios(df_win)
        fech_wdo = _fechamentos_diarios(df_wdo)
        ret_win = fech_win.pct_change(JANELA_SEGURANCA_DIAS)
        ret_wdo = fech_wdo.pct_change(JANELA_SEGURANCA_DIAS)
        diferenca = (ret_wdo - ret_win).dropna()
        if len(diferenca) > JANELA_SEGURANCA_DIAS:
            score = _zscore_para_0_100(diferenca.iloc[-1], diferenca.iloc[:-1], inverter=True)
            if score is not None:
                sub_scores['seguranca'] = round(score, 1)

    if not sub_scores:
        return None

    composto = sum(sub_scores.values()) / len(sub_scores)
    classificacao = diario_trade.classificar_sentimento_mercado(composto)
    nome, cor = classificacao if classificacao is not None else ('Neutro', 'text2')
    return {**sub_scores, 'valor': round(composto, 1), 'nivel_nome': nome, 'cor': cor}
