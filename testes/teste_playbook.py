"""
Testa o backend do Playbook (2026-08-02): cadastro de Checklist Pré-Trade,
Gerenciamento de Risco na Estratégia, resolução de estratégia única, e o
redesenho do Score (TradeScore/Discipline Score baseado em aderência ao
Playbook, não mais em aderência a traçado).

Roda direto: `python testes/teste_playbook.py`. Sai != 0 se falhar.
"""
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import persistencia  # noqa: E402
import diario_trade as dt  # noqa: E402

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


scratch = Path(tempfile.mkdtemp(prefix="teste_playbook_"))
persistencia._caminho_estrategias = lambda: scratch / "diario_estrategias.parquet"
persistencia._caminho_estrategias_lock = lambda: scratch / ".estrategias.lock"
persistencia._caminho_checklist_pretrade = lambda: scratch / "diario_checklist_pretrade.parquet"
persistencia._caminho_checklist_pretrade_dia = lambda: scratch / "diario_checklist_pretrade_dia.parquet"
persistencia._caminho_checklist_pretrade_dia_lock = lambda: scratch / ".checklist_pretrade_dia.lock"
persistencia._caminho_gerenciamento_risco = lambda: scratch / "diario_gerenciamento_risco.parquet"
persistencia._caminho_diario = lambda: scratch / "diario_trades.parquet"
persistencia._caminho_diario_lock = lambda: scratch / ".diario.lock"
persistencia._caminho_posicoes_abertas = lambda: scratch / "posicoes_abertas.parquet"
persistencia._caminho_posicoes_abertas_lock = lambda: scratch / ".posicoes.lock"
persistencia._caminho_tickets_finalizados = lambda: scratch / "tickets_finalizados.parquet"
persistencia._caminho_tickets_ignorados = lambda: scratch / "tickets_ignorados.parquet"
persistencia._caminho_reconciliacao = lambda: scratch / "reconciliacao.parquet"

HOJE = date.today()

