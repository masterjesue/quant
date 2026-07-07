# =============================================================================
# mapeamento_profundidade_ticks.py
# =============================================================================
# Para cada contrato pilar (WIN e WDO), mapeia até onde para trás no tempo
# os ticks realmente existem de forma consistente — usando o método validado
# (buscar_ticks_periodo, já corrigido para timezone e tipo de dado).
#
# Estratégia: para cada contrato, testa o INÍCIO da janela de candles
# conhecida (o ponto mais antigo e mais exigente) buscando uma pequena
# amostra de ticks (1 dia). Se houver tick lá, assume-se que o contrato
# tem cobertura de tick desde o início. Se não houver, faz busca binária
# entre o início e o fim para encontrar a fronteira real.
#
# Execute: python mapeamento_profundidade_ticks.py
# Isso pode levar alguns minutos dependendo de quantos contratos precisarem
# de busca binária.
# =============================================================================

import sys
sys.path.insert(0, '.')

import mt5_loader
import config_backtest as cfg
from datetime import datetime, timedelta
import pandas as pd
import time

print("=" * 90)
print("  MAPEAMENTO DE PROFUNDIDADE DE TICKS — contratos pilares")
print("=" * 90)

mt5_loader.conectar()

# -----------------------------------------------------------------------------
# Contratos pilares e janela de candles conhecida (do levantamento de 30/06)
# -----------------------------------------------------------------------------

CONTRATOS_TESTE = {
    "WIN": [
        ("WINZ22", datetime(2022, 8, 17), datetime(2022, 12, 14)),
        ("WING23", datetime(2022, 10, 13), datetime(2023, 2, 15)),
        ("WINV25", datetime(2025, 4, 16), datetime(2025, 10, 15)),
        ("WINJ26", datetime(2025, 9, 19), datetime(2026, 4, 15)),
        ("WINM26", datetime(2025, 12, 8), datetime(2026, 6, 17)),
        ("WINQ26", datetime(2026, 2, 13), datetime(2026, 6, 30)),
    ],
    "WDO": [
        ("WDOF23", datetime(2022, 7, 18), datetime(2022, 12, 29)),
        ("WDOZ22", datetime(2022, 9, 8), datetime(2022, 11, 30)),
        ("WDOG23", datetime(2022, 8, 22), datetime(2023, 1, 31)),
        ("WDOH23", datetime(2022, 8, 31), datetime(2023, 2, 28)),
        ("WDOV25", datetime(2024, 8, 7), datetime(2025, 9, 30)),
        ("WDON26", datetime(2026, 3, 31), datetime(2026, 6, 29)),
        ("WDOQ26", datetime(2026, 4, 13), datetime(2026, 6, 30)),
    ],
}

resultados = []

def testar_tick_disponivel(ticker: str, data: datetime, tentativas_extras: int = 3) -> bool:
    """
    Testa se há tick disponível numa janela pequena (1 dia) a partir da data.

    CORRIGIDO (30/06/2026): o retry padrão de buscar_ticks_periodo (3
    tentativas, 1.5s) não foi suficiente para diferenciar corretamente
    falha transitória de ausência real de dado quando testado em sequência
    longa (vários contratos seguidos) — confirmado em teste: WINV25 e
    WINJ26 falharam no mapeamento original mas funcionaram perfeitamente
    em teste isolado na primeira tentativa. Adiciona uma camada extra de
    tentativas aqui, com delay maior, antes de concluir "sem tick".
    """
    fim = data + timedelta(days=1)

    for tentativa_extra in range(tentativas_extras):
        resultado = mt5_loader.buscar_ticks_periodo(ticker, data, fim)
        if resultado is not None and not resultado.empty:
            return True
        if tentativa_extra < tentativas_extras - 1:
            time.sleep(3)  # delay maior entre camadas de retry

    return False

def busca_binaria_fronteira(ticker: str, inicio_conhecido: datetime, fim_conhecido: datetime) -> datetime:
    """
    Busca binária entre inicio_conhecido (sabe-se que NÃO tem tick) e
    fim_conhecido (sabe-se que TEM tick) para encontrar a fronteira real
    onde os ticks passam a existir.
    """
    baixo, alto = inicio_conhecido, fim_conhecido

    while (alto - baixo).days > 1:
        meio = baixo + (alto - baixo) / 2
        meio = meio.replace(hour=0, minute=0, second=0, microsecond=0)

        tem_tick = testar_tick_disponivel(ticker, meio)
        print(f"    Testando {meio.date()}: {'✅ tem tick' if tem_tick else '❌ sem tick'}")

        if tem_tick:
            alto = meio
        else:
            baixo = meio

    return alto


for prefixo, contratos in CONTRATOS_TESTE.items():
    print(f"\n{'=' * 90}")
    print(f"  {prefixo}")
    print(f"{'=' * 90}")

    for ticker, data_inicio, data_fim in contratos:
        print(f"\n  --- {ticker} (candles: {data_inicio.date()} a {data_fim.date()}) ---")

        # Testa primeiro se o INÍCIO já tem tick (caso ideal: cobertura total)
        tem_tick_no_inicio = testar_tick_disponivel(ticker, data_inicio)

        if tem_tick_no_inicio:
            print(f"  ✅ Tick disponível desde o início ({data_inicio.date()}) — cobertura completa.")
            resultados.append({
                'ticker': ticker, 'prefixo': prefixo,
                'inicio_candles': data_inicio, 'fim_candles': data_fim,
                'fronteira_tick': data_inicio, 'cobertura': 'completa'
            })
            continue

        # Testa se o FIM tem tick (esperado que sim, mais recente)
        tem_tick_no_fim = testar_tick_disponivel(ticker, data_fim - timedelta(days=2))

        if not tem_tick_no_fim:
            print(f"  ⚠️ Sem tick nem no fim do período — contrato pode não ter tick disponível.")
            resultados.append({
                'ticker': ticker, 'prefixo': prefixo,
                'inicio_candles': data_inicio, 'fim_candles': data_fim,
                'fronteira_tick': None, 'cobertura': 'nenhuma'
            })
            continue

        print(f"  Início sem tick, fim com tick — buscando fronteira exata...")
        fronteira = busca_binaria_fronteira(ticker, data_inicio, data_fim)
        print(f"  ✅ Fronteira encontrada: ticks disponíveis a partir de {fronteira.date()}")

        resultados.append({
            'ticker': ticker, 'prefixo': prefixo,
            'inicio_candles': data_inicio, 'fim_candles': data_fim,
            'fronteira_tick': fronteira, 'cobertura': 'parcial'
        })

mt5_loader.desconectar()

# -----------------------------------------------------------------------------
# RELATÓRIO FINAL
# -----------------------------------------------------------------------------
print("\n" + "=" * 90)
print("  RESUMO FINAL")
print("=" * 90)

df_resultado = pd.DataFrame(resultados)
print(df_resultado.to_string(index=False))

print("\n" + "=" * 90)
print("  Copie e me envie TODO o output acima.")
print("=" * 90)
