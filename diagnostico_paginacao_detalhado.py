# =============================================================================
# diagnostico_paginacao_detalhado.py
# =============================================================================
# A correção de timezone resolveu a busca pontual (14:00-14:30), mas quebrou
# a paginação de janela longa (09:00-18:30 retornou só até 09:04). Este
# script reproduz a lógica de paginação PASSO A PASSO com prints detalhados
# de cada iteração, para identificar exatamente onde e por que ela para cedo.
#
# Execute: python diagnostico_paginacao_detalhado.py
# =============================================================================

import sys
sys.path.insert(0, '.')

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

import mt5_loader
import config_backtest as cfg

print("=" * 90)
print("  DIAGNÓSTICO DETALHADO DE PAGINAÇÃO")
print("=" * 90)

mt5_loader.conectar()
ticker = mt5_loader.detectar_contrato_mais_recente("WIN")
print(f"\nUsando: {ticker}\n")

DESLOCAMENTO = pd.Timedelta(hours=3)

inicio = datetime(2026, 6, 29, 9, 0, 0)
fim = datetime(2026, 6, 29, 18, 30, 0)

print(f"Janela alvo: {inicio} até {fim}\n")

ponto_atual = inicio
iteracao = 0

while ponto_atual < fim and iteracao < 30:  # limite de segurança maior
    iteracao += 1
    ponto_ajustado = ponto_atual - DESLOCAMENTO

    print(f"--- Iteração {iteracao} ---")
    print(f"  ponto_atual (horário real desejado): {ponto_atual}")
    print(f"  ponto_ajustado (enviado ao MT5):      {ponto_ajustado}")

    lote = mt5.copy_ticks_from(ticker, ponto_ajustado, cfg.TICKS_POR_CHAMADA, mt5.COPY_TICKS_TRADE)

    if lote is None or len(lote) == 0:
        print(f"  ❌ Lote vazio. Parando.")
        break

    print(f"  Lote recebido: {len(lote)} ticks")

    primeiro_lote = pd.to_datetime(lote[0]['time'], unit='s')
    ultimo_lote = pd.to_datetime(lote[-1]['time'], unit='s')
    print(f"  Lote cobre (horário real, sem ajuste): {primeiro_lote} até {ultimo_lote}")

    lote_completo = len(lote) >= cfg.TICKS_POR_CHAMADA
    print(f"  Lote completo (== TICKS_POR_CHAMADA)? {lote_completo}")

    ponto_atual_novo = ultimo_lote + pd.Timedelta(seconds=1)
    print(f"  Próximo ponto_atual (ultimo_lote + 1s): {ponto_atual_novo}")

    if ponto_atual_novo <= ponto_atual:
        print(f"  ⚠️ PARADA: próximo ponto ({ponto_atual_novo}) <= ponto atual ({ponto_atual})")
        break

    ponto_atual = ponto_atual_novo

    if len(lote) < cfg.TICKS_POR_CHAMADA:
        print(f"  ⚠️ PARADA: lote incompleto ({len(lote)} < {cfg.TICKS_POR_CHAMADA})")
        break

    print()

mt5_loader.desconectar()

print("\n" + "=" * 90)
print("  Copie e me envie TODO o output acima.")
print("=" * 90)
