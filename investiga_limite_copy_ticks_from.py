# =============================================================================
# investiga_limite_copy_ticks_from.py
# =============================================================================
# A paginação trava repetindo o MESMO lote mesmo avançando o ponto de
# partida em milissegundos. Hipótese: copy_ticks_from() pode ter algum
# comportamento de cache ou limite que faz retornar sempre o mesmo
# conjunto quando o ponto pedido está numa janela de altíssima densidade
# de ticks (abertura de pregão). Testa pedindo pontos de partida bem
# diferentes (não apenas +1ms) para ver se o lote muda de verdade.
#
# Execute: python investiga_limite_copy_ticks_from.py
# =============================================================================

import sys
sys.path.insert(0, '.')

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta

import mt5_loader

print("=" * 90)
print("  INVESTIGAÇÃO — copy_ticks_from trava em janela de alta densidade?")
print("=" * 90)

mt5_loader.conectar()
ticker = mt5_loader.detectar_contrato_mais_recente("WIN")
print(f"\nUsando: {ticker}\n")

DESLOCAMENTO = timedelta(hours=3)

# -----------------------------------------------------------------------------
# TESTE 1 — Pede a partir de pontos BEM diferentes (não apenas alguns ms),
# em passos de 30 segundos, para ver se o lote realmente muda
# -----------------------------------------------------------------------------
print("-" * 90)
print("TESTE 1 — Pontos de partida em passos de 30 segundos")
print("-" * 90)

base = datetime(2026, 6, 29, 9, 0, 0) - DESLOCAMENTO  # já ajustado para MT5

for incremento_seg in [0, 30, 60, 90, 120, 150, 180]:
    ponto = base + timedelta(seconds=incremento_seg)
    lote = mt5.copy_ticks_from(ticker, ponto, 1000, mt5.COPY_TICKS_TRADE)  # lote MENOR para teste rápido

    if lote is None or len(lote) == 0:
        print(f"  +{incremento_seg:3d}s ({ponto}): ❌ vazio")
        continue

    primeiro = pd.to_datetime(lote[0]['time'], unit='s')
    ultimo = pd.to_datetime(lote[-1]['time'], unit='s')
    print(f"  +{incremento_seg:3d}s pedido={ponto.time()} -> primeiro_tick={primeiro} ultimo_tick={ultimo} qtd={len(lote)}")

# -----------------------------------------------------------------------------
# TESTE 2 — Testa se reduzir o tamanho do lote solicitado (count) muda o
# comportamento — talvez 100000 esteja sendo limitado internamente
# -----------------------------------------------------------------------------
print("\n" + "-" * 90)
print("TESTE 2 — Mesmo ponto de partida, diferentes tamanhos de count")
print("-" * 90)

ponto_fixo = datetime(2026, 6, 29, 9, 4, 15) - DESLOCAMENTO  # 1s depois do ultimo tick visto antes

for count in [100, 1000, 10000, 50000, 100000]:
    lote = mt5.copy_ticks_from(ticker, ponto_fixo, count, mt5.COPY_TICKS_TRADE)
    if lote is None or len(lote) == 0:
        print(f"  count={count:6d}: ❌ vazio")
        continue
    primeiro = pd.to_datetime(lote[0]['time'], unit='s')
    ultimo = pd.to_datetime(lote[-1]['time'], unit='s')
    print(f"  count={count:6d}: recebido={len(lote):6d} | {primeiro} até {ultimo}")

mt5_loader.desconectar()

print("\n" + "=" * 90)
print("  Copie e me envie TODO o output acima.")
print("=" * 90)
