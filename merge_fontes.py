# =============================================================================
# merge_fontes.py — Fusão CSV + MT5 numa série única rastreável
# =============================================================================
# Dois modos de operação, conforme decisão de 30/06/2026:
#
#   setup_inicial(ativo)      — roda uma vez (ou sob comando explícito).
#                                Lê CSV completo + MT5 complementar, calcula
#                                delta onde há tick, salva tudo em Parquet.
#                                Pesado, não deve rodar automaticamente.
#
#   atualizar_incremental(ativo) — roda no dia a dia. Lê o Parquet já salvo,
#                                busca SÓ o que é novo desde a última
#                                atualização (candles + ticks recentes),
#                                concatena, salva de novo. Leve e rápido.
#
# Nenhum dos dois varre os 2 anos inteiros de tick do zero rotineiramente —
# isso só acontece no setup_inicial, uma vez.
# =============================================================================

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from tqdm import tqdm

import config_backtest as cfg
import csv_loader
import mt5_loader
import persistencia


# -----------------------------------------------------------------------------
# CÁLCULO DE DELTA (comprador - vendedor) POR CANDLE, A PARTIR DE TICKS
# -----------------------------------------------------------------------------

def _calcular_delta_por_candle(candles: pd.DataFrame, ticks: pd.DataFrame,
                                prefixo: str = "", checkpoint_a_cada: int = 5000) -> pd.DataFrame:
    """
    Para cada candle, soma o volume de ticks compradores e vendedores que
    caíram dentro daquele intervalo de 5 minutos. Marca fluxo_disponivel
    explicitamente — candles sem tick correspondente ficam com
    fluxo_disponivel=False, nunca com delta=0 (que seria informação falsa:
    zero é um resultado real, ausência de dado é outra coisa).

    OTIMIZAÇÃO (30/06/2026): a versão anterior filtrava o DataFrame de
    ticks inteiro a cada candle (O(n*m) — para 56 mil candles x 3 milhões
    de ticks, isso é lento). Agora usa np.searchsorted sobre o array de
    timestamps já ordenado, que localiza o intervalo de cada candle em
    tempo logarítmico — muito mais rápido em volume grande.

    PROGRESSO E CHECKPOINT (30/06/2026, pedido por Jesué): mostra barra de
    progresso via tqdm, e salva um checkpoint parcial a cada N candles
    processados (checkpoint_a_cada) em disco, para nunca perder o trabalho
    pesado se o processo travar ou for interrompido no meio.
    """
    candles = candles.copy().sort_values('datetime').reset_index(drop=True)
    candles['vol_comprador'] = pd.NA
    candles['vol_vendedor'] = pd.NA
    candles['delta'] = pd.NA
    candles['fluxo_disponivel'] = False

    if ticks is None or ticks.empty:
        return candles

    ticks = ticks.sort_values('datetime').reset_index(drop=True)
    tick_times = ticks['datetime'].values
    tick_volumes = ticks['volume_ticks'].astype('int64').values  # já convertido p/ evitar overflow
    tick_agressor = ticks['agressor'].values

    candle_times = candles['datetime'].values
    # Limite superior de cada candle: início do próximo, ou +5min no último
    candle_fins = np.append(candle_times[1:], candle_times[-1] + np.timedelta64(5, 'm'))

    descricao = f"Calculando delta [{prefixo}]" if prefixo else "Calculando delta"

    for i in tqdm(range(len(candles)), desc=descricao, unit="candle"):
        inicio_idx = np.searchsorted(tick_times, candle_times[i], side='left')
        fim_idx = np.searchsorted(tick_times, candle_fins[i], side='left')

        if fim_idx <= inicio_idx:
            continue  # nenhum tick nesse intervalo, mantém fluxo_disponivel=False

        agressores_candle = tick_agressor[inicio_idx:fim_idx]
        volumes_candle = tick_volumes[inicio_idx:fim_idx]

        vol_c = int(volumes_candle[agressores_candle == 'comprador'].sum())
        vol_v = int(volumes_candle[agressores_candle == 'vendedor'].sum())

        candles.loc[i, 'vol_comprador'] = vol_c
        candles.loc[i, 'vol_vendedor'] = vol_v
        candles.loc[i, 'delta'] = vol_c - vol_v
        candles.loc[i, 'fluxo_disponivel'] = True

        # Checkpoint incremental — salva progresso parcial em disco para
        # nunca perder trabalho pesado se o processo for interrompido
        if prefixo and (i + 1) % checkpoint_a_cada == 0:
            persistencia.salvar_checkpoint(prefixo, candles.iloc[:i + 1])

    return candles


