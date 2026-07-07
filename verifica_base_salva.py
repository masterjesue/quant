# =============================================================================
# verifica_base_salva.py
# =============================================================================
# Confere a base WIN salva: estatísticas de delta (sem overflow), cobertura
# de fluxo_disponivel, e amostra de dados para inspeção visual.
#
# Execute: python verifica_base_salva.py
# =============================================================================

import sys
sys.path.insert(0, '.')

import pandas as pd
import persistencia

print("=" * 90)
print("  VERIFICAÇÃO DA BASE SALVA — WIN")
print("=" * 90)

df = persistencia.carregar_candles('WIN')

print(f"\nTotal de candles: {len(df)}")
print(f"Período: {df['datetime'].min()} até {df['datetime'].max()}")

com_fluxo = df[df['fluxo_disponivel'] == True]
print(f"\nCandles com fluxo calculado: {len(com_fluxo)} ({len(com_fluxo)/len(df)*100:.1f}%)")

if not com_fluxo.empty:
    delta_numerico = pd.to_numeric(com_fluxo['delta'])
    print(f"\nEstatísticas de delta:")
    print(f"  Mínimo:  {delta_numerico.min()}")
    print(f"  Máximo:  {delta_numerico.max()}")
    print(f"  Média:   {delta_numerico.mean():.1f}")
    print(f"  Mediana: {delta_numerico.median()}")

    # Checagem de sanidade: nenhum delta deveria ser um número absurdamente
    # grande (sinal de overflow não corrigido)
    suspeitos = com_fluxo[delta_numerico.abs() > 100000]
    print(f"\nCandles com delta suspeito (|delta| > 100.000): {len(suspeitos)}")
    if not suspeitos.empty:
        print("  ⚠️ ATENÇÃO: possível overflow ainda presente!")
        print(suspeitos[['datetime', 'vol_comprador', 'vol_vendedor', 'delta']].head(10))
    else:
        print("  ✅ Nenhum valor suspeito — overflow parece corrigido.")

    print(f"\nAmostra de candles com delta negativo (vendedor dominou):")
    negativos = com_fluxo[delta_numerico < 0].tail(5)
    print(negativos[['datetime', 'vol_comprador', 'vol_vendedor', 'delta']].to_string(index=False))

    print(f"\nAmostra de candles com delta positivo (comprador dominou):")
    positivos = com_fluxo[delta_numerico > 0].tail(5)
    print(positivos[['datetime', 'vol_comprador', 'vol_vendedor', 'delta']].to_string(index=False))

print("\n" + "=" * 90)
print("  Copie e me envie o output acima.")
print("=" * 90)
