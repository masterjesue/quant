"""
Testa `dt.analise_estrategia` — dado do bloco "Análise de Estratégia"
(2026-07-30, "upgrade diario.docx"): melhor estratégia por janela/direção/
ativo/categoria, mais Estratégia Vencedora/Perdedora geral.

O que precisa valer:
  1. só entra no páreo quem tem >= min_amostra trades NAQUELE recorte —
     "melhor por 1 trade de sorte" não pode aparecer
  2. escolhe corretamente a estratégia de MAIOR saldo em cada grupo
  3. categoria é lida do cadastro (join por slug), não do trade
  4. vencedora/perdedora geral = maior/menor saldo entre quem tem amostra
  5. sem trades reais -> tudo vazio, sem estourar

Roda direto: `python testes/teste_analise_estrategia.py`. Sai != 0 se falhar.
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import persistencia  # noqa: E402
import diario_trade as dt  # noqa: E402

falhas = []


def checar(cond, msg):
    print(("  OK   " if cond else "  FALHA") + f"  {msg}")
    if not cond:
        falhas.append(msg)


scratch = Path(tempfile.mkdtemp(prefix="teste_analise_estrategia_"))
persistencia._caminho_diario = lambda: scratch / "diario_trades.parquet"
persistencia._caminho_diario_lock = lambda: scratch / ".diario.lock"
persistencia._caminho_posicoes_abertas = lambda: scratch / "posicoes_abertas.parquet"
persistencia._caminho_posicoes_abertas_lock = lambda: scratch / ".posicoes.lock"
persistencia._caminho_tickets_finalizados = lambda: scratch / "tickets_finalizados.parquet"
persistencia._caminho_tickets_ignorados = lambda: scratch / "tickets_ignorados.parquet"
persistencia._caminho_reconciliacao = lambda: scratch / "reconciliacao.parquet"
persistencia._caminho_estrategias = lambda: scratch / "diario_estrategias.parquet"
persistencia._caminho_estrategias_lock = lambda: scratch / ".estrategias.lock"

try:
    print("\n=== sem trades reais ===")
    vazio = dt.analise_estrategia()
    checar(vazio["vencedora"] is None and vazio["perdedora"] is None, "sem trades -> vencedora/perdedora None")
    checar(vazio["melhor_por_janela"] == {}, "sem trades -> melhor_por_janela vazio")

    print("\n=== cadastro: 2 estratégias com categorias diferentes ===")
    id_a = dt.criar_estrategia("Setup A", categoria="tendencia")
    id_b = dt.criar_estrategia("Setup B", categoria="reversao")
    dt.editar_estrategia(id_a, slug="setup_a")
    dt.editar_estrategia(id_b, slug="setup_b")

    print("\n=== massa de trades ===")
    # setup_a: 4 trades na janela almoco, saldo total +400 (media +100)
    for saldo in (100, 100, 100, 100):
        dt.registrar_trade({"data_op": "2026-01-05", "ativo": "WIN", "tipo_operacao": "long",
                            "janela_pregao": "almoco", "estrategia_tags": ["setup_a"],
                            "saldo_operacao_reais": saldo})
    # setup_b: 4 trades na janela almoco, saldo total +40 (pior que setup_a)
    for saldo in (10, 10, 10, 10):
        dt.registrar_trade({"data_op": "2026-01-06", "ativo": "WIN", "tipo_operacao": "long",
                            "janela_pregao": "almoco", "estrategia_tags": ["setup_b"],
                            "saldo_operacao_reais": saldo})
    # setup_b sozinho na janela prime, só 2 trades (abaixo do min_amostra=3)
    for saldo in (500, 500):
        dt.registrar_trade({"data_op": "2026-01-07", "ativo": "WDO", "tipo_operacao": "short",
                            "janela_pregao": "prime", "estrategia_tags": ["setup_b"],
                            "saldo_operacao_reais": saldo})
    # setup_a sozinho no WDO, 3 trades — deve aparecer em melhor_por_ativo[WDO]
    for saldo in (-10, -10, -10):
        dt.registrar_trade({"data_op": "2026-01-08", "ativo": "WDO", "tipo_operacao": "short",
                            "janela_pregao": "tarde", "estrategia_tags": ["setup_a"],
                            "saldo_operacao_reais": saldo})

    resultado = dt.analise_estrategia(min_amostra=3)

    print("\n=== melhor por janela ===")
    checar("almoco" in resultado["melhor_por_janela"], "janela 'almoco' tem uma melhor estratégia")
    checar(resultado["melhor_por_janela"]["almoco"]["estrategia"] == "setup_a",
           f"setup_a (saldo +400) vence setup_b (+40) no almoço, veio "
           f"{resultado['melhor_por_janela'].get('almoco')}")
    checar("prime" not in resultado["melhor_por_janela"],
           "janela 'prime' NÃO aparece (setup_b só tem 2 trades ali, abaixo do min_amostra=3)")

    print("\n=== melhor por ativo ===")
    checar(resultado["melhor_por_ativo"]["WIN"]["estrategia"] == "setup_a",
           "WIN: setup_a vence (mais volume e saldo positivo)")
    # No WDO só setup_a atinge min_amostra (3 trades, saldo -30); setup_b no
    # WDO tem só 2 trades (prime) -> não conta ali.
    checar(resultado["melhor_por_ativo"]["WDO"]["estrategia"] == "setup_a",
           f"WDO: só setup_a tem amostra suficiente, mesmo com saldo negativo — "
           f"veio {resultado['melhor_por_ativo'].get('WDO')}")

    print("\n=== melhor por categoria ===")
    checar(resultado["melhor_por_categoria"].get("tendencia", {}).get("estrategia") == "setup_a",
           "categoria 'tendencia' (só setup_a) aponta pra setup_a")
    # setup_b é a ÚNICA estratégia em 'reversao', mas o total de trades dela
    # NAQUELA categoria (4 no almoço + 2 no prime = 6) já passa o
    # min_amostra=3 — não é "1 trade de sorte", é > o mínimo exigido.
    checar(resultado["melhor_por_categoria"].get("reversao", {}).get("estrategia") == "setup_b",
           f"categoria 'reversao' (setup_b, 6 trades ao todo) aponta pra setup_b — "
           f"veio {resultado['melhor_por_categoria'].get('reversao')}")
    checar(resultado["melhor_por_categoria"]["reversao"]["n"] == 6,
           "conta os 6 trades de setup_b sob 'reversao' (4 do almoço + 2 do prime)")

    print("\n=== vencedora / perdedora geral ===")
    # saldo total: setup_a = 400 (almoco) + (-30) (wdo tarde) = 370, n=7
    # setup_b = 40 (almoco) + 1000 (prime) = 1040, n=6
    checar(resultado["vencedora"]["estrategia"] == "setup_b",
           f"setup_b tem o maior saldo total (1040 vs 370) — veio {resultado['vencedora']}")
    checar(resultado["perdedora"]["estrategia"] == "setup_a",
           f"setup_a tem o menor saldo total entre os 2 — veio {resultado['perdedora']}")

    print("\n=== amostra insuficiente em TODOS os grupos de um recorte ===")
    resultado_amostra_alta = dt.analise_estrategia(min_amostra=100)
    checar(resultado_amostra_alta["melhor_por_janela"] == {},
           "com min_amostra=100 (ninguém bate), melhor_por_janela vem vazio, não estoura")
    checar(resultado_amostra_alta["vencedora"] is None,
           "com min_amostra=100, vencedora vem None (não inventa resposta)")

finally:
    shutil.rmtree(scratch, ignore_errors=True)

print()
if falhas:
    print(f"### {len(falhas)} FALHA(S):")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("### tudo OK")
