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
# =============================================================================

import argparse
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config_backtest as cfg
import merge_fontes
import features as feat
import persistencia
import mt5_loader


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
        df_feat = feat.calcular_todos_features(df)
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
        df_feat = feat.calcular_todos_features(df, verbose=False)
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
        df_feat = feat.calcular_todos_features(df)
        t1 = time.time()
        print(f"[run] {ativo}: features em {t1-t0:.1f}s")

        persistencia.salvar_candles(ativo, df_feat)
        print(f"[run] {ativo}: base com features salva.")

    print("\n" + "=" * 80)
    print("  FEATURES RECALCULADAS.")
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
    parser.add_argument('--sem-tick',  action='store_true', help='Com --setup: pula busca de tick histórico')

    args = parser.parse_args()

    if args.setup:
        rodar_setup(buscar_tick=not args.sem_tick)
    elif args.atualizar:
        rodar_atualizacao()
    elif args.features:
        rodar_features()


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
            df = feat.calcular_todos_features(df, verbose=False)
            persistencia.salvar_candles(ativo, df)
        return df
    except Exception as e:
        print(f"[dashboard] Atualização incremental {ativo} falhou: {e}")
        return persistencia.carregar_candles(ativo)
