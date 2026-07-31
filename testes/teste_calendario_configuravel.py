"""
Testa a Fase 4 do redesenho TradeZella (item F, `spec_redesenho_tradezella.md`):
`diario_trade.detalhe_dias_calendario` — dado por dia pro calendário
configurável (R-Multiple, Day Winrate, pontos totais, além do saldo que já
existia).

Roda direto: `python testes/teste_calendario_configuravel.py`. Sai != 0 se falhar.
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


scratch = Path(tempfile.mkdtemp(prefix="teste_calendario_"))
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
    checar(dt.detalhe_dias_calendario() == {}, "dict vazio sem trades")

    print("\n=== dia com 2 trades, 1 win 1 loss, só 1 com stop preenchido ===")
    dt.registrar_trade({"data_op": "2026-01-05", "ativo": "WIN", "tipo_operacao": "long",
                         "saldo_operacao_reais": 100.0, "stop_pontos": 50.0, "resultado_pontos": 100.0})
    dt.registrar_trade({"data_op": "2026-01-05", "ativo": "WIN", "tipo_operacao": "long",
                         "saldo_operacao_reais": -50.0, "stop_pontos": None, "resultado_pontos": -50.0})
    detalhe = dt.detalhe_dias_calendario()
    dia = detalhe["2026-01-05"]
    checar(dia["n"] == 2, "n = 2 trades no dia")
    checar(dia["saldo"] == 50.0, "saldo do dia = soma dos 2 trades")
    checar(dia["taxa_acerto"] == 0.5, "day winrate = 50% (1 win de 2)")
    checar(dia["r_multiple_medio"] == 2.0,
           f"r_multiple_medio = 2.0 (só o trade COM stop conta, veio {dia['r_multiple_medio']})")
    checar(dia["pontos_total"] == 50.0, f"pontos_total = soma dos resultado_pontos (veio {dia['pontos_total']})")

    print("\n=== dia com NENHUM trade com stop preenchido -> r_multiple_medio None ===")
    dt.registrar_trade({"data_op": "2026-01-06", "ativo": "WDO", "tipo_operacao": "short",
                         "saldo_operacao_reais": 30.0, "stop_pontos": None, "resultado_pontos": 3.0})
    detalhe2 = dt.detalhe_dias_calendario()
    checar(detalhe2["2026-01-06"]["r_multiple_medio"] is None,
           "sem NENHUM stop_pontos no dia -> r_multiple_medio None (nunca 0.0 fabricado)")

    print("\n=== filtro ano/mes ===")
    dt.registrar_trade({"data_op": "2026-02-10", "ativo": "WIN", "tipo_operacao": "long",
                         "saldo_operacao_reais": 10.0})
    detalhe_jan = dt.detalhe_dias_calendario(ano=2026, mes=1)
    checar("2026-02-10" not in detalhe_jan and "2026-01-05" in detalhe_jan,
           "filtro mes=1 exclui fevereiro e inclui janeiro")

finally:
    shutil.rmtree(scratch, ignore_errors=True)

print()
if falhas:
    print(f"### {len(falhas)} FALHA(S):")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("### tudo OK")
