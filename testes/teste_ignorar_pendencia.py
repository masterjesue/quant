"""Testes de `ignorar_pendencia` — 2026-07-27.

Falta funcional achada comparando o Diario com o relatorio da corretora: a
reconciliacao reporta como pendente TODA operacao real do MT5 sem trade
correspondente. Se o usuario conscientemente nao quer aquela operacao no
Diario (ex: o relatorio de performance e filtrado por CARTEIRA, e ela esta
fora da carteira acompanhada), ela reaparecia a cada ciclo, pra sempre — e a
unica forma de calar era registra-la, ou seja, aceitar o que se queria
recusar.

Os 5 caminhos de persistencia sao isolados num scratch.
"""
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

SCRATCH = Path(__file__).parent / "_scratch_ignorar"
if SCRATCH.exists():
    shutil.rmtree(SCRATCH)
SCRATCH.mkdir(parents=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import persistencia  # noqa: E402

persistencia._caminho_diario = lambda: SCRATCH / "diario_trades.parquet"
persistencia._caminho_diario_lock = lambda: SCRATCH / ".diario.lock"
persistencia._caminho_posicoes_abertas = lambda: SCRATCH / "posicoes_abertas.parquet"
persistencia._caminho_posicoes_abertas_lock = lambda: SCRATCH / ".posicoes.lock"
persistencia._caminho_tickets_finalizados = lambda: SCRATCH / "tickets_finalizados.parquet"
persistencia._caminho_tickets_ignorados = lambda: SCRATCH / "tickets_ignorados.parquet"
persistencia._caminho_reconciliacao = lambda: SCRATCH / "diario_reconciliacao.parquet"

import diario_trade as dt  # noqa: E402

falhas = []


def checar(nome, ok, detalhe=""):
    print(("  PASS  " if ok else "  FALHA ") + nome + ("" if ok else "  " + str(detalhe)))
    if not ok:
        falhas.append(nome)


def semear(pid, ativo="WDO", data="2026-07-03", saldo=-5.0):
    base = {c: None for c in dt.COLUNAS_RECONCILIACAO}
    base.update({
        "id_trade": None, "mt5_position_id": float(pid),
        "mt5_match_status": "sem_correspondencia_diario",
        "ativo": ativo, "data": pd.Timestamp(data).date(), "saldo_estimado": saldo,
        "contrato": ativo + "Q26", "tipo_operacao": "long", "volume": 1.0,
        "preco_entrada": 5206.0, "preco_saida": 5205.5,
        "datetime_entrada": pd.Timestamp(data + " 12:00:00"),
        "datetime_saida": pd.Timestamp(data + " 12:30:00"),
    })
    return base


print("\n=== TESTE 1 — ticket ignorado some da lista PENDENTE ===")
persistencia.salvar_reconciliacao(
    pd.DataFrame([semear(306278318)], columns=dt.COLUNAS_RECONCILIACAO))
checar("aparece antes de ignorar", len(dt.listar_pendencias_historico()) == 1)
checar("ignorar_pendencia devolve True", dt.ignorar_pendencia(306278318, "teste") is True)
checar("sumiu da lista", len(dt.listar_pendencias_historico()) == 0)

print("\n=== TESTE 2 — nao volta na reconciliacao seguinte ===")
# Reescreve o cache do zero, como faria um ciclo novo lendo o MT5.
persistencia.salvar_reconciliacao(
    pd.DataFrame([semear(306278318)], columns=dt.COLUNAS_RECONCILIACAO))
checar("continua fora mesmo com o cache reescrito",
       len(dt.listar_pendencias_historico()) == 0)

print("\n=== TESTE 3 — ignorado nao pode virar rascunho ===")
checar("materializar recusa ticket ignorado",
       dt.materializar_pendencia_como_rascunho(306278318) is None)

print("\n=== TESTE 4 — idempotente ===")
antes = len(persistencia.carregar_tickets_ignorados())
dt.ignorar_pendencia(306278318, "de novo")
depois = len(persistencia.carregar_tickets_ignorados())
checar("ignorar 2x nao duplica linha", antes == depois, f"{antes} -> {depois}")

print("\n=== TESTE 5 — par de hedge marca as DUAS pernas ===")
shutil.rmtree(SCRATCH); SCRATCH.mkdir()
checar("aceita id composto", dt.ignorar_pendencia("111-222", "par") is True)
ign = set(persistencia.carregar_tickets_ignorados()["id_posicao"])
checar("as 2 pernas ficaram marcadas", {111, 222} <= ign, ign)

print("\n=== TESTE 6 — pendencia NAO ignorada continua aparecendo ===")
shutil.rmtree(SCRATCH); SCRATCH.mkdir()
persistencia.salvar_reconciliacao(
    pd.DataFrame([semear(999888, ativo="WIN")], columns=dt.COLUNAS_RECONCILIACAO))
dt.ignorar_pendencia(111111, "outro ticket qualquer")
checar("ignorar um ticket nao engole os outros",
       len(dt.listar_pendencias_historico()) == 1)

print("\n=== TESTE 7 — id invalido nao quebra nem grava lixo ===")
checar("id nao numerico devolve False", dt.ignorar_pendencia("abc") is False)

print("\n" + "=" * 60)
if falhas:
    print(f"  {len(falhas)} FALHA(S): {falhas}")
    sys.exit(1)
print("  TODOS OS TESTES PASSARAM")
