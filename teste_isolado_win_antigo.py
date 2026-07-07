# =============================================================================
# teste_isolado_win_antigo.py
# =============================================================================
# O mapeamento de profundidade falhou consistentemente para TODOS os
# contratos WIN antigos (Z22, G23, V25, J26, M26) com "Terminal: Call
# failed" — padrão suspeito de falha sistêmica/transitória, não ausência
# real de dado (já vimos esse erro ser falso alarme antes). Testa UM único
# contrato antigo isoladamente, com mais diagnóstico e mais tentativas.
#
# Execute: python teste_isolado_win_antigo.py
# =============================================================================

import sys
sys.path.insert(0, '.')

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import time

import mt5_loader

print("=" * 90)
print("  TESTE ISOLADO — WINZ22 (contrato antigo, falhou no mapeamento)")
print("=" * 90)

mt5_loader.conectar()

TICKER = "WINZ22"

# -----------------------------------------------------------------------------
# TESTE 1 — symbol_select explícito, depois symbol_info para confirmar
# -----------------------------------------------------------------------------
print("-" * 90)
print("TESTE 1 — Confirmar que o símbolo está acessível")
print("-" * 90)

selecionado = mt5.symbol_select(TICKER, True)
print(f"  symbol_select retornou: {selecionado}")

info = mt5.symbol_info(TICKER)
print(f"  symbol_info: {'encontrado' if info else 'NÃO encontrado'}")
if info:
    print(f"    visible: {info.visible}")

# -----------------------------------------------------------------------------
# TESTE 2 — Chamada direta e simples ao copy_ticks_from, repetida 5 vezes
# -----------------------------------------------------------------------------
print("\n" + "-" * 90)
print("TESTE 2 — copy_ticks_from direto, 5 tentativas com pausa")
print("-" * 90)

ponto = datetime(2022, 8, 17, 6, 0, 0)  # já ajustado -3h do que seria 09:00 real

for tentativa in range(1, 6):
    lote = mt5.copy_ticks_from(TICKER, ponto, 1000, mt5.COPY_TICKS_TRADE)
    if lote is None or len(lote) == 0:
        erro = mt5.last_error()
        print(f"  Tentativa {tentativa}: ❌ vazio. Erro: {erro}")
    else:
        primeiro = pd.to_datetime(lote[0]['time'], unit='s')
        print(f"  Tentativa {tentativa}: ✅ {len(lote)} ticks | primeiro: {primeiro}")
    time.sleep(1.5)

# -----------------------------------------------------------------------------
# TESTE 3 — Testa em pontos bem diferentes do período conhecido de candles,
# incluindo o FIM do período (mais recente, pode ter mais chance de ter tick)
# -----------------------------------------------------------------------------
print("\n" + "-" * 90)
print("TESTE 3 — Testando vários pontos dentro do período de candles conhecido")
print("-" * 90)

pontos_teste = [
    datetime(2022, 8, 17, 6, 0, 0),   # início, ajustado -3h
    datetime(2022, 9, 15, 6, 0, 0),   # meio
    datetime(2022, 12, 14, 6, 0, 0),  # fim, ajustado -3h
]

for ponto in pontos_teste:
    lote = mt5.copy_ticks_from(TICKER, ponto, 1000, mt5.COPY_TICKS_TRADE)
    if lote is None or len(lote) == 0:
        print(f"  {ponto}: ❌ vazio. Erro: {mt5.last_error()}")
    else:
        primeiro = pd.to_datetime(lote[0]['time'], unit='s')
        print(f"  {ponto}: ✅ {len(lote)} ticks | primeiro: {primeiro}")
    time.sleep(1)

mt5_loader.desconectar()

print("\n" + "=" * 90)
print("  Copie e me envie TODO o output acima.")
print("=" * 90)
