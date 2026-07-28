"""Testes de `_fundir_pernas_fechadas_espelhadas` — bug real 2026-07-27.

Conta hedging: uma operacao vira DUAS posicoes no MT5. Quando a corretora
cruza as duas pernas (DEAL_ENTRY_OUT_BY), AS DUAS ganham preco de saida e
escapavam de `_parear_hedge_deals` (que so fundia pernas SEM saida). Como o
MT5 devolve profit=0 nesse cruzamento, `agrupar_posicoes` recalculava o
resultado de CADA perna separadamente — uma operacao real de -R$30 virava
duas posicoes de -R$30, e a perna sem trade ficava para sempre como
"operacao sem registro no diario" (as "ordens fantasmas de hedge").

Reproduz os tickets REAIS achados no diario do Jesue.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import diario_trade as dt  # noqa: E402

falhas = []


def checar(nome, ok, detalhe=""):
    print(("  PASS  " if ok else "  FALHA ") + nome + ("" if ok else "  " + str(detalhe)))
    if not ok:
        falhas.append(nome)


COLUNAS = ["position_id", "raiz_contrato", "contrato", "tipo", "preco_entrada",
           "preco_saida", "datetime_entrada", "datetime_saida", "volume",
           "profit_total", "tickets_entrada", "tickets_saida"]


def pos(pid, contrato, tipo, pe, ps, entrada, saida, vol=1.0, profit=0.0):
    return {
        "position_id": pid, "raiz_contrato": contrato[:3], "contrato": contrato,
        "tipo": tipo, "preco_entrada": pe, "preco_saida": ps,
        "datetime_entrada": pd.Timestamp(entrada), "datetime_saida": pd.Timestamp(saida),
        "volume": vol, "profit_total": profit,
        "tickets_entrada": [pid * 10], "tickets_saida": [pid * 10 + 1],
    }


def montar(linhas):
    return pd.DataFrame(linhas, columns=COLUNAS)


print("\n=== TESTE 1 — caso REAL do diario (WIN 03/07, tickets 306271796/306272514) ===")
# As 2 pernas espelhadas: entrada de uma == saida da outra.
df = montar([
    pos(306271796, "WINQ26", "venda", 176775.0, 176925.0,
        "2026-07-03 12:03:03", "2026-07-03 12:30:00"),
    pos(306272514, "WINQ26", "compra", 176925.0, 176775.0,
        "2026-07-03 12:03:03", "2026-07-03 12:30:00"),
])
r = dt._parear_hedge_deals(df)
checar("as 2 pernas viram 1 posicao so", len(r) == 1, f"len={len(r)}")
if len(r) == 1:
    # short 176775 -> 176925 = -150 pts * 0.20 = -30 (o valor REAL da operacao)
    checar("resultado e o da operacao real (-R$30), nao -60",
           abs(float(r.iloc[0]["profit_total"]) - (-30.0)) < 0.01,
           f"profit={r.iloc[0]['profit_total']}")

print("\n=== TESTE 2 — caso REAL WDO 03/07 (306278318/306331658) ===")
df = montar([
    pos(306278318, "WDOQ26", "compra", 5206.0, 5205.5,
        "2026-07-03 12:30:00", "2026-07-03 12:45:00"),
    pos(306331658, "WDOQ26", "venda", 5205.5, 5206.0,
        "2026-07-03 12:30:00", "2026-07-03 12:45:00"),
])
r = dt._parear_hedge_deals(df)
checar("WDO espelhado tambem funde", len(r) == 1, f"len={len(r)}")
if len(r) == 1:
    # long 5206 -> 5205.5 = -0.5 pts * 10 = -5
    checar("resultado -R$5 (nao -10)",
           abs(float(r.iloc[0]["profit_total"]) - (-5.0)) < 0.01,
           f"profit={r.iloc[0]['profit_total']}")

print("\n=== TESTE 3 (NEGATIVO) — 2 round-trips independentes NAO podem fundir ===")
# Mesmo dia, mesmo contrato, mesmo volume, direcoes opostas — mas precos NAO
# espelhados. Sao duas operacoes de verdade e precisam continuar separadas.
df = montar([
    pos(9001, "WINQ26", "compra", 130000.0, 130150.0,
        "2026-07-10 11:00:00", "2026-07-10 11:10:00", profit=30.0),
    pos(9002, "WINQ26", "venda", 131000.0, 130800.0,
        "2026-07-10 14:00:00", "2026-07-10 14:20:00", profit=40.0),
])
r = dt._parear_hedge_deals(df)
checar("nao funde operacoes independentes", len(r) == 2, f"len={len(r)}")

print("\n=== TESTE 4 (NEGATIVO) — pernas espelhadas em DIAS diferentes nao fundem ===")
df = montar([
    pos(9101, "WDOQ26", "compra", 5100.0, 5090.0,
        "2026-07-10 11:00:00", "2026-07-10 12:00:00"),
    pos(9102, "WDOQ26", "venda", 5090.0, 5100.0,
        "2026-07-11 11:00:00", "2026-07-11 12:00:00"),
])
r = dt._parear_hedge_deals(df)
checar("dias diferentes nao fundem", len(r) == 2, f"len={len(r)}")

print("\n=== TESTE 5 (NEGATIVO) — volumes diferentes nao fundem ===")
df = montar([
    pos(9201, "WDOQ26", "compra", 5100.0, 5090.0,
        "2026-07-10 11:00:00", "2026-07-10 12:00:00", vol=1.0),
    pos(9202, "WDOQ26", "venda", 5090.0, 5100.0,
        "2026-07-10 11:00:00", "2026-07-10 12:00:00", vol=2.0),
])
r = dt._parear_hedge_deals(df)
checar("volumes diferentes nao fundem", len(r) == 2, f"len={len(r)}")

print("\n=== TESTE 6 — contratos diferentes (WIN x WDO) nunca se misturam ===")
df = montar([
    pos(9301, "WINQ26", "compra", 5100.0, 5090.0,
        "2026-07-10 11:00:00", "2026-07-10 12:00:00"),
    pos(9302, "WDOQ26", "venda", 5090.0, 5100.0,
        "2026-07-10 11:00:00", "2026-07-10 12:00:00"),
])
r = dt._parear_hedge_deals(df)
checar("contratos diferentes nao fundem", len(r) == 2, f"len={len(r)}")

print("\n=== TESTE 7 — 2 pares no mesmo dia fundem em 2 operacoes ===")
df = montar([
    pos(9401, "WDOQ26", "compra", 5100.0, 5090.0,
        "2026-07-10 11:00:00", "2026-07-10 12:00:00"),
    pos(9402, "WDOQ26", "venda", 5090.0, 5100.0,
        "2026-07-10 11:00:00", "2026-07-10 12:00:00"),
    pos(9403, "WDOQ26", "compra", 5200.0, 5210.0,
        "2026-07-10 15:00:00", "2026-07-10 16:00:00"),
    pos(9404, "WDOQ26", "venda", 5210.0, 5200.0,
        "2026-07-10 15:00:00", "2026-07-10 16:00:00"),
])
r = dt._parear_hedge_deals(df)
checar("4 pernas viram 2 operacoes", len(r) == 2, f"len={len(r)}")

print("\n=== TESTE 8 — perna orfa (sem saida) continua no caminho antigo ===")
df = montar([
    pos(9501, "WDOQ26", "compra", 5100.0, None,
        "2026-07-10 11:00:00", None),
    pos(9502, "WDOQ26", "venda", 5110.0, None,
        "2026-07-10 11:30:00", None),
])
r = dt._parear_hedge_deals(df)
checar("orfas ainda fundem (regressao)", len(r) == 1, f"len={len(r)}")

print("\n=== TESTE 9 (NEGATIVO) — orfas de DIAS diferentes nao fundem ===")
# Mesma trava que `detectar_pares_hedge` ganhou: sem ela o pareamento
# historico fabricava operacao juntando pernas de dias distintos (foi o que
# gerou o trade #167 de -R$225).
df = montar([
    pos(9601, "WDOQ26", "compra", 5100.0, None, "2026-07-10 11:00:00", None),
    pos(9602, "WDOQ26", "venda", 5110.0, None, "2026-07-11 11:00:00", None),
])
r = dt._parear_hedge_deals(df)
checar("orfas de dias diferentes nao fundem", len(r) == 2, f"len={len(r)}")

print("\n=== TESTE 10 (NEGATIVO) — orfas de VENCIMENTOS diferentes nao fundem ===")
# Agrupava por raiz ('WDO'), entao WDOQ26 podia parear com WDOU26.
df = montar([
    pos(9701, "WDOQ26", "compra", 5100.0, None, "2026-07-10 11:00:00", None),
    pos(9702, "WDOU26", "venda", 5110.0, None, "2026-07-10 11:30:00", None),
])
r = dt._parear_hedge_deals(df)
checar("vencimentos diferentes nao fundem", len(r) == 2, f"len={len(r)}")

print("\n" + "=" * 60)
if falhas:
    print(f"  {len(falhas)} FALHA(S): {falhas}")
    sys.exit(1)
print("  TODOS OS TESTES PASSARAM")
