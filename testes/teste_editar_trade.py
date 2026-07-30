"""
Testa `diario_trade.atualizar_trade` — edição de um trade JÁ FINALIZADO
(`diario_trades.parquet`), que sustenta o botão "Editar" do Histórico
(pedido do Jesué em 2026-07-30: reclassificar estratégias depois do fato).

O que precisa valer:
  1. edita os campos da allowlist e persiste
  2. RECUSA qualquer campo fora dela — preço/saldo/data vêm do MT5 e são
     fato, não opinião (foi uma auditoria anual inteira pra alinhar isso)
  3. não vaza pra outros trades
  4. `estrategia_tags` continua sendo LISTA depois do round-trip em parquet,
     inclusive partindo de uma coluna 100% vazia (que vem como float64 e
     levantaria ValueError num `.at` com lista)
  5. id inexistente devolve False em vez de estourar

Roda direto: `python testes/teste_editar_trade.py`. Sai != 0 se algo falhar.
"""
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import persistencia  # noqa: E402
import diario_trade as dt  # noqa: E402

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


scratch = Path(tempfile.mkdtemp(prefix="teste_editar_trade_"))

# Convenção do CLAUDE.md: monkeypatch de TODOS os caminhos de persistência do
# Diário antes de exercer qualquer escrita — nunca contra os parquets reais.
persistencia._caminho_diario = lambda: scratch / "diario_trades.parquet"
persistencia._caminho_diario_lock = lambda: scratch / ".diario.lock"
persistencia._caminho_posicoes_abertas = lambda: scratch / "posicoes_abertas.parquet"
persistencia._caminho_posicoes_abertas_lock = lambda: scratch / ".posicoes.lock"
persistencia._caminho_tickets_finalizados = lambda: scratch / "tickets_finalizados.parquet"
persistencia._caminho_tickets_ignorados = lambda: scratch / "tickets_ignorados.parquet"
persistencia._caminho_reconciliacao = lambda: scratch / "reconciliacao.parquet"

try:
    print("\n=== cenário: 2 trades gravados, edição retroativa do primeiro ===")
    id_a = dt.registrar_trade({
        "data_op": "2026-07-01", "ativo": "WIN", "tipo_operacao": "compra",
        "preco_entrada": 140000.0, "preco_saida": 140150.0,
        "resultado_pontos": 150.0, "saldo_operacao_reais": 30.0,
        "resumo_operacional": "texto original",
    })
    id_b = dt.registrar_trade({
        "data_op": "2026-07-02", "ativo": "WDO", "tipo_operacao": "venda",
        "preco_entrada": 5400.0, "preco_saida": 5395.0,
        "resultado_pontos": 5.0, "saldo_operacao_reais": 50.0,
    })
    checar(id_a == 1 and id_b == 2, f"2 trades gravados (ids {id_a}, {id_b})")

    # --- 1. edita os campos da allowlist -------------------------------------
    ok = dt.atualizar_trade(id_a, {
        "estrategia_tags": ["bounce_em_suporte"],
        "resumo_operacional": "reclassificado na revisão",
        "janela_pregao": "abertura",
        "nivel_sr_tipo": "suporte",
    })
    checar(ok is True, "atualizar_trade devolveu True")

    df = dt.listar_trades()
    linha_a = df[df["id_trade"] == id_a].iloc[0]
    checar(list(linha_a["estrategia_tags"]) == ["bounce_em_suporte"],
           "estrategia_tags gravada")
    checar(linha_a["resumo_operacional"] == "reclassificado na revisão",
           "resumo_operacional gravado")
    checar(linha_a["janela_pregao"] == "abertura", "janela_pregao gravada")
    checar(linha_a["nivel_sr_tipo"] == "suporte", "nivel_sr_tipo gravado")

    # --- 4. lista continua lista depois do round-trip em parquet -------------
    checar(hasattr(linha_a["estrategia_tags"], "__iter__")
           and not isinstance(linha_a["estrategia_tags"], str),
           "estrategia_tags voltou como sequência, não string")
    # `tags_emocionais` estava 100% vazia (coluna float64) — o caso que
    # levantava ValueError sem o astype(object).
    ok_emo = dt.atualizar_trade(id_a, {"tags_emocionais": ["ansioso", "apressado"]})
    checar(ok_emo is True, "escreve lista em coluna que estava 100% vazia")
    linha_a = dt.listar_trades().query("id_trade == @id_a").iloc[0]
    checar(list(linha_a["tags_emocionais"]) == ["ansioso", "apressado"],
           "tags_emocionais gravada a partir de coluna vazia")

    # --- 2. RECUSA campos fora da allowlist ---------------------------------
    recusado = dt.atualizar_trade(id_a, {
        "saldo_operacao_reais": 99999.0,
        "preco_entrada": 1.0,
        "data_op": "1999-01-01",
        "id_trade": 777,
    })
    checar(recusado is False, "atualizar_trade recusa payload só com campos proibidos")
    linha_a = dt.listar_trades().query("id_trade == @id_a").iloc[0]
    checar(float(linha_a["saldo_operacao_reais"]) == 30.0, "saldo_operacao_reais intacto")
    checar(float(linha_a["preco_entrada"]) == 140000.0, "preco_entrada intacto")
    checar(str(linha_a["data_op"]) == "2026-07-01", "data_op intacta")
    checar(int(linha_a["id_trade"]) == id_a, "id_trade intacto")

    # payload MISTO: aplica o permitido e ignora o resto, sem falhar
    misto = dt.atualizar_trade(id_a, {"obs": "nota nova", "saldo_operacao_reais": -1.0})
    linha_a = dt.listar_trades().query("id_trade == @id_a").iloc[0]
    checar(misto is True and linha_a["obs"] == "nota nova",
           "payload misto aplica o campo permitido")
    checar(float(linha_a["saldo_operacao_reais"]) == 30.0,
           "payload misto NÃO aplica o campo proibido")

    # --- 3. não vaza pro outro trade ----------------------------------------
    linha_b = dt.listar_trades().query("id_trade == @id_b").iloc[0]
    checar(len(dt._para_lista(linha_b["estrategia_tags"])) == 0,
           "trade vizinho continua sem estratégia")
    checar(pd.isna(linha_b["resumo_operacional"]) or not linha_b["resumo_operacional"],
           "trade vizinho continua sem resumo")
    checar(float(linha_b["saldo_operacao_reais"]) == 50.0, "trade vizinho com saldo intacto")

    # --- 5. bordas -----------------------------------------------------------
    checar(dt.atualizar_trade(9999, {"resumo_operacional": "x"}) is False,
           "id_trade inexistente devolve False")
    checar(dt.atualizar_trade(id_a, {}) is False, "payload vazio devolve False")

    checar(len(dt.listar_trades()) == 2, "nenhum trade criado/apagado pela edição")

finally:
    shutil.rmtree(scratch, ignore_errors=True)

print()
if falhas:
    print(f"### {len(falhas)} FALHA(S):")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("### tudo OK")
