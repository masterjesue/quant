# =============================================================================
# valida_correcao_timezone.py
# =============================================================================
# Valida a correção aplicada em buscar_ticks_periodo() para o deslocamento
# de 3h identificado em 30/06/2026. Testa especificamente a janela que
# falhou antes (14:00-14:30 de 29/06/2026) e confirma que agora retorna
# dado real dentro da janela correta.
#
# Execute: python valida_correcao_timezone.py
# =============================================================================

import sys
sys.path.insert(0, '.')

import mt5_loader
from datetime import datetime

print("=" * 90)
print("  VALIDAÇÃO — correção de timezone em buscar_ticks_periodo()")
print("=" * 90)

mt5_loader.conectar()
ticker = mt5_loader.detectar_contrato_mais_recente("WIN")
print(f"\nUsando: {ticker}\n")

# Janela que falhou antes da correção
inicio = datetime(2026, 6, 29, 14, 0, 0)
fim = datetime(2026, 6, 29, 14, 30, 0)

print(f"Testando janela que falhava antes: {inicio} até {fim}")
resultado = mt5_loader.buscar_ticks_periodo(ticker, inicio, fim)

if resultado is None or resultado.empty:
    print("❌ AINDA FALHOU — correção não resolveu.")
else:
    print(f"✅ {len(resultado)} ticks retornados")
    print(f"   Primeiro: {resultado['datetime'].min()}")
    print(f"   Último:   {resultado['datetime'].max()}")

    # Confirma que os timestamps estão DENTRO da janela pedida (não fora)
    dentro_da_janela = (resultado['datetime'] >= inicio).all() and (resultado['datetime'] <= fim).all()
    print(f"   Todos os timestamps dentro da janela pedida: {dentro_da_janela}")

# Teste adicional — janela completa do dia, para garantir que não quebrou
# o que já funcionava
print(f"\nTestando janela completa do dia (deve continuar funcionando):")
inicio_dia = datetime(2026, 6, 29, 9, 0, 0)
fim_dia = datetime(2026, 6, 29, 18, 30, 0)
resultado_dia = mt5_loader.buscar_ticks_periodo(ticker, inicio_dia, fim_dia)

if resultado_dia is None or resultado_dia.empty:
    print("❌ Janela completa do dia falhou — possível regressão.")
else:
    print(f"✅ {len(resultado_dia)} ticks | {resultado_dia['datetime'].min()} até {resultado_dia['datetime'].max()}")

mt5_loader.desconectar()

print("\n" + "=" * 90)
print("  Copie e me envie TODO o output acima.")
print("=" * 90)
