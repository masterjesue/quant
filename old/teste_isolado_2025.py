# =============================================================================
# teste_isolado_2025.py
# =============================================================================
# WINV25 e WINJ26 (contratos de 2025, mais recentes que WINZ22/WING23 de
# 2022/2023) também falharam no mapeamento original com "Terminal: Call
# failed". Diferente do WINZ22 (já confirmado como ausência real de tick,
# 8/8 tentativas falharam de forma consistente), esses são mais recentes e
# pode ser que a causa seja diferente. Testa com o mesmo rigor: múltiplas
# tentativas, múltiplos pontos no tempo, para cada um.
#
# Execute: python teste_isolado_2025.py
# =============================================================================

import sys
sys.path.insert(0, '.')

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import time

import mt5_loader

print("=" * 90)
print("  TESTE ISOLADO — WINV25 e WINJ26 (contratos 2025)")
print("=" * 90)

mt5_loader.conectar()

CONTRATOS = {
    "WINV25": [
        datetime(2025, 4, 16, 6, 0, 0),   # início, ajustado -3h
        datetime(2025, 7, 15, 6, 0, 0),   # meio
        datetime(2025, 10, 14, 6, 0, 0),  # fim, ajustado -3h
    ],
    "WINJ26": [
        datetime(2025, 9, 19, 6, 0, 0),   # início, ajustado -3h
        datetime(2026, 1, 1, 6, 0, 0),    # meio
        datetime(2026, 4, 14, 6, 0, 0),   # fim, ajustado -3h
    ],
}

resultados = {}

for ticker, pontos in CONTRATOS.items():
    print(f"\n{'-' * 90}")
    print(f"  {ticker}")
    print(f"{'-' * 90}")

    mt5.symbol_select(ticker, True)
    info = mt5.symbol_info(ticker)
    print(f"  symbol_info encontrado: {info is not None}")

    sucesso_em_algum_ponto = False

    for ponto in pontos:
        # Testa 3 vezes cada ponto, com pausa, antes de desistir
        sucesso_aqui = False
        for tentativa in range(1, 4):
            lote = mt5.copy_ticks_from(ticker, ponto, 1000, mt5.COPY_TICKS_TRADE)
            if lote is not None and len(lote) > 0:
                primeiro = pd.to_datetime(lote[0]['time'], unit='s')
                print(f"  {ponto} (tentativa {tentativa}): ✅ {len(lote)} ticks | primeiro: {primeiro}")
                sucesso_aqui = True
                sucesso_em_algum_ponto = True
                break
            else:
                print(f"  {ponto} (tentativa {tentativa}): ❌ {mt5.last_error()}")
            time.sleep(1.5)

        if not sucesso_aqui:
            print(f"  {ponto}: falhou em todas as 3 tentativas")

    resultados[ticker] = sucesso_em_algum_ponto
    print(f"\n  RESULTADO {ticker}: {'✅ tem tick em algum ponto' if sucesso_em_algum_ponto else '❌ sem tick em nenhum ponto testado'}")

mt5_loader.desconectar()

print("\n" + "=" * 90)
print("  RESUMO FINAL")
print("=" * 90)
for ticker, tem_tick in resultados.items():
    print(f"  {ticker}: {'TEM tick disponível' if tem_tick else 'SEM tick disponível (testado rigorosamente)'}")

print("\n" + "=" * 90)
print("  Copie e me envie TODO o output acima.")
print("=" * 90)