# -----------------------------------------------------------------------------
# SETUP INICIAL (pesado — roda uma vez, ou sob comando explícito)
# -----------------------------------------------------------------------------

def setup_inicial(prefixo: str, buscar_tick_historico: bool = True) -> pd.DataFrame:
    """
    Monta a base completa do zero: CSV (2 anos de candle) + MT5 (candle
    recente que o CSV não cobre + tick onde disponível para delta).

    PESADO — só deve ser chamado explicitamente, nunca em loop automático.
    Ao final, salva em Parquet via persistencia.py.

    buscar_tick_historico=False permite montar só a parte de candle,
    pulando a etapa mais demorada (varrer tick de 2 anos) — útil para
    testar rápido ou se só precisar de candle/tendência/S&R por enquanto.
    """
    print(f"\n{'=' * 80}")
    print(f"  SETUP INICIAL — {prefixo}")
    print(f"{'=' * 80}")

    mt5_loader.conectar()
    mt5_loader.exigir_conexao_saudavel()

    # --- 1. Carrega CSV (fonte primária de candle, 2 anos)
    df_csv = csv_loader.carregar_todos_csvs(prefixo)
    if df_csv.empty:
        raise RuntimeError(f"Nenhum CSV encontrado para {prefixo}. Verifique {cfg.PASTA_CSV}")

    df_csv['fonte'] = 'csv'
    ultima_data_csv = df_csv['datetime'].max()
    print(f"[merge] {prefixo}: CSV cobre até {ultima_data_csv}")

    # --- 2. Complementa com MT5 — candles posteriores ao fim do CSV
    ticker_atual = mt5_loader.detectar_contrato_mais_recente(prefixo)
    if ticker_atual is None:
        raise RuntimeError(f"Não foi possível detectar contrato vigente para {prefixo}")

    df_mt5_recente = mt5_loader.buscar_candles_apos(ticker_atual, ultima_data_csv)

    if df_mt5_recente is not None and not df_mt5_recente.empty:
        print(f"[merge] {prefixo}: MT5 complementa com {len(df_mt5_recente)} candles "
              f"após {ultima_data_csv}")
        df_mt5_recente = df_mt5_recente.rename(columns={'volume_ticks': 'volume_financeiro'})
        colunas_comuns = ['datetime', 'open', 'high', 'low', 'close', 'fonte']
        df_completo = pd.concat([
            df_csv[colunas_comuns],
            df_mt5_recente[colunas_comuns]
        ], ignore_index=True)
    else:
        print(f"[merge] {prefixo}: nada novo no MT5 além do CSV")
        df_completo = df_csv[['datetime', 'open', 'high', 'low', 'close', 'fonte']].copy()

    df_completo = df_completo.drop_duplicates(subset='datetime', keep='last')
    df_completo = df_completo.sort_values('datetime').reset_index(drop=True)

    print(f"[merge] {prefixo}: série consolidada de candles = {len(df_completo)} linhas "
          f"({df_completo['datetime'].min()} até {df_completo['datetime'].max()})")

    # --- 3. Calcula delta onde houver tick disponível no MT5
    if buscar_tick_historico:
        print(f"[merge] {prefixo}: buscando ticks para cálculo de delta "
              f"(pode levar alguns minutos)...")

        inicio_busca = df_completo['datetime'].min()
        fim_busca = df_completo['datetime'].max()

        ticks = mt5_loader.buscar_ticks_periodo(ticker_atual, inicio_busca, fim_busca)

        if ticks is not None and not ticks.empty:
            print(f"[merge] {prefixo}: {len(ticks)} ticks obtidos para cálculo de delta")
            df_completo = _calcular_delta_por_candle(df_completo, ticks, prefixo=prefixo)
        else:
            print(f"[merge] {prefixo}: nenhum tick disponível no período — "
                  f"todos os candles ficam com fluxo_disponivel=False")
            df_completo['fluxo_disponivel'] = False
    else:
        print(f"[merge] {prefixo}: pulando busca de tick (buscar_tick_historico=False)")
        df_completo['delta'] = pd.NA
        df_completo['vol_comprador'] = pd.NA
        df_completo['vol_vendedor'] = pd.NA
        df_completo['fluxo_disponivel'] = False

    # --- 4. Salva
    persistencia.salvar_candles(prefixo, df_completo)
    persistencia.limpar_checkpoint(prefixo)  # processamento concluído, checkpoint não é mais necessário
    persistencia.salvar_metadata(prefixo, {
        'ultima_atualizacao': datetime.now().isoformat(),
        'ultima_data_coberta': str(df_completo['datetime'].max()),
        'contrato_usado': ticker_atual,
        'total_candles': len(df_completo),
        'candles_com_fluxo': int(df_completo['fluxo_disponivel'].sum())
    })

    print(f"[merge] {prefixo}: setup concluído. Lembre de chamar "
          f"mt5_loader.desconectar() ao final da sessão se não for usar mais o MT5.")

    return df_completo


