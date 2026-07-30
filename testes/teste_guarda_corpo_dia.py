"""
Testa o guarda-corpo operacional do Diário — `dt.status_do_dia` e
`dt.avaliar_contexto_operacao` (2026-07-30).

Existe porque o maior achado do estudo foi comportamental, não técnico: dias
com 5+ operações destruíram R$ 627 em 12 dias. O contador precisa estar certo
ou o aviso vira ruído — um alerta que dispara errado é pior que nenhum.

O que precisa valer:
  1. conta só o DIA pedido, só trades REAIS (simulador fora)
  2. os 3 estados (livre / atencao / critico) viram nos limites certos
  3. 2 stops disparam 'critico' mesmo com poucas operações
  4. posição ainda ABERTA não conta como operação do dia
  5. avaliar_contexto_operacao acusa setup suspenso e reconhece vencedor

Roda direto: `python testes/teste_guarda_corpo_dia.py`. Sai != 0 se falhar.
"""
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config_backtest as cfg  # noqa: E402
import persistencia  # noqa: E402
import diario_trade as dt  # noqa: E402

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


scratch = Path(tempfile.mkdtemp(prefix="teste_guarda_corpo_"))
persistencia._caminho_diario = lambda: scratch / "diario_trades.parquet"
persistencia._caminho_diario_lock = lambda: scratch / ".diario.lock"
persistencia._caminho_posicoes_abertas = lambda: scratch / "posicoes_abertas.parquet"
persistencia._caminho_posicoes_abertas_lock = lambda: scratch / ".posicoes.lock"
persistencia._caminho_tickets_finalizados = lambda: scratch / "tickets_finalizados.parquet"
persistencia._caminho_tickets_ignorados = lambda: scratch / "tickets_ignorados.parquet"
persistencia._caminho_reconciliacao = lambda: scratch / "reconciliacao.parquet"

HOJE = date.today()
ONTEM = HOJE - timedelta(days=1)


def gravar(data_op, saldo, saida="manual", simulado=False):
    return dt.registrar_trade({
        "data_op": data_op.isoformat(), "ativo": "WDO", "tipo_operacao": "short",
        "saldo_operacao_reais": saldo, "resultado_pontos": saldo / 10.0,
        "tipo_saida": saida, "is_simulated": simulado,
    })


try:
    print("\n=== dia vazio ===")
    s = dt.status_do_dia()
    checar(s["n_trades"] == 0 and s["estado"] == "livre", "dia sem trades = livre")
    checar(s["restantes"] == cfg.DIARIO_LIMITE_TRADES_DIA, "restantes = limite cheio")
    checar(s["motivos"] == [], "sem motivos quando está livre")

    print("\n=== isolamento por dia e por simulador ===")
    gravar(ONTEM, -50.0, "stop")
    gravar(ONTEM, -50.0, "stop")
    gravar(ONTEM, -50.0, "stop")
    checar(dt.status_do_dia()["n_trades"] == 0, "trades de ONTEM não contam hoje")
    checar(dt.status_do_dia(ONTEM)["n_trades"] == 3, "consultando ontem, conta os 3")

    gravar(HOJE, 100.0, "gain", simulado=True)
    checar(dt.status_do_dia()["n_trades"] == 0, "trade SIMULADO não conta")

    print("\n=== os três estados ===")
    gravar(HOJE, 30.0, "gain")
    s = dt.status_do_dia()
    checar(s["n_trades"] == 1 and s["estado"] == "livre", "1 operação = livre")
    checar(s["restantes"] == cfg.DIARIO_LIMITE_TRADES_DIA - 1, "restantes desconta 1")

    gravar(HOJE, 20.0, "gain")
    s = dt.status_do_dia()
    checar(s["n_trades"] == 2 and s["estado"] == "atencao",
           f"{cfg.DIARIO_LIMITE_TRADES_DIA} operações = atencao")
    checar(s["restantes"] == 0, "restantes zera no limite")
    checar(len(s["motivos"]) == 1, "atencao explica o motivo")

    gravar(HOJE, -10.0, "manual")
    s = dt.status_do_dia()
    checar(s["n_trades"] == 3 and s["estado"] == "critico",
           f"{cfg.DIARIO_TRADES_DIA_CRITICO} operações = critico")
    checar(s["restantes"] == 0, "restantes não fica negativo")
    checar(abs(s["saldo"] - 40.0) < 1e-9, f"saldo do dia soma certo (esperado 40, veio {s['saldo']})")

    print("\n=== stops disparam critico sozinhos ===")
    scratch2 = Path(tempfile.mkdtemp(prefix="teste_guarda_corpo2_"))
    persistencia._caminho_diario = lambda: scratch2 / "diario_trades.parquet"
    persistencia._caminho_diario_lock = lambda: scratch2 / ".diario.lock"
    gravar(HOJE, -50.0, "stop")
    checar(dt.status_do_dia()["estado"] == "livre", "1 stop ainda é livre")
    gravar(HOJE, -50.0, "stop")
    s = dt.status_do_dia()
    checar(s["n_stops"] == 2, "conta os stops do dia")
    checar(s["estado"] == "critico",
           f"{cfg.DIARIO_LIMITE_STOPS_DIA} stops = critico mesmo dentro do limite de trades")
    checar(any("stops" in m for m in s["motivos"]), "o motivo cita os stops")

    print("\n=== avaliar_contexto_operacao ===")
    av = dt.avaliar_contexto_operacao(["bounce_em_suporte"], ativo="WDO", janela="almoco")
    checar(any(a["nivel"] == "alerta" for a in av), "setup suspenso gera alerta")

    av = dt.avaliar_contexto_operacao(["bounce_em_resistencia"], ativo="WDO", janela="almoco")
    checar(any(a["nivel"] == "ok" for a in av), "setup vencedor é reconhecido")
    checar(not any(a["nivel"] == "alerta" for a in av), "vencedor no contexto certo não gera alerta")

    av = dt.avaliar_contexto_operacao(["bounce_em_resistencia"], ativo="WIN", janela="prime")
    textos = " ".join(a["texto"] for a in av)
    checar("almoco" in textos, "avisa quando está fora da janela preferida")
    checar("WDO" in textos, "avisa quando o ativo não é o que rendeu")

    checar(dt.avaliar_contexto_operacao([], ativo=None, janela=None) == [],
           "sem estratégia e sem contexto, não inventa aviso")
    checar(dt.avaliar_contexto_operacao(None) == [], "aceita None sem estourar")

finally:
    shutil.rmtree(scratch, ignore_errors=True)
    try:
        shutil.rmtree(scratch2, ignore_errors=True)
    except NameError:
        pass

print()
if falhas:
    print(f"### {len(falhas)} FALHA(S):")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("### tudo OK")
