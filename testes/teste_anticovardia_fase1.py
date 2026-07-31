"""
Testa a Fase 1 do redesenho TradeZella (item F, `spec_redesenho_tradezella.md`):
gaps de dado triviais adicionados a `diario_trade.py` antes de qualquer UI —
`profit_factor`/`media_ganho_reais`/`media_perda_reais` em
`calcular_estatisticas`, `sequencia_atual`, `r_multiple_trade`,
`duracao_minutos_trade` e `calcular_anticovardia_score`.

Roda direto: `python testes/teste_anticovardia_fase1.py`. Sai != 0 se falhar.
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


scratch = Path(tempfile.mkdtemp(prefix="teste_anticovardia_"))
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
    checar(dt.sequencia_atual() == {"tipo": None, "trades": 0, "dias": 0},
           "sequencia_atual vazia sem trades")
    checar(dt.calcular_anticovardia_score() == {"score": None, "eixos": {}},
           "anticovardia score vazio sem trades")

    print("\n=== profit_factor / avg win / avg loss ===")
    # 2 wins de +100, 2 losses de -50 -> ganho_total=200, perda_total=-100
    # -> profit_factor = 2.0; media_ganho=100, media_perda=-50
    for saldo in (100.0, 100.0, -50.0, -50.0):
        dt.registrar_trade({"data_op": "2026-01-05", "ativo": "WIN", "tipo_operacao": "long",
                             "saldo_operacao_reais": saldo})
    stats = dt.calcular_estatisticas()
    checar(stats["profit_factor"] == 2.0, f"profit_factor geral = 2.0 (veio {stats['profit_factor']})")
    checar(stats["media_ganho_reais"] == 100.0, "media_ganho_reais = 100.0")
    checar(stats["media_perda_reais"] == -50.0, "media_perda_reais = -50.0")

    print("\n=== sequencia_atual (win/loss consecutivos, quebra em breakeven) ===")
    # sequência até aqui: W, W, L, L -> última sequência é de 2 losses
    seq = dt.sequencia_atual()
    checar(seq["tipo"] == "loss" and seq["trades"] == 2,
           f"sequência atual = 2 losses (veio {seq})")

    dt.registrar_trade({"data_op": "2026-01-06", "ativo": "WIN", "tipo_operacao": "long",
                         "saldo_operacao_reais": 0.0})
    seq2 = dt.sequencia_atual()
    checar(seq2 == {"tipo": None, "trades": 0, "dias": 0},
           f"breakeven no fim zera a sequência (veio {seq2})")

    dt.registrar_trade({"data_op": "2026-01-07", "ativo": "WIN", "tipo_operacao": "long",
                         "saldo_operacao_reais": 80.0})
    dt.registrar_trade({"data_op": "2026-01-08", "ativo": "WIN", "tipo_operacao": "long",
                         "saldo_operacao_reais": 40.0})
    seq3 = dt.sequencia_atual()
    checar(seq3["tipo"] == "win" and seq3["trades"] == 2 and seq3["dias"] == 2,
           f"2 wins em 2 dias distintos -> trades=2, dias=2 (veio {seq3})")

    print("\n=== r_multiple_trade ===")
    checar(dt.r_multiple_trade({"stop_pontos": 150.0, "resultado_pontos": 300.0}) == 2.0,
           "R-multiple = resultado/stop")
    checar(dt.r_multiple_trade({"stop_pontos": 0.0, "resultado_pontos": 300.0}) is None,
           "stop_pontos=0 -> None (nunca ZeroDivisionError)")
    checar(dt.r_multiple_trade({"stop_pontos": None, "resultado_pontos": 300.0}) is None,
           "stop_pontos ausente -> None")

    print("\n=== duracao_minutos_trade ===")
    import pandas as pd
    checar(dt.duracao_minutos_trade({
        "datetime_entrada": pd.Timestamp("2026-01-05 10:00:00"),
        "datetime_saida": pd.Timestamp("2026-01-05 10:15:00"),
    }) == 15.0, "duração = 15 minutos")
    checar(dt.duracao_minutos_trade({"datetime_entrada": None, "datetime_saida": None}) is None,
           "timestamps ausentes -> None")

    print("\n=== calcular_anticovardia_score — 6 eixos presentes, score = média ===")
    resultado = dt.calcular_anticovardia_score()
    checar(resultado["score"] is not None, "score calculado com trades reais presentes")
    eixos_esperados = {"win_rate", "profit_factor", "avg_win_loss", "recovery_factor",
                       "max_drawdown", "consistencia"}
    checar(set(resultado["eixos"].keys()) == eixos_esperados,
           f"6 eixos presentes (veio {sorted(resultado['eixos'].keys())})")
    checar(abs(resultado["score"] - sum(resultado["eixos"].values()) / 6) < 1e-9,
           "score = média aritmética dos 6 eixos")
    checar(all(0.0 <= v <= 100.0 for v in resultado["eixos"].values()),
           f"todos os eixos entre 0 e 100 (veio {resultado['eixos']})")

finally:
    shutil.rmtree(scratch, ignore_errors=True)

print()
if falhas:
    print(f"### {len(falhas)} FALHA(S):")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("### tudo OK")
