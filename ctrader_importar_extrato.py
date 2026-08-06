# =============================================================================
# ctrader_importar_extrato.py — parser do extrato "Histórico" exportado pela
# plataforma cTrader/Spotware (menu Conta > Extrato de Conta > Exportar)
# =============================================================================
# Fonte de dados provisória pros trades de XAUUSD/EURUSD (conta cTrader, IC
# Markets) enquanto a Fase 1 do plano de integração (histórico automático via
# Open API) segue bloqueada pela aprovação da Spotware — Jesué já começou a
# operar em 2026-08-05 e precisa lançar esses trades no Diário sem esperar.
#
# A conta cTrader é NETTING (ao contrário da MT5, hedging) — o extrato já vem
# com cada trade como um round-trip líquido fechado (entrada+saída pareadas),
# não fills brutos. Isso é bem mais simples que o parsing de ExecutionReport
# em `ctrader_loader.py` (feito pra consumir fills ao vivo da sessão FIX
# TRADE): aqui, 1 linha da tabela "Histórico" = 1 trade pronto pro Diário.
#
# Dois formatos de extrato suportados (Jesué reconfigurou a exportação em
# 2026-08-05 pra trazer mais colunas — ver `COLUNAS_EXTRATO`):
#   - "básico" (8 campos/linha): Símbolo, Direção de abertura, Hora de
#     fecho, Preço de entrada, Preço de fecho, Quantidade de Fecho, USD
#     líquidos, Saldo USD. NÃO traz horário de entrada — `datetime_entrada`
#     sai None (limitação do formato, não do parser).
#   - "completo" (15 campos/linha, formato atual): acrescenta Direção de
#     fecho, Horário de ABERTURA, Swap, Comissão, USD brutos, Pips, Canal.
#     `datetime_entrada` vem preenchido — não precisa mais de revisão manual
#     antes de importar. "USD líquidos" já inclui swap+comissão (conferido:
#     brutos + swap - comissão == líquidos), continua sendo o campo certo
#     pra `saldo_usd`.
# Os dois formatos podem aparecer misturados no mesmo arquivo (nunca visto
# na prática, mas o parser trata linha a linha por contagem de campos, então
# suporta isso de graça).
# =============================================================================

from __future__ import annotations

import io
import re

import pandas as pd

_DIRECAO_LONG = {"comprar", "buy"}
_DIRECAO_SHORT = {"vender", "sell"}

COLUNAS_EXTRATO = [
    "ativo", "tipo_operacao", "datetime_entrada", "datetime_saida",
    "preco_entrada", "preco_saida", "lote", "saldo_usd", "pips",
]

# Tabela "Histórico" delimitada pelo título até o próximo </table> — o
# extrato tem outras tabelas depois (Posições/Ordens/Transações/Resumo) que
# não nos interessam aqui.
_RE_TABELA_HISTORICO = re.compile(r'Hist[oó]rico</strong>.*?</table>', re.S)
_RE_TR = re.compile(r'<tr[^>]*>(.*?)</tr>', re.S)
_RE_NOBR = re.compile(r'<nobr>(.*?)</nobr>', re.S)
_RE_NUM = re.compile(r'-?[\d.,]+')


def _num(texto: str) -> float | None:
    m = _RE_NUM.search(texto.strip())
    if not m:
        return None
    return float(m.group().replace(',', '.'))


def _direcao(texto: str) -> str | None:
    norm = texto.strip().lower()
    if norm in _DIRECAO_LONG:
        return "long"
    if norm in _DIRECAO_SHORT:
        return "short"
    return None


def _data(valor) -> pd.Timestamp | None:
    return pd.to_datetime(str(valor).strip(), dayfirst=True, errors="coerce")


def _decodificar(conteudo_bytes: bytes) -> str:
    """O extrato declara `charset=utf-16` no `<meta>`, mas os exports reais
    observados vêm em UTF-8 puro (a declaração parece um bug/artefato do
    gerador do relatório) — tenta UTF-8 primeiro, cai pra cp1252/latin-1 se
    falhar, nunca levanta exceção de decode."""
    for codec in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return conteudo_bytes.decode(codec)
        except UnicodeDecodeError:
            continue
    return conteudo_bytes.decode("latin-1")


