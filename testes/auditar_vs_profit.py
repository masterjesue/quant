"""Audita o Diario contra um export de "Relatorio de Performance" do ProfitPro.

O relatorio da corretora e a fonte de verdade final — este script diz, dia a
dia, onde o Diario diverge dele e por que. Criado em 2026-07-27 depois de uma
divergencia real (46 operacoes no Diario contra 42 no Profit) causada por
pernas de hedge contadas como operacoes separadas.

Uso:
    python testes/auditar_vs_profit.py <caminho-do-export.csv>
    python testes/auditar_vs_profit.py            # usa o padrao do Desktop

Somente LEITURA — nunca escreve em nada.
"""
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PADRAO = Path.home() / "OneDrive" / "Desktop" / "profit.csv"
CSV = Path(sys.argv[1]) if len(sys.argv) > 1 else PADRAO
if not CSV.exists():
    print(f"CSV nao encontrado: {CSV}")
    sys.exit(2)


def _num(s):
    s = (s or "").strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


# ---------- le o export do Profit (cp1252, cabecalho de 4 linhas) ----------
linhas = CSV.read_text(encoding="latin-1").splitlines()
try:
    i0 = next(i for i, l in enumerate(linhas) if l.startswith("Ativo;"))
except StopIteration:
    print("Formato inesperado: nao achei a linha de cabecalho 'Ativo;...'")
    sys.exit(2)

regs = []
for l in linhas[i0 + 1:]:
    p = l.split(";")
    if len(p) < 14 or not p[0].strip():
        continue
    regs.append({
        "ativo": p[0][:3],
        "abertura": datetime.strptime(p[1], "%d/%m/%Y %H:%M:%S"),
        "res": _num(p[13]),
    })
prof = pd.DataFrame(regs)
if prof.empty:
    print("Nenhuma operacao no export.")
    sys.exit(2)
prof["data"] = prof["abertura"].dt.date
d_ini, d_fim = prof["data"].min(), prof["data"].max()

# ---------- le o Diario no mesmo periodo ----------
import diario_trade as dt  # noqa: E402

di = dt.listar_trades(data_ini=str(d_ini), data_fim=str(d_fim), is_simulated=False)
di["data"] = pd.to_datetime(di["data_op"]).dt.date

print("=" * 68)
print(f"  AUDITORIA  {d_ini}  ->  {d_fim}")
print("=" * 68)


def bloco(nome, n, soma, w, p, z):
    print(f"  {nome:<10} ops={n:<4} saldo={soma:>+9.2f}  wins={w:<3} losses={p:<3} zerados={z}")


bloco("PROFIT", len(prof), prof["res"].sum(),
      (prof["res"] > 0).sum(), (prof["res"] < 0).sum(), (prof["res"] == 0).sum())
s = di["saldo_operacao_reais"]
bloco("DIARIO", len(di), s.sum(), (s > 0).sum(), (s < 0).sum(), (s == 0).sum())

# ---------- por dia ----------
a = prof.groupby("data")["res"].agg(["count", "sum"])
b = di.groupby("data")["saldo_operacao_reais"].agg(["count", "sum"])
c = a.join(b, how="outer", lsuffix="_profit", rsuffix="_diario").fillna(0)
c["d_ops"] = (c["count_diario"] - c["count_profit"]).astype(int)
c["d_reais"] = c["sum_diario"] - c["sum_profit"]

div = c[(c["d_ops"] != 0) | (c["d_reais"].abs() > 0.005)]
print()
if div.empty:
    print("  OK — todos os dias batem com o relatorio da corretora.")
else:
    print(f"  {len(div)} DIA(S) DIVERGENTE(S):")
    print()
    for data, r in div.iterrows():
        print(f"    {data}   ops: {int(r['count_diario'])} vs {int(r['count_profit'])} "
              f"(dif {int(r['d_ops']):+d})   R$ {r['sum_diario']:+.2f} vs {r['sum_profit']:+.2f} "
              f"(dif {r['d_reais']:+.2f})")
    print()
    print(f"    TOTAL  dif ops: {int(c['d_ops'].sum()):+d}   dif R$: {c['d_reais'].sum():+.2f}")

# ---------- pendencias que ja tem trade (o bug de perna fantasma) ----------
print()
pend = dt.listar_pendencias_historico()
print(f"  Pendencias abertas no Historico: {len(pend)}")
suspeitas = []
if not pend.empty:
    todos = dt.listar_trades(is_simulated=False)
    for _, r in pend.iterrows():
        ja = todos[(todos["ativo"] == r["ativo"])
                   & (todos["data_op"].astype(str) == str(r["data"]))]
        marca = "  <-- ja existe trade nessa data" if len(ja) else ""
        if len(ja):
            suspeitas.append(r["id_editar"])
        print(f"    {r['data']}  {r['ativo']}  R$ {r['saldo_estimado']:+.2f}"
              f"  (id {r['id_editar']}){marca}")
    if suspeitas:
        print()
        print("    ATENCAO: as marcadas acima podem ser perna de hedge nao fundida.")
        print("    Confirme no relatorio da corretora ANTES de finalizar — registrar")
        print("    uma dessas cria trade duplicado.")

# ---------- consistencia interna (preco x saldo) ----------
import config_backtest as cfg  # noqa: E402

print()
ruins = []
for _, r in di.iterrows():
    pe, ps = r["preco_entrada"], r["preco_saida"]
    if pd.isna(pe) or pd.isna(ps):
        continue
    lote = r["lote"] or 1
    pts = (ps - pe) if r["tipo_operacao"] == "long" else (pe - ps)
    esperado = pts * cfg.VALOR_PONTO.get(r["ativo"], 0) * lote
    if abs(esperado - r["saldo_operacao_reais"]) > 0.01:
        ruins.append((int(r["id_trade"]), str(r["data_op"]), r["ativo"],
                      round(esperado, 2), r["saldo_operacao_reais"], r["fonte_registro"]))
print(f"  Trades cujo saldo nao bate com os proprios precos: {len(ruins)}")
for x in ruins:
    nota = "  (par de hedge — esperado)" if x[5] == "manual" else "  <-- conferir"
    print(f"    #{x[0]}  {x[1]}  {x[2]}  precos dao {x[3]:+.2f}, gravado {x[4]:+.2f}{nota}")
print()
print("  Obs: em par de hedge o saldo e a SOMA das 2 pernas, entao entrada/saida")
print("  nao reconstroem o valor — divergencia ali e esperada, nao erro.")
