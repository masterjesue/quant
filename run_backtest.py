# =============================================================================
# run_backtest.py — Ponto de entrada único do motor de backtest
# =============================================================================
# Orquestra: setup_inicial (pesado, uma vez) ou atualizar_incremental (leve,
# diário), seguido do cálculo de features sobre a base consolidada.
#
# Uso típico:
#
#   1ª vez / reset:
#       python run_backtest.py --setup
#       python run_backtest.py --setup --sem-tick   ← só candle, mais rápido
#
#   Dia a dia (antes de abrir o dashboard):
#       python run_backtest.py --atualizar
#
#   Só recalcular features (sem buscar dados novos):
#       python run_backtest.py --features
#
#   Backfill de tick histórico (protótipo de backtesting tick a tick, sob
#   demanda, nunca automático):
#       python run_backtest.py --backfill-ticks
#       python run_backtest.py --backfill-ticks --dias 30
#       python run_backtest.py --backfill-ticks --de 2026-02-01 --ate 2026-08-01
# =============================================================================

import argparse
import time
import sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

import config_backtest as cfg
import merge_fontes
import features as feat
import persistencia
import mt5_loader
import diario_trade
import ctrader_openapi_loader


def rodar_setup(buscar_tick: bool = True):
    print("=" * 80)
    print("  SETUP INICIAL — processamento completo da base de dados")
    print(f"  Ativos: {cfg.ATIVOS}")
    print(f"  Buscar tick histórico: {buscar_tick}")
    print("=" * 80)

    for ativo in cfg.ATIVOS:
        t0 = time.time()
        df = merge_fontes.setup_inicial(ativo, buscar_tick_historico=buscar_tick)
        t1 = time.time()
        print(f"\n[run] {ativo}: base consolidada em {t1-t0:.1f}s")

        print(f"[run] {ativo}: calculando features...")
        t2 = time.time()
        df_feat = feat.calcular_todos_features(df, ativo=ativo)
        t3 = time.time()
        print(f"[run] {ativo}: features calculadas em {t3-t2:.1f}s")

        # Salva base com features incorporadas
        persistencia.salvar_candles(ativo, df_feat)
        print(f"[run] {ativo}: base com features salva.")

    mt5_loader.desconectar()
    print("\n" + "=" * 80)
    print("  SETUP CONCLUÍDO — base pronta para uso no dashboard.")
    print("=" * 80)


def rodar_atualizacao():
    print("=" * 80)
    print("  ATUALIZAÇÃO INCREMENTAL — buscando apenas dados novos")
    print("=" * 80)

    for ativo in cfg.ATIVOS:
        if not persistencia.base_existe(ativo):
            print(f"[run] {ativo}: base não encontrada. Rode --setup primeiro.")
            continue

        t0 = time.time()
        df = merge_fontes.atualizar_incremental(ativo)
        t1 = time.time()
        print(f"[run] {ativo}: atualização em {t1-t0:.1f}s")

        print(f"[run] {ativo}: recalculando features...")
        t2 = time.time()
        df_feat = feat.calcular_todos_features(df, ativo=ativo, verbose=False)
        t3 = time.time()
        print(f"[run] {ativo}: features em {t3-t2:.1f}s")

        persistencia.salvar_candles(ativo, df_feat)
        print(f"[run] {ativo}: base atualizada e salva.")

    mt5_loader.desconectar()
    print("\n" + "=" * 80)
    print("  ATUALIZAÇÃO CONCLUÍDA.")
    print("=" * 80)


def rodar_features():
    """Recalcula features sem buscar dados novos — útil após ajuste do features.py."""
    print("=" * 80)
    print("  RECALCULAR FEATURES — sem buscar dados novos")
    print("=" * 80)

    for ativo in cfg.ATIVOS:
        if not persistencia.base_existe(ativo):
            print(f"[run] {ativo}: base não encontrada. Rode --setup primeiro.")
            continue

        df = persistencia.carregar_candles(ativo)
        print(f"[run] {ativo}: recalculando features sobre {len(df)} candles...")

        t0 = time.time()
        df_feat = feat.calcular_todos_features(df, ativo=ativo)
        t1 = time.time()
        print(f"[run] {ativo}: features em {t1-t0:.1f}s")

        persistencia.salvar_candles(ativo, df_feat)
        print(f"[run] {ativo}: base com features salva.")

    print("\n" + "=" * 80)
    print("  FEATURES RECALCULADAS.")
    print("=" * 80)


# Margem em torno do pregão (abertura ~09:00, fechamento ~17:55/18:00 no B3)
# — folga pra cobrir leilão de abertura/fechamento sem precisar acertar o
# minuto exato por ativo/dia. Só usada aqui (backfill de tick); não é um
# valor calibrável do motor ao vivo, não pertence a config_backtest.py.
_HORA_INICIO_BACKFILL = dt.time(8, 45)
_HORA_FIM_BACKFILL = dt.time(18, 15)