def parsear_extrato_html(conteudo: str | bytes) -> pd.DataFrame:
    """Extrai a tabela "Histórico" do extrato HTML (.htm) da cTrader/Spotware.
    Retorna um DataFrame com `COLUNAS_EXTRATO`, uma linha por trade fechado."""
    if isinstance(conteudo, bytes):
        conteudo = _decodificar(conteudo)

    match = _RE_TABELA_HISTORICO.search(conteudo)
    if not match:
        return pd.DataFrame(columns=COLUNAS_EXTRATO)

    linhas = []
    for tr in _RE_TR.findall(match.group(0)):
        campos = _RE_NOBR.findall(tr)

        if len(campos) == 15:
            # Formato completo (2026-08-05+): Símbolo, Direção abertura,
            # Direção fecho, Horário abertura, Hora fecho, Preço entrada,
            # Preço fecho, Quantidade, Swap, Comissão, $ brutos, $ líquidos,
            # Saldo $, Pips, Canal.
            (simbolo, dir_abertura, _dir_fecho, hora_abertura, hora_fecho,
             preco_entrada, preco_fecho, qtd, _swap, _comissao, _brutos,
             usd_liq, _saldo_conta, pips, _canal) = campos
            tipo_operacao = _direcao(dir_abertura)
            if tipo_operacao is None:
                continue
            linhas.append({
                "ativo": simbolo.strip(), "tipo_operacao": tipo_operacao,
                "datetime_entrada": _data(hora_abertura),
                "datetime_saida": _data(hora_fecho),
                "preco_entrada": _num(preco_entrada), "preco_saida": _num(preco_fecho),
                "lote": _num(qtd), "saldo_usd": _num(usd_liq), "pips": _num(pips),
            })
        elif len(campos) == 8:
            # Formato básico (sem horário de entrada).
            simbolo, direcao, hora_fecho, preco_entrada, preco_fecho, qtd, usd_liq, _saldo_conta = campos
            tipo_operacao = _direcao(direcao)
            if tipo_operacao is None:
                continue
            linhas.append({
                "ativo": simbolo.strip(), "tipo_operacao": tipo_operacao,
                "datetime_entrada": None, "datetime_saida": _data(hora_fecho),
                "preco_entrada": _num(preco_entrada), "preco_saida": _num(preco_fecho),
                "lote": _num(qtd), "saldo_usd": _num(usd_liq), "pips": None,
            })
        # qualquer outra contagem = cabeçalho/linha de "Totais", ignora

    return pd.DataFrame(linhas, columns=COLUNAS_EXTRATO)


def parsear_extrato_xlsx(conteudo_bytes: bytes) -> pd.DataFrame:
    """Extrai a mesma tabela "Histórico" da versão .xlsx do extrato (aba
    'Records'). Lida por POSIÇÃO de coluna, não por nome — os cabeçalhos
    acentuados vêm com encoding inconsistente dependendo do exportador,
    posição é o único contrato estável. Suporta os dois formatos (básico/
    completo), detectados pelo número de colunas da planilha."""
    df_raw = pd.read_excel(io.BytesIO(conteudo_bytes), sheet_name=0, header=0)
    n_cols = df_raw.shape[1]
    linhas = []
    for _, row in df_raw.iterrows():
        try:
            if n_cols >= 15:
                # Símbolo(0) Direção abertura(1) Direção fecho(2) Horário
                # abertura(3) Hora fecho(4) Preço entrada(5) Preço fecho(6)
                # Quantidade(7) Swap(8) Comissão(9) $ brutos(10) $ líquidos(11)
                # Saldo $(12) Pips(13) Canal(14).
                tipo_operacao = _direcao(str(row.iloc[1]))
                if tipo_operacao is None:
                    continue
                linhas.append({
                    "ativo": str(row.iloc[0]).strip(), "tipo_operacao": tipo_operacao,
                    "datetime_entrada": _data(row.iloc[3]), "datetime_saida": _data(row.iloc[4]),
                    "preco_entrada": float(row.iloc[5]), "preco_saida": float(row.iloc[6]),
                    "lote": float(row.iloc[7]), "saldo_usd": float(row.iloc[11]),
                    "pips": float(row.iloc[13]),
                })
            else:
                # Formato básico: Símbolo(0) Direção abertura(1) Hora
                # fecho(2) Preço entrada(3) Preço fecho(4) Quantidade
                # fecho(5) Volume fecho(6, não usado) $ líquidos(7) Saldo $(8).
                tipo_operacao = _direcao(str(row.iloc[1]))
                if tipo_operacao is None:
                    continue
                linhas.append({
                    "ativo": str(row.iloc[0]).strip(), "tipo_operacao": tipo_operacao,
                    "datetime_entrada": None, "datetime_saida": _data(row.iloc[2]),
                    "preco_entrada": float(row.iloc[3]), "preco_saida": float(row.iloc[4]),
                    "lote": float(row.iloc[5]), "saldo_usd": float(row.iloc[7]), "pips": None,
                })
        except (ValueError, TypeError):
            continue
    return pd.DataFrame(linhas, columns=COLUNAS_EXTRATO)


def parsear_extrato(nome_arquivo: str, conteudo_bytes: bytes) -> pd.DataFrame:
    """Dispatch por extensão — usado direto pelo callback de upload da UI."""
    extensao = nome_arquivo.rsplit('.', 1)[-1].lower() if '.' in nome_arquivo else ''
    if extensao in ("htm", "html"):
        return parsear_extrato_html(conteudo_bytes)
    if extensao == "xlsx":
        return parsear_extrato_xlsx(conteudo_bytes)
    raise ValueError(f"Formato não suportado: .{extensao} (use .htm ou .xlsx)")
