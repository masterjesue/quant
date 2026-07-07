# =============================================================================
# teste_busca_ticks.py
# =============================================================================
# Testa a função buscar_ticks_periodo() do mt5_loader — ainda não validada.
# Essa função usa copy_ticks_from() com paginação (avança o ponto de partida
# a cada lote) e retry, já que copy_ticks_range() foi descartado por falhar
# de forma consistente em teste anterior.
#
# Testa em duas escalas: uma janela curta (últimos 30 minutos, validação
# rápida de correção) e uma janela mais longa (último dia completo de
# pregão, validação de paginação e performance).
#
# Execute: python teste_busca_ticks.py
# =============================================================================

import sys
sys.path.insert(0, '.')

import mt5_loader
import config_backtest as cfg
from datetime import datetime, timedelta
import time

print("=" * 90)
print("  TESTE — buscar_ticks_periodo()")
print("=" * 90)

mt5_loader.conectar()

# Detecta o contrato vigente de WIN para o teste (já validado anteriormente)
ticker = mt5_loader.detectar_contrato_mais_recente("WIN")
if ticker is None:
    print("❌ Não foi possível detectar contrato. Abortando teste.")
    mt5_loader.desconectar()
    sys.exit(1)

print(f"\nUsando contrato: {ticker}\n")

# -----------------------------------------------------------------------------
# TESTE 1 — Janela curta (30 minutos, em horário de pregão CONHECIDO como
# tendo dado robusto — evita depender do horário exato em que o script roda)
# -----------------------------------------------------------------------------
print("-" * 90)
print("TESTE 1 — Janela curta (30 minutos, horário fixo de pregão conhecido)")
print("-" * 90)

inicio_curto = datetime(2026, 6, 29, 14, 0, 0)
fim_curto = datetime(2026, 6, 29, 14, 30, 0)

print(f"Janela: {inicio_curto} até {fim_curto}")

t0 = time.time()
ticks_curto = mt5_loader.buscar_ticks_periodo(ticker, inicio_curto, fim_curto)
t1 = time.time()

if ticks_curto is None or ticks_curto.empty:
    print("❌ Nenhum tick retornado.")
else:
    print(f"✅ {len(ticks_curto)} ticks em {t1-t0:.2f}s")
    print(f"\nPrimeiros 5 ticks:")
    print(ticks_curto.head().to_string(index=False))
    print(f"\nÚltimos 5 ticks:")
    print(ticks_curto.tail().to_string(index=False))

    print(f"\nDistribuição de agressor:")
    print(ticks_curto['agressor'].value_counts())

# -----------------------------------------------------------------------------
# TESTE 2 — Janela mais longa (dia de pregão completo conhecido: 29/06/2026)
# -----------------------------------------------------------------------------
print("\n" + "-" * 90)
print("TESTE 2 — Janela longa (dia de pregão completo, 29/06/2026, 09h às 18h30)")
print("-" * 90)

inicio_longo = datetime(2026, 6, 29, 9, 0, 0)
fim_longo = datetime(2026, 6, 29, 18, 30, 0)

print(f"Janela: {inicio_longo} até {fim_longo}")

t0 = time.time()
ticks_longo = mt5_loader.buscar_ticks_periodo(ticker, inicio_longo, fim_longo)
t1 = time.time()

if ticks_longo is None or ticks_longo.empty:
    print("❌ Nenhum tick retornado.")
else:
    print(f"✅ {len(ticks_longo)} ticks em {t1-t0:.2f}s")
    print(f"Primeiro tick: {ticks_longo['datetime'].min()}")
    print(f"Último tick:   {ticks_longo['datetime'].max()}")
    print(f"\nDistribuição de agressor:")
    print(ticks_longo['agressor'].value_counts())

    # Checa se há buracos suspeitos (paginação pode ter falhado em algum trecho)
    ticks_longo_sorted = ticks_longo.sort_values('datetime')
    diffs = ticks_longo_sorted['datetime'].diff()
    maior_gap = diffs.max()
    print(f"\nMaior intervalo entre ticks consecutivos: {maior_gap}")
    if maior_gap > timedelta(minutes=15):
        print("⚠️ Gap suspeito > 15 min encontrado — pode indicar falha de paginação "
              "ou período real sem negociação (ex: leilão, baixa liquidez).")

mt5_loader.desconectar()

print("\n" + "=" * 90)
print("  Copie e me envie TODO o output acima.")
print("=" * 90)