def _combos_ativo_contrato_dia(dias: int | None, de, ate) -> pd.DataFrame:
    """
    Só os dias/contratos elegíveis a backfill de tick: trades que têm um
    ticket MT5 real vinculado (`tickets_finalizados.parquet`) — trades
    antigos/importados sem esse vínculo não têm como ser alinhados a tick
    e ficam de fora (ver spec_prototipo_backtesting_tick.md, "cobertura
    desigual da base"). `contrato` já vem gravado por trade em
    `COLUNAS_DIARIO` — não precisa redetectar o ticker vigente.
    """
    trades = diario_trade.listar_trades()
    if trades.empty:
        return trades[['ativo', 'contrato', 'data_op']]

    tickets_df = persistencia.carregar_tickets_finalizados()
    ids_com_ticket = (set(tickets_df['id_trade'].unique())
                       if tickets_df is not None and not tickets_df.empty else set())
    trades = trades[trades['id_trade'].isin(ids_com_ticket)]
    trades = trades.dropna(subset=['ativo', 'contrato', 'data_op'])

    if de is not None:
        trades = trades[trades['data_op'] >= de]
    if ate is not None:
        trades = trades[trades['data_op'] <= ate]
    if dias is not None and de is None and ate is None and not trades.empty:
        limite = trades['data_op'].max() - dt.timedelta(days=dias)
        trades = trades[trades['data_op'] >= limite]

    return trades[['ativo', 'contrato', 'data_op']].drop_duplicates().sort_values('data_op')


def rodar_backfill_ticks(dias: int | None = None, de=None, ate=None):
    """
    Backfill de tick histórico, SEMPRE decoupled do ciclo de 30s do
    dashboard e do `--atualizar` diário — roda sob demanda, escopado aos
    dias em que houve trade real (não os 6 meses inteiros de candle).
    Particionado por dia: um dia já salvo nunca é buscado de novo.
    """
    print("=" * 80)
    print("  BACKFILL DE TICKS — dias com trade real")
    print("=" * 80)

    combos = _combos_ativo_contrato_dia(dias, de, ate)
    if combos.empty:
        print("[backfill-ticks] Nenhum dia elegível encontrado (sem trades com ticket MT5 vinculado).")
        return

    print(f"[backfill-ticks] {len(combos)} combinação(ões) ativo/contrato/dia candidatas.")

    mt5_loader.conectar()
    salvos = pulados = vazios = 0
    for row in combos.itertuples(index=False):
        ativo, contrato, dia = row.ativo, row.contrato, row.data_op
        if persistencia.ticks_dia_existe(ativo, dia):
            pulados += 1
            continue

        inicio = dt.datetime.combine(dia, _HORA_INICIO_BACKFILL)
        fim = dt.datetime.combine(dia, _HORA_FIM_BACKFILL)
        print(f"[backfill-ticks] {ativo} {contrato} {dia}: buscando ticks...")
        ticks = mt5_loader.buscar_ticks_periodo(contrato, inicio, fim)

        if ticks is None or ticks.empty:
            print(f"[backfill-ticks] {ativo} {contrato} {dia}: nenhum tick retornado.")
            vazios += 1
            continue

        persistencia.salvar_ticks_dia(ativo, dia, ticks)
        salvos += 1

    mt5_loader.desconectar()
    print("\n" + "=" * 80)
    print(f"  BACKFILL CONCLUÍDO — {salvos} dia(s) salvo(s), {pulados} já existia(m), {vazios} sem tick.")
    print("=" * 80)


# Sem CSV/MT5 pros ativos cTrader — a Open API é fonte única, sem o merge
# dual-fonte que `merge_fontes.py` faz pra WIN/WDO. Lookback padrão de 1 ano
# quando `--dias` não é passado (não há "primeira base" prévia pra herdar).
_LOOKBACK_PADRAO_CTRADER_DIAS = 365


def rodar_setup_ctrader(dias: int | None = None):
    print("=" * 80)
    print("  SETUP cTrader — histórico via Open API")
    print(f"  Ativos: {cfg.ATIVOS_CTRADER}")
    print("=" * 80)

    ate = dt.datetime.utcnow()
    desde = ate - dt.timedelta(days=dias or _LOOKBACK_PADRAO_CTRADER_DIAS)
    pares = [(ativo, ativo) for ativo in cfg.ATIVOS_CTRADER]

    t0 = time.time()
    resultado = ctrader_openapi_loader.buscar_historico(pares, desde, ate)
    print(f"[run] cTrader: histórico baixado em {time.time()-t0:.1f}s")

    for ativo, df in resultado.items():
        if df.empty:
            print(f"[run] {ativo}: nenhum candle retornado.")
            continue
        print(f"[run] {ativo}: {len(df)} candles, calculando features...")
        t1 = time.time()
        df_feat = feat.calcular_todos_features(df, ativo=ativo)
        print(f"[run] {ativo}: features calculadas em {time.time()-t1:.1f}s")
        persistencia.salvar_candles(ativo, df_feat)
        print(f"[run] {ativo}: base com features salva.")

    print("\n" + "=" * 80)
    print("  SETUP cTrader CONCLUÍDO.")
    print("=" * 80)


