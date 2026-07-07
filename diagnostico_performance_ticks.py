# =============================================================================
# diagnostico_performance_ticks.py
# =============================================================================
# O teste anterior mostrou 98s para buscar ~166k ticks de 1 dia — lento
# demais para escalar a 2 anos de histórico (~13h estimadas). Este script
# isola onde o tempo está sendo gasto: chamada bruta ao MT5 vs processamento
# em pandas, e testa diferentes tamanhos de lote (TICKS_POR_CHAMADA).
#
# Execute: python diagnostico_performance_ticks.py
# =============================================================================

import sys
sys.path.insert(0, '.')

import MetaTrader5 as mt5
import pandas as pd
import time
from datetime import datetime, timedelta

import mt5_loader
import config_backtest as cfg

print("=" * 90)
print("  DIAGNÓSTICO DE PERFORMANCE — busca de ticks")
print("=" * 90)

mt5_loader.conectar()
ticker = mt5_loader.detectar_contrato_mais_recente("WIN")
print(f"\nUsando: {ticker}\n")

# -----------------------------------------------------------------------------
# TESTE A — Uma ÚNICA chamada bruta ao copy_ticks_from, sem nenhum
# processamento, com diferentes tamanhos de lote
# -----------------------------------------------------------------------------
print("-" * 90)
print("TESTE A — Chamada bruta única, sem processamento, diferentes tamanhos")
print("-" * 90)

inicio_teste = datetime(2026, 6, 29, 12, 0, 0)  # sabemos que há dado aqui

for tamanho_lote in [5000, 20000, 50000, 100000, 200000]:
    t0 = time.time()
    lote = mt5.copy_ticks_from(ticker, inicio_teste, tamanho_lote, mt5.COPY_TICKS_TRADE)
    t1 = time.time()

    qtd = len(lote) if lote is not None else 0
    print(f"  Lote solicitado={tamanho_lote:>7d} | recebido={qtd:>7d} | "
          f"tempo={t1-t0:.2f}s | taxa={qtd/(t1-t0) if t1>t0 else 0:.0f} ticks/s")

# -----------------------------------------------------------------------------
# TESTE B — Mede separadamente: tempo da chamada MT5 vs tempo do
# processamento pandas (DataFrame, filtro, concat) dentro da função real
# -----------------------------------------------------------------------------
print("\n" + "-" * 90)
print("TESTE B — Separando tempo MT5 puro vs processamento pandas")
print("-" * 90)

t0 = time.time()
lote_grande = mt5.copy_ticks_from(ticker, inicio_teste, 200000, mt5.COPY_TICKS_TRADE)
t1 = time.time()
print(f"  Tempo só da chamada MT5 (200k ticks): {t1-t0:.2f}s")

if lote_grande is not None:
    t2 = time.time()
    df = pd.DataFrame(lote_grande)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    t3 = time.time()
    print(f"  Tempo de conversão para DataFrame + datetime: {t3-t2:.2f}s")

    t4 = time.time()
    df['agressor'] = df['flags'].apply(
        lambda f: 'comprador' if f & 32 else ('vendedor' if f & 64 else 'indefinido')
    )
    t5 = time.time()
    print(f"  Tempo de classificação de agressor (apply): {t5-t4:.2f}s")

mt5_loader.desconectar()

print("\n" + "=" * 90)
print("  Copie e me envie TODO o output acima.")
print("=" * 90)