# -----------------------------------------------------------------------------
# ATUALIZAÇÃO INCREMENTAL (leve — roda no dia a dia)
# -----------------------------------------------------------------------------

def atualizar_incremental(prefixo: str) -> pd.DataFrame:
    """
    Atualização leve para uso diário: carrega o que já está salvo, busca
    SÓ o incremento (candles e ticks desde a última atualização), concatena
    e salva de novo. Nunca varre o histórico completo.

    Se ainda não existir base salva, orienta a rodar setup_inicial() antes.
    """
    if not persistencia.base_existe(prefixo):
        raise RuntimeError(
            f"Nenhuma base salva para {prefixo}. Rode setup_inicial('{prefixo}') primeiro."
        )

    mt5_loader.conectar()
    mt5_loader.exigir_conexao_saudavel()

    df_existente = persistencia.carregar_candles(prefixo)
    metadata = persistencia.carregar_metadata(prefixo)

    ultima_data = pd.to_datetime(metadata['ultima_data_coberta'])
    print(f"[merge] {prefixo}: atualização incremental desde {ultima_data}")

    ticker_atual = mt5_loader.detectar_contrato_mais_recente(prefixo)
    if ticker_atual is None:
        raise RuntimeError(f"Não foi possível detectar contrato vigente para {prefixo}")

    # --- Candles novos
    df_novo = mt5_loader.buscar_candles_apos(ticker_atual, ultima_data.to_pydatetime())

    if df_novo is None or df_novo.empty:
        print(f"[merge] {prefixo}: nenhum candle novo desde a última atualização")
        return df_existente

    df_novo = df_novo.rename(columns={'volume_ticks': 'volume_financeiro'})
    df_novo['vol_comprador'] = pd.NA
    df_novo['vol_vendedor'] = pd.NA
    df_novo['delta'] = pd.NA
    df_novo['fluxo_disponivel'] = False

    # --- Ticks para o período novo (incremento pequeno, rápido)
    inicio_ticks = ultima_data.to_pydatetime()
    fim_ticks = df_novo['datetime'].max().to_pydatetime()
    ticks_novos = mt5_loader.buscar_ticks_periodo(ticker_atual, inicio_ticks, fim_ticks)

    if ticks_novos is not None and not ticks_novos.empty:
        df_novo = _calcular_delta_por_candle(df_novo, ticks_novos)

    # --- Concatena com o existente, sem duplicar
    colunas = df_existente.columns
    df_novo = df_novo.reindex(columns=colunas, fill_value=pd.NA)

    df_atualizado = pd.concat([df_existente, df_novo], ignore_index=True)
    df_atualizado = df_atualizado.drop_duplicates(subset='datetime', keep='last')
    df_atualizado = df_atualizado.sort_values('datetime').reset_index(drop=True)

    print(f"[merge] {prefixo}: {len(df_novo)} candles novos adicionados "
          f"(total agora: {len(df_atualizado)})")

    persistencia.salvar_candles(prefixo, df_atualizado)
    persistencia.salvar_metadata(prefixo, {
        'ultima_atualizacao': datetime.now().isoformat(),
        'ultima_data_coberta': str(df_atualizado['datetime'].max()),
        'contrato_usado': ticker_atual,
        'total_candles': len(df_atualizado),
        'candles_com_fluxo': int(df_atualizado['fluxo_disponivel'].sum())
    })

    return df_atualizado


# -----------------------------------------------------------------------------
# TESTE STANDALONE
# -----------------------------------------------------------------------------

if __name__ == '__main__':
    print("=" * 90)
    print("  TESTE STANDALONE — merge_fontes.py")
    print("=" * 90)
    print("\nEste módulo não deve rodar setup_inicial() automaticamente aqui —")
    print("é uma operação pesada e deve ser chamada explicitamente.")
    print("Para testar, importe o módulo e chame manualmente:")
    print("  import merge_fontes")
    print("  merge_fontes.setup_inicial('WIN', buscar_tick_historico=False)  # rápido, só candle")
    print("  merge_fontes.setup_inicial('WIN')  # completo, com tick (mais lento)")