try:
    print("\n=== Checklist Pré-Trade (cadastro) ===")
    df0 = dt.listar_checklists_pretrade()
    checar(len(df0) == 1 and df0.iloc[0]["padrao"], "bootstrap cria 1 checklist padrão")

    id_c2 = dt.criar_checklist_pretrade("Checklist B", ["item x", "item y"], padrao=True)
    checar(id_c2 is not None, "cria 2º checklist")
    df1 = dt.listar_checklists_pretrade()
    padroes = df1[df1["padrao"]]
    checar(len(padroes) == 1 and padroes.iloc[0]["id"] == id_c2, "só 1 padrão por vez (o mais novo)")

    id_c1 = df0.iloc[0]["id"]
    checar(dt.definir_checklist_pretrade_padrao(id_c1), "redefine padrão pro primeiro")
    checar(dt.obter_checklist_pretrade()["id"] == id_c1, "obter_checklist_pretrade() sem id retorna o padrão")
    checar(dt.obter_checklist_pretrade(id_c2)["nome"] == "Checklist B", "obter_checklist_pretrade(id) resolve por id")

    checar(dt.desativar_checklist_pretrade(id_c2), "desativa checklist")
    checar(len(dt.listar_checklists_pretrade()) == 1, "checklist desativado some da listagem ativa")

    print("\n=== Checklist Pré-Trade DIÁRIO (ritual único por dia) ===")
    checar(dt.obter_checklist_pretrade_dia(HOJE) is None, "nada feito hoje ainda = None")
    checar(dt.salvar_checklist_pretrade_dia(HOJE, id_c1, [0, 1], 3), "salva o checklist do dia")
    registro = dt.obter_checklist_pretrade_dia(HOJE)
    checar(registro is not None and registro["total"] == 3, "lê de volta o registro do dia")
    checar(dt.salvar_checklist_pretrade_dia(HOJE, id_c1, [0, 1, 2], 3), "upsert não duplica linha do dia")
    checar(len(persistencia.carregar_checklist_pretrade_dia()) == 1, "só 1 linha por data (upsert)")

    print("\n=== Gerenciamento de Risco (cadastro separado, por ATIVO) ===")
    id_ger = dt.criar_gerenciamento_risco(
        "Perfil 2:1 Teste", "WDO", alvo_pontos=100.0, stop_pontos=50.0,
        breakeven_pontos=20.0, regra_saida="sair no alvo",
        regra_conduta="não mexer no stop", justificativa="2:1 testado no backtest",
    )
    checar(id_ger is not None, "cria gerenciamento de risco")
    gerenciamento = dt.obter_gerenciamento_risco(id_ger)
    checar(gerenciamento is not None and gerenciamento["alvo_pontos"] == 100.0
           and gerenciamento["stop_pontos"] == 50.0, "gerenciamento round-trips")
    checar(gerenciamento["ativo_mercado"] == "WDO", "ativo_mercado persiste")
    checar(gerenciamento["justificativa"] == "2:1 testado no backtest", "justificativa persiste")
    checar(gerenciamento["padrao"] == True, "1º gerenciamento de um ativo já nasce padrão")
    checar(dt.obter_gerenciamento_risco(None) is None, "obter_gerenciamento_risco(None) = None")
    checar(dt.obter_gerenciamento_risco(999999) is None, "id inexistente = None")

    id_ger_win = dt.criar_gerenciamento_risco("Perfil WIN Teste", "WIN", 80.0, 40.0)
    checar(dt.obter_gerenciamento_risco_padrao("WDO")["id"] == id_ger, "padrão de WDO resolve certo")
    checar(dt.obter_gerenciamento_risco_padrao("WIN")["id"] == id_ger_win,
           "padrão de WIN é INDEPENDENTE do padrão de WDO")
    checar(dt.obter_gerenciamento_risco_padrao("WDO")["id"] != dt.obter_gerenciamento_risco_padrao("WIN")["id"],
           "cada ativo tem seu próprio padrão")

    id_ger_wdo2 = dt.criar_gerenciamento_risco("Perfil 2:1 Alternativo", "WDO", 120.0, 60.0)
    checar(dt.obter_gerenciamento_risco_padrao("WDO")["id"] == id_ger,
           "criar um 2º gerenciamento do MESMO ativo não rouba o padrão sem pedir")
    checar(dt.definir_gerenciamento_risco_padrao(id_ger_wdo2), "redefine o padrão de WDO")
    checar(dt.obter_gerenciamento_risco_padrao("WDO")["id"] == id_ger_wdo2, "novo padrão de WDO vale")
    checar(dt.obter_gerenciamento_risco_padrao("WIN")["id"] == id_ger_win,
           "trocar o padrão de WDO NÃO afeta o padrão de WIN")
    checar(dt.obter_gerenciamento_risco_padrao("WDX") is None, "ativo sem nenhum gerenciamento cadastrado = None")

    print("\n=== resolver_estrategia_unica (Estratégia NÃO referencia mais Gerenciamento de Risco) ===")
    id_e1 = dt.criar_estrategia("Rompimento Teste")
    estrategia_df = dt.listar_estrategias(somente_ativas=False)
    linha = estrategia_df[estrategia_df["id"] == id_e1].iloc[0].to_dict()
    checar("id_gerenciamento_risco" not in linha, "Estratégia não tem mais campo id_gerenciamento_risco")
    slug_e1 = linha["slug"]
    checar(dt.resolver_estrategia_unica([slug_e1]) is not None, "1 estratégia resolve")
    checar(dt.resolver_estrategia_unica([]) is None, "0 estratégias não resolve")
    checar(dt.resolver_estrategia_unica(None) is None, "None não resolve")
    checar(dt.resolver_estrategia_unica([slug_e1, "outra_qualquer"]) is None, "2+ estratégias não resolve")

    print("\n=== avaliar_risco_seguido ===")
    risco_resolvido = dt.obter_gerenciamento_risco(id_ger)
    checar(dt.avaliar_risco_seguido(100.0, 50.0, risco_resolvido) is True, "dentro da tolerância = True")
    checar(dt.avaliar_risco_seguido(200.0, 50.0, risco_resolvido) is False, "fora da tolerância = False")
    checar(dt.avaliar_risco_seguido(100.0, 50.0, None) is None, "sem gerenciamento resolvido = None")
    sem_risco = {"alvo_pontos": None, "stop_pontos": None}
    checar(dt.avaliar_risco_seguido(100.0, 50.0, sem_risco) is None, "gerenciamento sem valores cadastrados = None")

    print("\n=== Integração ponta-a-ponta: snapshot do Playbook num trade real ===")
    dt.salvar_checklist_pretrade_dia(HOJE, id_c1, [0, 1], 3)
    dt.salvar_checkin_dia(HOJE, {"observacoes": "teste"})
    # Replica a composição de `_snapshot_playbook_trade` (diario_app_classico.py)
    # usando só funções de dt — mesma lógica, sem precisar importar a UI.
    # Gerenciamento de risco agora vem do ATIVO (WDO), não da estratégia —
    # usa o id_ger direto (não o padrão atual de WDO, que os testes acima
    # já trocaram pra id_ger_wdo2) pra manter os pontos 100/50 consistentes.
    estrategia_resolvida = dt.resolver_estrategia_unica([slug_e1])
    gerenciamento_resolvido = dt.obter_gerenciamento_risco(id_ger)
    checklist_dia = dt.obter_checklist_pretrade_dia(HOJE)
    snapshot = dict(
        checklist_estrategia_slug=estrategia_resolvida["slug"],
        checklist_estrategia_marcados=[0],
        checklist_estrategia_total=len(estrategia_resolvida["checklist"]),
        checklist_pretrade_marcados_dia=checklist_dia["marcados"],
        checklist_pretrade_total_dia=checklist_dia["total"],
        risco_gerenciamento_seguido=dt.avaliar_risco_seguido(100.0, 50.0, gerenciamento_resolvido),
        coaching_checkin_feito=bool(dt.obter_checkin_dia(HOJE)),
    )
    id_trade = dt.registrar_trade({
        "data_op": HOJE.isoformat(), "ativo": "WDO", "tipo_operacao": "compra",
        "estrategia_tags": [slug_e1], "alvo_pontos": 100.0, "stop_pontos": 50.0,
        "saldo_operacao_reais": 50.0, "resultado_pontos": 5.0, **snapshot,
    })
    checar(id_trade is not None, "registra o trade com snapshot do Playbook")
    trade_salvo = dt.listar_trades()
    linha_trade = trade_salvo[trade_salvo["id_trade"] == id_trade].iloc[0].to_dict()
    checar(linha_trade["checklist_estrategia_slug"] == slug_e1, "checklist_estrategia_slug round-trips")
    checar(list(linha_trade["checklist_estrategia_marcados"]) == [0], "checklist_estrategia_marcados round-trips")
    checar(linha_trade["risco_gerenciamento_seguido"] == True, "risco_gerenciamento_seguido round-trips (True)")
    checar(linha_trade["coaching_checkin_feito"] == True, "coaching_checkin_feito round-trips (True)")
    score_final = dt.calcular_trade_score(linha_trade, anticovardia_score_atual=70.0)
    checar(score_final is not None and 0 <= score_final <= 100, "calcular_trade_score funciona sobre o trade real salvo")

    print("\n=== calcular_playbook_adherence_trade ===")
    trade_completo = {
        "checklist_pretrade_marcados_dia": [0, 1, 2], "checklist_pretrade_total_dia": 3,
        "checklist_estrategia_marcados": [0], "checklist_estrategia_total": 2,
        "risco_gerenciamento_seguido": True, "coaching_checkin_feito": True,
    }
    r = dt.calcular_playbook_adherence_trade(trade_completo)
    checar(r["adherence"] is not None and 0 <= r["adherence"] <= 100, "aderência completa cai em [0,100]")
    checar(abs(r["adherence"] - (100 * 0.30 + 50 * 0.30 + 100 * 0.25 + 100 * 0.15)) < 1e-6,
           "aderência completa bate com a soma ponderada esperada")

    trade_parcial = {"risco_gerenciamento_seguido": True}
    r2 = dt.calcular_playbook_adherence_trade(trade_parcial)
    checar(r2["adherence"] == 100.0, "só 1 componente disponível = renormaliza pra 100% dele (True=100)")

    trade_vazio = {}
    r3 = dt.calcular_playbook_adherence_trade(trade_vazio)
    checar(r3["adherence"] is None, "nenhum componente disponível = None")

    print("\n=== calcular_trade_score ===")
    checar(dt.calcular_trade_score(trade_completo, anticovardia_score_atual=80.0) is not None,
           "score com os 2 lados presentes")
    checar(dt.calcular_trade_score(trade_vazio, anticovardia_score_atual=80.0) == 80.0,
           "só anticovardia disponível = usa só ele")
    checar(dt.calcular_trade_score(trade_completo, anticovardia_score_atual=None) == r["adherence"],
           "só playbook disponível = usa só ele")
    checar(dt.calcular_trade_score(trade_vazio, anticovardia_score_atual=None) is None,
           "nenhum dos dois = None")

    print("\n=== calcular_discipline_score ===")
    df_vazio = dt.listar_trades().iloc[0:0]
    vazio = dt.calcular_discipline_score(df=df_vazio)
    checar(vazio["score"] is None and vazio["eixos"] == {}, "sem trades = score None, eixos vazios")
    com_dados = dt.calcular_discipline_score(df=dt.listar_trades())
    checar(com_dados["score"] is not None, "com o trade real salvo acima, score sai preenchido")

finally:
    shutil.rmtree(scratch, ignore_errors=True)

print()
if falhas:
    print(f"### {len(falhas)} FALHA(S):")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("### tudo OK")
