# =============================================================================
# profit_importar_extrato.py — parser do export "Relatório de Performance" /
# "Operações" do ProfitPro (Nelogica)
# =============================================================================
# Preparado defensivamente (2026-08-05, pedido do Jesué: "prepare o sistema
# para receber imports do profit também, caso seja necessário") — WIN/WDO já
# chega ao Diário sozinho via sincronização MT5 ao vivo, então este caminho
# NÃO é o fluxo do dia a dia; serve pra backfill/reconciliação manual quando
# fizer sentido (ex: buraco na sincronização, período fora do alcance do
# MT5). `diario_trade.trade_ja_importado` já protege contra duplicar um
# trade que o MT5 tenha sincronizado sozinho.
#
# Reaproveita byte a byte a lógica de parsing já validada em
# `testes/auditar_vs_profit.py` (script de auditoria read-only existente,
# usado desde 2026-07-27) — mesmo encoding/delimitador/formato numérico,
# only-read virou also-write aqui.
# =============================================================================

from __future__ import annotations

from datetime import datetime

import pandas as pd

COLUNAS_EXTRATO = [
    "ativo", "tipo_operacao", "datetime_entrada", "datetime_saida",
    "preco_entrada", "preco_saida", "lote", "saldo_reais",
]

_LADO_LONG = {"c"}   # Compra — abriu comprado
_LADO_SHORT = {"v"}  # Venda — abriu vendido


def _num(texto: str) -> float:
    """Formato BR: milhar com ponto, decimal com vírgula (ex: '1.234,56')."""
    texto = (texto or "").strip().replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def _decodificar(conteudo_bytes: bytes) -> str:
    """O export do ProfitPro vem em cp1252/latin-1 (acentos fora de UTF-8) —
    mesma escolha de `testes/auditar_vs_profit.py`."""
    try:
        return conteudo_bytes.decode("latin-1")
    except UnicodeDecodeError:
        return conteudo_bytes.decode("utf-8", errors="replace")


def parsear_extrato_csv(conteudo: str | bytes) -> pd.DataFrame:
    """Extrai a tabela de operações do export "Operações"/"Relatório de
    Performance" do ProfitPro. Retorna um DataFrame com `COLUNAS_EXTRATO`,
    uma linha por trade fechado — `saldo_reais` já vem pronto (Res.
    Operação), sem conversão de moeda (WIN/WDO liquidam em BRL nativamente).
    """
    if isinstance(conteudo, bytes):
        conteudo = _decodificar(conteudo)

    linhas_arquivo = conteudo.splitlines()
    try:
        i0 = next(i for i, l in enumerate(linhas_arquivo) if l.startswith("Ativo;"))
    except StopIteration:
        return pd.DataFrame(columns=COLUNAS_EXTRATO)

    linhas = []
    for l in linhas_arquivo[i0 + 1:]:
        p = l.split(";")
        # Colunas: Ativo(0) Abertura(1) Fechamento(2) Tempo Operação(3)
        # Qtd Compra(4) Qtd Venda(5) Lado(6) Preço Compra(7) Preço Venda(8)
        # Preço de Mercado(9) Médio(10) Res. Intervalo Bruto(11) Res.
        # Intervalo %(12) Res. Operação(13) Res. Operação %(14) TET(15) Total(16).
        if len(p) < 14 or not p[0].strip():
            continue
        lado = p[6].strip().lower()
        if lado in _LADO_LONG:
            tipo_operacao = "long"
            preco_entrada, preco_saida = _num(p[7]), _num(p[8])
        elif lado in _LADO_SHORT:
            tipo_operacao = "short"
            preco_entrada, preco_saida = _num(p[8]), _num(p[7])
        else:
            continue
        try:
            datetime_entrada = datetime.strptime(p[1].strip(), "%d/%m/%Y %H:%M:%S")
            datetime_saida = datetime.strptime(p[2].strip(), "%d/%m/%Y %H:%M:%S")
        except ValueError:
            continue
        qtd = max(_num(p[4]), _num(p[5])) or 1
        linhas.append({
            "ativo": p[0].strip()[:3], "tipo_operacao": tipo_operacao,
            "datetime_entrada": datetime_entrada, "datetime_saida": datetime_saida,
            "preco_entrada": preco_entrada, "preco_saida": preco_saida,
            "lote": qtd, "saldo_reais": _num(p[13]),
        })
    return pd.DataFrame(linhas, columns=COLUNAS_EXTRATO)
