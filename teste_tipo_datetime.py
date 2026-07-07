# =============================================================================
# teste_tipo_datetime.py
# =============================================================================
# Hipótese: copy_ticks_from() pode se comportar diferente dependendo do tipo
# exato do argumento de data — datetime.datetime nativo do Python vs
# pandas.Timestamp. Testa o MESMO ponto no tempo com os dois tipos, lado a
# lado, para isolar essa variável especificamente.
#
# Execute: python teste_tipo_datetime.py
# =============================================================================

import sys
sys.path.insert(0, '.')

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta

import mt5_loader

print("=" * 90)
print("  TESTE — datetime.datetime vs pandas.Timestamp em copy_ticks_from()")
print("=" * 90)

mt5_loader.conectar()
ticker = mt5_loader.detectar_contrato_mais_recente("WIN")
print(f"\nUsando: {ticker}\n")

# Mesmo ponto no tempo, dois tipos diferentes
ponto_datetime_nativo = datetime(2026, 6, 29, 6, 4, 15)
ponto_pandas_timestamp = pd.Timestamp(2026, 6, 29, 6, 4, 15)

print(f"Tipo 1: {type(ponto_datetime_nativo)} = {ponto_datetime_nativo}")
print(f"Tipo 2: {type(ponto_pandas_timestamp)} = {ponto_pandas_timestamp}")
print(f"São iguais em valor? {ponto_datetime_nativo == ponto_pandas_timestamp}\n")

print("-" * 90)
print("TESTE A — usando datetime.datetime nativo")
print("-" * 90)
lote_a = mt5.copy_ticks_from(ticker, ponto_datetime_nativo, 100000, mt5.COPY_TICKS_TRADE)
if lote_a is not None and len(lote_a) > 0:
    primeiro = pd.to_datetime(lote_a[0]['time'], unit='s')
    ultimo = pd.to_datetime(lote_a[-1]['time'], unit='s')
    print(f"  {len(lote_a)} ticks | {primeiro} até {ultimo}")
else:
    print("  ❌ vazio")

print("\n" + "-" * 90)
print("TESTE B — usando pandas.Timestamp")
print("-" * 90)
lote_b = mt5.copy_ticks_from(ticker, ponto_pandas_timestamp, 100000, mt5.COPY_TICKS_TRADE)
if lote_b is not None and len(lote_b) > 0:
    primeiro = pd.to_datetime(lote_b[0]['time'], unit='s')
    ultimo = pd.to_datetime(lote_b[-1]['time'], unit='s')
    print(f"  {len(lote_b)} ticks | {primeiro} até {ultimo}")
else:
    print("  ❌ vazio")

mt5_loader.desconectar()

print("\n" + "=" * 90)
print("  Copie e me envie TODO o output acima.")
print("=" * 90)
