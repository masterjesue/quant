# =============================================================================
# investiga_deslocamento_3h.py
# =============================================================================
# O teste anterior mostrou: pedir ticks a partir de 14:00 retornou ticks de
# 17:00 (deslocamento de exatos +3h). Mas testes anteriores (profundidade de
# ticks, 30/06) usaram datetime "ingênuo" da mesma forma e funcionaram
# corretamente. Preciso entender a diferença entre os cenários antes de
# aplicar qualquer correção — pode não ser timezone, pode ser outra coisa
# (ex: o ponto exato pedido não tinha tick e o MT5 retornou o PRÓXIMO tick
# disponível, que por coincidência ficou ~3h depois nesse dia específico,
# por exemplo se houve baixa liquidez entre 14h-17h nesse dia).
#
# Execute: python investiga_deslocamento_3h.py
# =============================================================================

import sys
sys.path.insert(0, '.')

import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

import mt5_loader

print("=" * 90)
print("  INVESTIGAÇÃO — causa do deslocamento de 3h")
print("=" * 90)

mt5_loader.conectar()
ticker = mt5_loader.detectar_contrato_mais_recente("WIN")
print(f"\nUsando: {ticker}\n")

# -----------------------------------------------------------------------------
# Testa pontos de partida em vários horários do mesmo dia, em passos de 1h,
# para ver se o deslocamento de 3h é constante ou varia
# -----------------------------------------------------------------------------
print("-" * 90)
print("Testando copy_ticks_from em vários horários do dia 29/06/2026")
print("-" * 90)

for hora in [9, 10, 11, 12, 13, 14, 15, 16, 17, 18]:
    ponto = datetime(2026, 6, 29, hora, 0, 0)
    lote = mt5.copy_ticks_from(ticker, ponto, 10, mt5.COPY_TICKS_TRADE)

    if lote is None or len(lote) == 0:
        print(f"  Pedido {ponto.strftime('%H:%M')}: ❌ vazio")
        continue

    primeiro_retornado = pd.to_datetime(lote[0]['time'], unit='s')
    diff = primeiro_retornado - ponto
    print(f"  Pedido {ponto.strftime('%H:%M')} -> Retornado {primeiro_retornado} "
          f"(diferença: {diff})")

# -----------------------------------------------------------------------------
# Confere: existe mesmo dado real perto das 14h, usando uma janela MAIOR
# pedida a partir das 13:00 (1h antes), para ver se em algum ponto intermediário
# aparece o dado das 14h
# -----------------------------------------------------------------------------
print("\n" + "-" * 90)
print("Pedindo lote grande a partir de 13:00 para ver se cobre até 14h-15h")
print("-" * 90)

lote_grande = mt5.copy_ticks_from(ticker, datetime(2026, 6, 29, 13, 0, 0), 200000, mt5.COPY_TICKS_TRADE)
if lote_grande is not None and len(lote_grande) > 0:
    df = pd.DataFrame(lote_grande)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    print(f"  {len(df)} ticks retornados | {df['datetime'].min()} até {df['datetime'].max()}")

    # Verifica se 14:00-14:30 está coberto dentro desse lote maior
    janela_14h = df[(df['datetime'] >= datetime(2026,6,29,14,0,0)) &
                     (df['datetime'] <= datetime(2026,6,29,14,30,0))]
    print(f"  Ticks dentro de 14:00-14:30 neste lote maior: {len(janela_14h)}")
    if not janela_14h.empty:
        print(janela_14h[['datetime']].head())

mt5_loader.desconectar()

print("\n" + "=" * 90)
print("  Copie e me envie TODO o output acima.")
print("=" * 90)
