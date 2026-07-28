"""Testes dos fixes de 2026-07-27 — duplicacao, pendencia falsa, hedge cross-day.

TODOS os 6 caminhos de persistencia (4 parquets + 2 lockfiles) apontam pra um
scratch dir. Producao NUNCA e tocada. Ver CLAUDE.md "Testing conventions".
"""
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

SCRATCH = Path(__file__).parent / "_scratch_parquets"
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
persistencia._caminho_reconciliacao = lambda: SCRATCH / "diario_reconciliacao.parquet"

import diario_trade as dt  # noqa: E402

falhas = []


def checar(nome, condicao, detalhe=""):
    if condicao:
        print(f"  PASS  {nome}")
    else:
        print(f"  FAIL  {nome}  {detalhe}")
        falhas.append(nome)


def reset():
    for f in SCRATCH.glob("*.parquet"):
        f.unlink()


def semear_reconciliacao(linhas):
    """Grava linhas 'sem_correspondencia_diario' no cache de reconciliacao."""
    df = pd.DataFrame(linhas, columns=dt.COLUNAS_RECONCILIACAO)
    persistencia.salvar_reconciliacao(df)


def linha_reconc(pid, ativo="WDO", data="2026-07-23", saldo=30.0,
                 pe=5098.5, ps=5095.5, tipo="short"):
    base = {c: None for c in dt.COLUNAS_RECONCILIACAO}
    base.update({
        "id_trade": None, "mt5_position_id": float(pid),
        "mt5_ticket_entrada": 1, "mt5_ticket_saida": 2,
        "mt5_divergencia_reais": None, "confianca": None,
        "mt5_match_status": "sem_correspondencia_diario",
        "ativo": ativo, "data": pd.Timestamp(data).date(), "saldo_estimado": saldo,
        "contrato": ativo + "Q26", "tipo_operacao": tipo, "volume": 1.0,
        "preco_entrada": pe, "preco_saida": ps,
        "datetime_entrada": pd.Timestamp(data + " 12:28:13"),
        "datetime_saida": pd.Timestamp(data + " 13:34:08"),
    })
    return base


print("\n=== TESTE 1 — botao check clicado 5x deve gravar 1 trade so ===")
reset()
TICKET = 308536027
semear_reconciliacao([linha_reconc(TICKET)])

ids_trade = []
for i in range(5):
    pid = dt.materializar_pendencia_como_rascunho(TICKET)
    if pid is not None:
        ids_trade.append(dt.finalizar_posicao(pid, {"conta": "real"}))
    else:
        ids_trade.append(None)

diario = persistencia.carregar_diario()
n_trades = 0 if diario is None else len(diario)
checar("gravou exatamente 1 trade", n_trades == 1, f"(gravou {n_trades})")
checar("cliques 2-5 recusados (None)", ids_trade[1:] == [None] * 4, f"(retornos={ids_trade})")

pend = dt.listar_pendencias_historico()
checar("pendencia sumiu da lista apos finalizar", len(pend) == 0, f"(ainda lista {len(pend)})")

print("\n=== TESTE 2 — pendencia ja finalizada ha dias nao pode aparecer ===")
reset()
# Reproduz o caso real: 307609756 virou o trade #147 em 2026-07-16, mas o cache
# de reconciliacao ainda listava ele como pendente em 2026-07-27.
semear_reconciliacao([linha_reconc(307609756, ativo="WDO", data="2026-07-15", saldo=80.0)])
dt._marcar_tickets_finalizados([307609756], 147)
pend = dt.listar_pendencias_historico()
checar("ticket com lapide nao e listado", len(pend) == 0, f"(listou {list(pend['id_editar'])})")
checar("materializar recusa ticket com lapide",
       dt.materializar_pendencia_como_rascunho(307609756) is None)

print("\n=== TESTE 3 — hedge so pareia no mesmo dia de pregao ===")


def pos(pid, tipo, dt_entrada, preco, status=None):
    linha = {c: None for c in dt.COLUNAS_POSICOES_ABERTAS}
    linha.update({
        "id_posicao": pid, "ativo": "WDO", "contrato": "WDOQ26",
        "tipo_operacao": tipo, "volume": 1.0, "preco_entrada": preco,
        "datetime_entrada": pd.Timestamp(dt_entrada),
        "status": status or dt.STATUS_POSICAO_ABERTA,
        "criado_em": datetime.now(), "atualizado_em": datetime.now(),
    })
    return linha


# Caso real do trade #167: WDO short de 22/07 vs WDO long de 23/07.
df_cross = pd.DataFrame([
    pos(308391086, "short", "2026-07-22 11:55:15", 5073.0),
    pos(308536474, "long", "2026-07-23 12:00:00", 5095.5),
], columns=dt.COLUNAS_POSICOES_ABERTAS)
pares_cross = dt.detectar_pares_hedge(df_cross)
checar("dias diferentes NAO formam par", len(pares_cross) == 0,
       f"(formou {len(pares_cross)}: {pares_cross})")

df_mesmo = pd.DataFrame([
    pos(1001, "short", "2026-07-23 11:55:15", 5100.0),
    pos(1002, "long", "2026-07-23 12:00:00", 5095.0),
], columns=dt.COLUNAS_POSICOES_ABERTAS)
pares_mesmo = dt.detectar_pares_hedge(df_mesmo)
checar("mesmo dia FORMA par", len(pares_mesmo) == 1, f"(formou {len(pares_mesmo)})")
if pares_mesmo:
    p = pares_mesmo[0]
    # short 5100 -> saida 5095 = +5 pts * 10 R$/pt * 1 = +50
    checar("pnl do par correto (+50)", abs(p["pnl_reais"] - 50.0) < 0.01,
           f"(deu {p['pnl_reais']})")

print("\n=== TESTE 4 — par de hedge tambem nao pode ser finalizado 2x ===")
reset()
persistencia.salvar_posicoes_abertas(df_mesmo)
id1 = dt.finalizar_par_hedge(1001, 1002, {"conta": "real"})
id2 = dt.finalizar_par_hedge(1001, 1002, {"conta": "real"})
checar("1a finalizacao do par funciona", id1 is not None, f"(deu {id1})")
checar("2a finalizacao do par recusada", id2 is None, f"(deu {id2})")
diario = persistencia.carregar_diario()
checar("par gerou 1 trade so", (0 if diario is None else len(diario)) == 1)

print("\n=== TESTE 5 — posicao genuinamente pendente AINDA aparece ===")
reset()
semear_reconciliacao([linha_reconc(999111, ativo="WIN", data="2026-07-10", saldo=-30.0)])
pend = dt.listar_pendencias_historico()
checar("pendencia real nao foi engolida pelo fix", len(pend) == 1,
       f"(listou {len(pend)})")

print("\n" + "=" * 60)
if falhas:
    print(f"  {len(falhas)} FALHA(S): {falhas}")
    sys.exit(1)
print("  TODOS OS TESTES PASSARAM")
