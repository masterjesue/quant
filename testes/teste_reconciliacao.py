"""Testes de `reconciliar_com_mt5` — bug real 2026-07-27.

Achado comparando o Diario com o relatorio de performance do ProfitPro
(46 operacoes contra 42): o casamento trade<->posicao e por `(ativo, data)` e
consome UMA posicao por trade. Um par de hedge, porem, sao DUAS posicoes do
MT5 que viram UM trade — a segunda perna sobrava como
"sem_correspondencia_diario" pra sempre, enchendo a lista PENDENTE de
operacoes que JA estavam registradas. Duas delas chegaram a ser finalizadas de
novo e geraram trades duplicados.

Os 4 caminhos de persistencia sao isolados num scratch. Producao nunca e
tocada (ver CLAUDE.md "Testing conventions").
"""
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

SCRATCH = Path(__file__).parent / "_scratch_reconc"
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
import mt5_loader  # noqa: E402

falhas = []


def checar(nome, ok, detalhe=""):
    print(("  PASS  " if ok else "  FALHA ") + nome + ("" if ok else "  " + str(detalhe)))
    if not ok:
        falhas.append(nome)


ONTEM = (datetime.now() - timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)


def deal(position_id, ticket, entry, symbol, tipo, preco, volume, profit, quando):
    """Uma perna (deal) crua, no formato que `agrupar_posicoes` espera."""
    return {
        "position_id": position_id, "ticket": ticket, "entry": entry,
        "symbol": symbol, "raiz_contrato": symbol[:3], "tipo": tipo,
        "preco": preco, "volume": volume, "profit": profit, "time": quando,
    }


def montar_deals(linhas):
    return pd.DataFrame(linhas)


print("\n=== TESTE 1 — par de hedge nao pode deixar perna fantasma pendente ===")
# Cenario real: 2 posicoes opostas do MT5 (as 2 pernas) viraram 1 trade so.
deals = montar_deals([
    deal(1001, 90001, 0, "WDOQ26", "compra", 5100.0, 1.0, 0.0, ONTEM),
    deal(1001, 90002, 1, "WDOQ26", "venda", 5105.0, 1.0, 50.0, ONTEM + timedelta(minutes=30)),
    deal(1002, 90003, 0, "WDOQ26", "venda", 5105.0, 1.0, 0.0, ONTEM + timedelta(minutes=30)),
    deal(1002, 90004, 1, "WDOQ26", "compra", 5105.0, 1.0, 0.0, ONTEM + timedelta(minutes=60)),
])
mt5_loader.buscar_execucoes_periodo = lambda i, f: deals

# 1 trade no diario representando o par
dt.registrar_trade({
    "ativo": "WDO", "contrato": "WDOQ26", "tipo_operacao": "long", "lote": 1.0,
    "preco_entrada": 5100.0, "preco_saida": 5105.0, "data_op": ONTEM.date(),
    "resultado_pontos": 5.0, "saldo_operacao_reais": 50.0, "conta": "real",
})
id_trade = int(persistencia.carregar_diario()["id_trade"].iloc[0])
# ...e a lapide ligando as DUAS pernas a ele (o que finalizar_par_hedge faz)
dt._marcar_tickets_finalizados([1001, 1002], id_trade)

res = dt.reconciliar_com_mt5(dias_atras=30)
pend = res[res["mt5_match_status"] == "sem_correspondencia_diario"]
checar("nenhuma perna vira pendencia fantasma", len(pend) == 0,
       f"pendencias={list(pend['mt5_position_id'])}")
checar("as 2 pernas ficam 'ok'", (res["mt5_match_status"] == "ok").sum() == 2,
       res["mt5_match_status"].value_counts().to_dict())
checar("ambas apontam pro mesmo trade",
       set(res[res["mt5_match_status"] == "ok"]["id_trade"]) == {id_trade},
       list(res[res["mt5_match_status"] == "ok"]["id_trade"]))
checar("nao aparece na lista PENDENTE do Historico",
       len(dt.listar_pendencias_historico()) == 0)

