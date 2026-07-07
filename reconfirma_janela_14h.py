# =============================================================================
# reconfirma_janela_14h.py
# =============================================================================
# O teste anterior mostrou dado robusto das 12:00 às 18:24 do dia 29/06,
# mas uma busca isolada de 14:00-14:30 (dentro dessa faixa) retornou vazio.
# Isso é suspeito de ser falha transitória (já vista antes em copy_ticks_from)
# e não ausência real de dado. Reconfirma com retry manual e também via
# busca por posição (copy_ticks_from com retorno maior, filtrando depois).
#
# Execute: python reconfirma_janela_14h.py
# =============================================================================

import sys
sys.path.insert(0, '.')

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import time

import mt5_loader

print("=" * 90)
print("  RECONFIRMAÇÃO — janela 14:00-14:30 de 29/06/2026")
print("=" * 90)

mt5_loader.conectar()
ticker = mt5_loader.detectar_contrato_mais_recente("WIN")
print(f"\nUsando: {ticker}\n")

inicio = datetime(2026, 6, 29, 14, 0, 0)
fim = datetime(2026, 6, 29, 14, 30, 0)

# -----------------------------------------------------------------------------
# TESTE 1 — Repetir a função real buscar_ticks_periodo() 5 vezes
# -----------------------------------------------------------------------------
print("-" * 90)
print("TESTE 1 — Repetindo buscar_ticks_periodo() 5 vezes na mesma janela")
print("-" * 90)

for tentativa in range(1, 6):
    resultado = mt5_loader.buscar_ticks_periodo(ticker, inicio, fim)
    qtd = len(resultado) if resultado is not None else 0
    print(f"  Tentativa {tentativa}: {qtd} ticks")
    time.sleep(0.5)

# -----------------------------------------------------------------------------
# TESTE 2 — Chamada bruta direta ao copy_ticks_from, sem passar pela nossa
# função, pedindo um lote grande a partir das 14:00 e vendo o que vem
# -----------------------------------------------------------------------------
print("\n" + "-" * 90)
print("TESTE 2 — copy_ticks_from bruto a partir de 14:00, lote de 50000")
print("-" * 90)

for tentativa in range(1, 4):
    lote = mt5.copy_ticks_from(ticker, inicio, 50000, mt5.COPY_TICKS_TRADE)
    if lote is None or len(lote) == 0:
        print(f"  Tentativa {tentativa}: ❌ vazio. Erro: {mt5.last_error()}")
    else:
        primeiro = pd.to_datetime(lote[0]['time'], unit='s')
        ultimo = pd.to_datetime(lote[-1]['time'], unit='s')
        print(f"  Tentativa {tentativa}: ✅ {len(lote)} ticks | {primeiro} até {ultimo}")
    time.sleep(0.5)

mt5_loader.desconectar()

print("\n" + "=" * 90)
print("  Copie e me envie TODO o output acima.")
print("=" * 90)
