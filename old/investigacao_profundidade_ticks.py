# =============================================================================
# isolamento_minimo_ticks.py
# =============================================================================
# Teste no nível mais básico possível: existe ALGUM tick retornável pela
# API, em qualquer janela, com qualquer função? Vamos eliminar variáveis
# uma de cada vez.
#
# Execute: python isolamento_minimo_ticks.py
# =============================================================================

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta

print("=" * 90)
print("  ISOLAMENTO MÍNIMO — existe tick retornável de alguma forma?")
print("=" * 90)

if not mt5.initialize():
    print(f"❌ Falha ao conectar no MT5: {mt5.last_error()}")
    quit()

print("✅ Conectado ao MT5\n")

TICKER = "WINQ26"
mt5.symbol_select(TICKER, True)

# -----------------------------------------------------------------------------
# TESTE A — copy_ticks_from a partir de uma data BEM antiga, pedindo poucos ticks
# Isso testa se a função funciona em absoluto, independente de "janela recente"
# -----------------------------------------------------------------------------
print("-" * 90)
print("TESTE A — copy_ticks_from a partir de 2020-01-01, pedindo 10 ticks, COPY_TICKS_ALL")
print("-" * 90)

data_antiga = datetime(2020, 1, 1)
r = mt5.copy_ticks_from(TICKER, data_antiga, 10, mt5.COPY_TICKS_ALL)
print(f"  Resultado: {r}")
print(f"  Erro: {mt5.last_error()}")

# -----------------------------------------------------------------------------
# TESTE B — copy_ticks_from a partir de AGORA (sem timedelta nenhum)
# -----------------------------------------------------------------------------
print("\n" + "-" * 90)
print("TESTE B — copy_ticks_from a partir do tempo do último tick exato, 10 ticks, ALL")
print("-" * 90)

tick_atual = mt5.symbol_info_tick(TICKER)
tempo_exato = pd.to_datetime(tick_atual.time, unit='s')
print(f"  Tempo exato do último tick: {tempo_exato} (epoch: {tick_atual.time})")

r2 = mt5.copy_ticks_from(TICKER, tempo_exato - timedelta(seconds=10), 10, mt5.COPY_TICKS_ALL)
print(f"  Resultado: {r2}")
print(f"  Erro: {mt5.last_error()}")

# -----------------------------------------------------------------------------
# TESTE C — Mesmo teste, mas usando epoch diretamente (int), não datetime
# -----------------------------------------------------------------------------
print("\n" + "-" * 90)
print("TESTE C — copy_ticks_from usando epoch int diretamente")
print("-" * 90)

epoch_recente = tick_atual.time - 60  # 1 minuto antes, em epoch puro
r3 = mt5.copy_ticks_from(TICKER, epoch_recente, 10, mt5.COPY_TICKS_ALL)
print(f"  epoch usado: {epoch_recente}")
print(f"  Resultado: {r3}")
print(f"  Erro: {mt5.last_error()}")

# -----------------------------------------------------------------------------
# TESTE D — Testar em outro símbolo conhecido por alta liquidez internacional
# (se disponível) para descartar problema específico do WIN
# -----------------------------------------------------------------------------
print("\n" + "-" * 90)
print("TESTE D — Verificar símbolos disponíveis para teste alternativo")
print("-" * 90)

todos_simbolos = mt5.symbols_get()
visiveis = [s.name for s in todos_simbolos if s.visible][:20]
print(f"  Primeiros símbolos visíveis na Market Watch: {visiveis}")

# -----------------------------------------------------------------------------
# TESTE E — Versão da biblioteca e do terminal
# -----------------------------------------------------------------------------
print("\n" + "-" * 90)
print("TESTE E — Versões")
print("-" * 90)
print(f"  mt5.__version__: {mt5.__version__ if hasattr(mt5, '__version__') else 'N/A'}")
print(f"  mt5.version(): {mt5.version()}")
terminal = mt5.terminal_info()
print(f"  Terminal build: {terminal.build}")
print(f"  Terminal path: {terminal.path}")
print(f"  Connected: {terminal.connected}")

mt5.shutdown()

print("\n" + "=" * 90)
print("  Copie e me envie TODO o output acima.")
print("=" * 90)