def rodar_atualizacao_ctrader():
    print("=" * 80)
    print("  ATUALIZAÇÃO cTrader — buscando apenas dados novos")
    print("=" * 80)

    colunas_base = ['datetime', 'open', 'high', 'low', 'close', 'fonte']
    ultimo_candle = {}
    pares = []
    for ativo in cfg.ATIVOS_CTRADER:
        if not persistencia.base_existe(ativo):
            print(f"[run] {ativo}: base não encontrada. Rode --setup-ctrader primeiro.")
            continue
        df_atual = persistencia.carregar_candles(ativo)
        ultimo_candle[ativo] = df_atual['datetime'].max()
        pares.append((ativo, ativo))

    if not pares:
        return

    desde = min(ultimo_candle.values())
    ate = dt.datetime.utcnow()
    resultado = ctrader_openapi_loader.buscar_historico(pares, desde, ate)

    for ativo, df_novo in resultado.items():
        df_novo = df_novo[df_novo['datetime'] > ultimo_candle[ativo]]
        if df_novo.empty:
            print(f"[run] {ativo}: sem candle novo.")
            continue

        df_existente = persistencia.carregar_candles(ativo)
        df_bruto = pd.concat([df_existente[colunas_base], df_novo[colunas_base]], ignore_index=True)
        df_bruto = df_bruto.drop_duplicates(subset='datetime').sort_values('datetime').reset_index(drop=True)

        print(f"[run] {ativo}: {len(df_novo)} candle(s) novo(s), recalculando features...")
        df_feat = feat.calcular_todos_features(df_bruto, ativo=ativo, verbose=False)
        persistencia.salvar_candles(ativo, df_feat)
        print(f"[run] {ativo}: base atualizada e salva.")

    print("\n" + "=" * 80)
    print("  ATUALIZAÇÃO cTrader CONCLUÍDA.")
    print("=" * 80)


def carregar_base_para_dashboard() -> dict:
    """
    Chamada pelo dashboard ao iniciar — carrega os DataFrames de ambos os
    ativos já com features calculadas, prontos para consumo do motor ao vivo.
    Retorna dict: {'WIN': df_win, 'WDO': df_wdo}
    """
    bases = {}
    for ativo in cfg.ATIVOS:
        df = persistencia.carregar_candles(ativo)
        if df is not None:
            bases[ativo] = df
        else:
            print(f"[run] ⚠️ Base de {ativo} não encontrada. Rode run_backtest.py --setup.")
    return bases


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Motor de backtest — orquestrador')
    grupo  = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument('--setup',      action='store_true', help='Setup inicial completo')
    grupo.add_argument('--atualizar',  action='store_true', help='Atualização incremental (dia a dia)')
    grupo.add_argument('--features',   action='store_true', help='Recalcular só features sem buscar dados')
    grupo.add_argument('--backfill-ticks', action='store_true',
                        help='Backfill de tick histórico pros dias com trade real (decoupled do --atualizar)')
    grupo.add_argument('--setup-ctrader', action='store_true',
                        help='Setup inicial dos ativos cTrader (XAUUSD/EURUSD) via Open API')
    grupo.add_argument('--atualizar-ctrader', action='store_true',
                        help='Atualização incremental dos ativos cTrader via Open API')
    parser.add_argument('--sem-tick',  action='store_true', help='Com --setup: pula busca de tick histórico')
    parser.add_argument('--dias', type=int, default=None,
                         help='Com --backfill-ticks: limita aos últimos N dias com trade. '
                              'Com --setup-ctrader: dias de histórico a buscar (padrão 365)')
    parser.add_argument('--de', type=str, default=None,
                         help='Com --backfill-ticks: data inicial YYYY-MM-DD')
    parser.add_argument('--ate', type=str, default=None,
                         help='Com --backfill-ticks: data final YYYY-MM-DD')

    args = parser.parse_args()

    if args.setup:
        rodar_setup(buscar_tick=not args.sem_tick)
    elif args.atualizar:
        rodar_atualizacao()
    elif args.features:
        rodar_features()
    elif args.backfill_ticks:
        de = pd.Timestamp(args.de).date() if args.de else None
        ate = pd.Timestamp(args.ate).date() if args.ate else None
        rodar_backfill_ticks(dias=args.dias, de=de, ate=ate)
    elif args.setup_ctrader:
        rodar_setup_ctrader(dias=args.dias)
    elif args.atualizar_ctrader:
        rodar_atualizacao_ctrader()


def atualizar_incremental_silencioso(ativo: str):
    """
    Versão silenciosa da atualização incremental para o dashboard.
    Chamada automaticamente ao abrir o dashboard — sem prints verbosos,
    retorna o DataFrame atualizado ou None em caso de falha.
    """
    try:
        df = merge_fontes.atualizar_incremental(ativo)
        if df is not None and not df.empty:
            import features as feat
            df = feat.calcular_todos_features(df, ativo=ativo, verbose=False)
            persistencia.salvar_candles(ativo, df)
        return df
    except Exception as e:
        print(f"[dashboard] Atualização incremental {ativo} falhou: {e}")
        return persistencia.carregar_candles(ativo)