print("\n=== TESTE 2 — posicao REALMENTE sem registro continua sendo reportada ===")
shutil.rmtree(SCRATCH); SCRATCH.mkdir()
deals2 = montar_deals([
    deal(2001, 90011, 0, "WINQ26", "compra", 130000.0, 1.0, 0.0, ONTEM),
    deal(2001, 90012, 1, "WINQ26", "venda", 130150.0, 1.0, 30.0, ONTEM + timedelta(minutes=10)),
])
mt5_loader.buscar_execucoes_periodo = lambda i, f: deals2
res2 = dt.reconciliar_com_mt5(dias_atras=30)
pend2 = res2[res2["mt5_match_status"] == "sem_correspondencia_diario"]
checar("operacao sem trade nenhum ainda aparece", len(pend2) == 1,
       res2["mt5_match_status"].value_counts().to_dict())
checar("e chega na lista PENDENTE do Historico",
       len(dt.listar_pendencias_historico()) == 1)

print("\n=== TESTE 3 — trade avulso (1 ticket = 1 trade) segue reconciliando ===")
shutil.rmtree(SCRATCH); SCRATCH.mkdir()
deals3 = montar_deals([
    deal(3001, 90021, 0, "WINQ26", "compra", 130000.0, 1.0, 0.0, ONTEM),
    deal(3001, 90022, 1, "WINQ26", "venda", 130150.0, 1.0, 30.0, ONTEM + timedelta(minutes=10)),
])
mt5_loader.buscar_execucoes_periodo = lambda i, f: deals3
dt.registrar_trade({
    "ativo": "WIN", "contrato": "WINQ26", "tipo_operacao": "long", "lote": 1.0,
    "preco_entrada": 130000.0, "preco_saida": 130150.0, "data_op": ONTEM.date(),
    "resultado_pontos": 150.0, "saldo_operacao_reais": 30.0, "conta": "real",
})
id3 = int(persistencia.carregar_diario()["id_trade"].iloc[0])
dt._marcar_tickets_finalizados([3001], id3)
res3 = dt.reconciliar_com_mt5(dias_atras=30)
checar("ticket avulso fica 'ok'", (res3["mt5_match_status"] == "ok").sum() == 1,
       res3["mt5_match_status"].value_counts().to_dict())
checar("sem pendencia", len(dt.listar_pendencias_historico()) == 0)

print("\n=== TESTE 4 — data da pendencia vem da ENTRADA, nao da saida estimada ===")
# Bug real 2026-07-28: quando o fechamento real nao e achado no MT5, o sync
# grava saida ESTIMADA com datetime_saida = agora. Usando a saida, uma
# operacao de ontem aparecia com a data de HOJE — e ia "andando" de dia em dia
# enquanto nao fosse registrada, bem na hora de conferir contra o relatorio da
# corretora POR DATA.
shutil.rmtree(SCRATCH); SCRATCH.mkdir()
ONTEM_DIA = (datetime.now() - timedelta(days=1)).date()
linha = {c: None for c in dt.COLUNAS_POSICOES_ABERTAS}
linha.update({
    "id_posicao": 4242, "ativo": "WDO", "contrato": "WDOQ26",
    "tipo_operacao": "long", "volume": 1.0, "preco_entrada": 5100.0,
    "datetime_entrada": pd.Timestamp(ONTEM),          # entrou ONTEM
    "status": dt.STATUS_POSICAO_FECHADA_PENDENTE,
    "preco_saida": 5105.0,
    "datetime_saida": pd.Timestamp(datetime.now()),   # saida ESTIMADA = agora
    "saida_estimada": True,
    "pnl_pontos_final": 5.0, "pnl_reais_final": 50.0,
    "criado_em": datetime.now(), "atualizado_em": datetime.now(),
})
persistencia.salvar_posicoes_abertas(
    pd.DataFrame([linha], columns=dt.COLUNAS_POSICOES_ABERTAS))
p = dt.listar_pendencias_historico()
checar("pendencia aparece", len(p) == 1, len(p))
if len(p) == 1:
    checar("data = dia da ENTRADA (nao hoje)", p.iloc[0]["data"] == ONTEM_DIA,
           f"deu {p.iloc[0]['data']}, esperado {ONTEM_DIA}")

print("\n" + "=" * 60)
if falhas:
    print(f"  {len(falhas)} FALHA(S): {falhas}")
    sys.exit(1)
print("  TODOS OS TESTES PASSARAM")
