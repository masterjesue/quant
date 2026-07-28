"""Testes de `mt5_loader.agrupar_posicoes` — revisao do motor 2026-07-27.

Achados corrigidos aqui:
  1. volume vinha SO do primeiro fill de entrada — uma posicao de 2 contratos
     preenchida como 1+1 virava volume 1 e o resultado saia pela metade;
  2. preco de entrada/saida vinha de UM fill, nao da media ponderada;
  3. `iloc[0]`/`iloc[-1]` sem ordenar por tempo — `history_deals_get` nao
     garante ordem.

Os testes 1-2 travam o comportamento antigo (caso simples 1-in/1-out) pra
garantir que a correcao nao mudou nada onde ja estava certo.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mt5_loader  # noqa: E402

falhas = []


def checar(nome, ok, detalhe=""):
    print(("  PASS  " if ok else "  FALHA ") + nome + ("" if ok else "  " + str(detalhe)))
    if not ok:
        falhas.append(nome)


T0 = datetime(2026, 7, 10, 11, 0, 0)


def d(ticket, position_id, entry, tipo, preco, volume, profit, minutos):
    return {
        "ticket": ticket, "time": T0 + timedelta(minutes=minutos),
        "symbol": "WINQ26", "raiz_contrato": "WIN", "tipo": tipo,
        "entry": entry, "volume": volume, "preco": preco,
        "profit": profit, "position_id": position_id, "comment": "",
    }


print("\n=== TESTE 1 — caso simples 1-in/1-out nao muda ===")
df = pd.DataFrame([
    d(1, 100, 0, "compra", 130000.0, 1.0, 0.0, 0),
    d(2, 100, 1, "venda", 130150.0, 1.0, 30.0, 10),
])
r = mt5_loader.agrupar_posicoes(df)
checar("1 posicao", len(r) == 1)
p = r.iloc[0]
checar("volume 1", p["volume"] == 1.0, p["volume"])
checar("entrada 130000", p["preco_entrada"] == 130000.0, p["preco_entrada"])
checar("saida 130150", p["preco_saida"] == 130150.0, p["preco_saida"])
checar("profit do MT5 preservado (30)", p["profit_total"] == 30.0, p["profit_total"])

print("\n=== TESTE 2 — entrada PARCIAL (1+1) soma volume e faz media ===")
df = pd.DataFrame([
    d(1, 200, 0, "compra", 130000.0, 1.0, 0.0, 0),
    d(2, 200, 0, "compra", 130100.0, 1.0, 0.0, 1),   # segundo fill da MESMA entrada
    d(3, 200, 1, "venda", 130200.0, 2.0, 40.0, 10),
])
r = mt5_loader.agrupar_posicoes(df)
p = r.iloc[0]
checar("volume somado = 2", p["volume"] == 2.0, p["volume"])
checar("entrada = media ponderada 130050", p["preco_entrada"] == 130050.0, p["preco_entrada"])

print("\n=== TESTE 3 — saida PARCIAL faz media ponderada ===")
df = pd.DataFrame([
    d(1, 300, 0, "compra", 130000.0, 2.0, 0.0, 0),
    d(2, 300, 1, "venda", 130100.0, 1.0, 20.0, 10),
    d(3, 300, 1, "venda", 130300.0, 1.0, 60.0, 11),
])
r = mt5_loader.agrupar_posicoes(df)
p = r.iloc[0]
checar("saida = media ponderada 130200", p["preco_saida"] == 130200.0, p["preco_saida"])
checar("profit = soma dos fills (80)", p["profit_total"] == 80.0, p["profit_total"])

print("\n=== TESTE 4 — deals FORA DE ORDEM ainda dao entrada/saida certas ===")
df = pd.DataFrame([
    d(3, 400, 1, "venda", 130150.0, 1.0, 30.0, 10),   # saida vem primeiro na lista
    d(1, 400, 0, "compra", 130000.0, 1.0, 0.0, 0),
])
r = mt5_loader.agrupar_posicoes(df)
p = r.iloc[0]
checar("entrada correta mesmo desordenado", p["preco_entrada"] == 130000.0, p["preco_entrada"])
checar("saida correta mesmo desordenado", p["preco_saida"] == 130150.0, p["preco_saida"])
checar("hora de entrada = a mais antiga", p["datetime_entrada"] == T0, p["datetime_entrada"])

print("\n=== TESTE 5 — OUT_BY (profit 0) recalcula com o VOLUME TOTAL ===")
# Hedge cruzado pela corretora: MT5 devolve profit 0 nas 2 pernas. Com entrada
# parcial 1+1, o recalculo tem que usar volume 2 — antes usava 1 e saia metade.
df = pd.DataFrame([
    d(1, 500, 0, "compra", 130000.0, 1.0, 0.0, 0),
    d(2, 500, 0, "compra", 130000.0, 1.0, 0.0, 1),
    d(3, 500, mt5_loader.mt5.DEAL_ENTRY_OUT_BY, "venda", 130150.0, 2.0, 0.0, 10),
])
r = mt5_loader.agrupar_posicoes(df)
p = r.iloc[0]
# +150 pts * 0.20 * 2 contratos = +60
checar("recalculo usa volume 2 (=R$60, nao R$30)",
       abs(float(p["profit_total"]) - 60.0) < 0.01, p["profit_total"])

print("\n=== TESTE 6 — posicao sem perna IN visivel e descartada ===")
df = pd.DataFrame([
    d(1, 600, 1, "venda", 130150.0, 1.0, 30.0, 10),
])
r = mt5_loader.agrupar_posicoes(df)
checar("descarta posicao sem entrada", len(r) == 0, len(r))

print("\n" + "=" * 60)
if falhas:
    print(f"  {len(falhas)} FALHA(S): {falhas}")
    sys.exit(1)
print("  TODOS OS TESTES PASSARAM")
