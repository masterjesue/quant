# =============================================================================
# calendario_economico.py — Janelas de notícia de alto impacto (Fase 1)
# =============================================================================
#
# Registrado em 2026-07-02 (ver CHANGELOG.md): Jesué não opera notícia, mas
# confirmou que janelas de alto impacto (payroll, Copom, FOMC) distorcem
# visivelmente a leitura de S/R — foi exatamente um payroll que "gastou" o
# sinal intradiário no caso real WDO 5.231,50 (01-02/07/2026). Esse módulo
# existe pra marcar essas janelas no dashboard, não pra operar a notícia.
#
# IMPORTANTE — este módulo NÃO faz nenhuma chamada de rede (sem dependência
# externa nova no dashboard). As datas abaixo foram obtidas via pesquisa web
# em 2026-07-04, direto das fontes oficiais citadas em cada bloco — não são
# chute. Ainda assim, CONFERIR periodicamente: Copom e FOMC divulgam
# calendário novo uma vez por ano; se faltar o ano corrente numa lista
# abaixo, a função correspondente simplesmente retorna vazio (nunca inventa
# uma data).
# =============================================================================

from __future__ import annotations
import datetime as dt
from zoneinfo import ZoneInfo

BRT = ZoneInfo("America/Sao_Paulo")
ET = ZoneInfo("America/New_York")

# -----------------------------------------------------------------------------
# COPOM (Banco Central do Brasil) — decisão de Selic
# -----------------------------------------------------------------------------
# Fonte: calendário oficial divulgado pelo BCB em 24/06/2025 para todo 2026,
# confirmado via CNN Brasil, InfoMoney, B3 e Poder360 (pesquisa em
# 2026-07-04). Reunião sempre em 2 dias (terça+quarta); decisão anunciada no
# 2º dia, às 18:30 BRT (horário de Brasília, sem variação de DST desde 2019).
HORA_COPOM_BRT = "18:30"
COPOM_POR_ANO: dict[int, list[str]] = {
    2026: [
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
        "2026-08-05", "2026-09-16", "2026-11-04", "2026-12-09",
    ],
}

# -----------------------------------------------------------------------------
# FOMC (Federal Reserve) — decisão de juros dos EUA
# -----------------------------------------------------------------------------
# Fonte: federalreserve.gov/monetarypolicy/fomccalendars.htm (pesquisa em
# 2026-07-04) — as 8 reuniões regulares de 2026. Decisão no 2º dia da
# reunião, anunciada às 14:00 horário de Nova York (ET). Convertida pra BRT
# considerando o horário de verão americano (DST): o Brasil não tem mais
# horário de verão desde 2019, então o offset ET→BRT varia ao longo do ano
# (ver `_horario_evento_brt`, feito via zoneinfo — não precisa de tabela
# separada por época do ano).
HORA_FOMC_ET = "14:00"
FOMC_POR_ANO: dict[int, list[str]] = {
    2026: [
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
        "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    ],
}

# -----------------------------------------------------------------------------
# NFP / Payroll (Bureau of Labor Statistics) — Employment Situation report
# -----------------------------------------------------------------------------
# Regra estável e bem conhecida: 1ª sexta-feira de cada mês, 08:30 ET —
# calculado por fórmula, não por lista fixa (confirmado via pesquisa em
# 2026-07-04: bls.gov confirma "geralmente na 1ª sexta, ou desloca se
# houver feriado federal no meio"). Exceções conhecidas entram em
# `EXCECOES_NFP` abaixo, sobrepondo a fórmula.
HORA_NFP_ET = "08:30"

# Exceção CONFIRMADA (bls.gov/news.release/archives/empsit_07022026.htm,
# pesquisa em 2026-07-04): o relatório de junho/2026 saiu em 02/07/2026
# (quinta-feira), não na "1ª sexta" (03/07/2026) — 04/07/2026 (Independence
# Day) cai numa sexta-feira, empurrando o release um dia pra frente. Essa é
# exatamente a mesma data do payroll que aparece no caso real WDO 5.231,50
# documentado por Jesué (susto de notícia 09:30-09:50 BRT em 02/07/2026) —
# confirma o registro batendo com o dado oficial.
EXCECOES_NFP: dict[str, str] = {
    "2026-07": "2026-07-02",
}


def _horario_evento_brt(data_str: str, hora_str: str, tz_origem: ZoneInfo) -> dt.datetime:
    ingenuo = dt.datetime.strptime(f"{data_str} {hora_str}", "%Y-%m-%d %H:%M")
    return ingenuo.replace(tzinfo=tz_origem).astimezone(BRT)


def _primeira_sexta(ano: int, mes: int) -> dt.date:
    d = dt.date(ano, mes, 1)
    while d.weekday() != 4:  # 4 = sexta-feira
        d += dt.timedelta(days=1)
    return d


def _montar_eventos(ano: int) -> list[dict]:
    eventos = []
    for data_str in COPOM_POR_ANO.get(ano, []):
        eventos.append({
            'nome': 'Copom — decisão Selic', 'pais': 'BR', 'tipo': 'copom',
            'datetime_brt': _horario_evento_brt(data_str, HORA_COPOM_BRT, BRT),
            'fonte': 'BCB — calendário oficial 2026 (divulgado 24/06/2025)',
        })
    for data_str in FOMC_POR_ANO.get(ano, []):
        eventos.append({
            'nome': 'FOMC — decisão de juros (EUA)', 'pais': 'US', 'tipo': 'fomc',
            'datetime_brt': _horario_evento_brt(data_str, HORA_FOMC_ET, ET),
            'fonte': 'federalreserve.gov/monetarypolicy/fomccalendars.htm',
        })
    for mes in range(1, 13):
        chave_excecao = f'{ano:04d}-{mes:02d}'
        if chave_excecao in EXCECOES_NFP:
            data_str = EXCECOES_NFP[chave_excecao]
            fonte = 'bls.gov (confirmado — deslocado por feriado, ver EXCECOES_NFP)'
        else:
            data_str = _primeira_sexta(ano, mes).strftime('%Y-%m-%d')
            fonte = 'regra BLS (1ª sexta do mês, 08:30 ET) — aproximado, conferir bls.gov se precisão exata importar'
        eventos.append({
            'nome': 'NFP — payroll (EUA)', 'pais': 'US', 'tipo': 'nfp',
            'datetime_brt': _horario_evento_brt(data_str, HORA_NFP_ET, ET),
            'fonte': fonte,
        })
    eventos.sort(key=lambda e: e['datetime_brt'])
    return eventos


_cache_por_ano: dict[int, list[dict]] = {}


def obter_eventos(ano: int) -> list[dict]:
    """Todos os eventos conhecidos de um ano. Vazio (não erro) se o ano
    ainda não tiver calendário oficial carregado acima."""
    if ano not in _cache_por_ano:
        _cache_por_ano[ano] = _montar_eventos(ano)
    return _cache_por_ano[ano]


def proximos_eventos(agora: dt.datetime, janela_horas_antes: int = 6,
                      janela_horas_depois: int = 48) -> list[dict]:
    """Eventos dentro de uma janela ao redor de `agora`. `agora` sem
    timezone é tratado como BRT (mesma convenção do resto do sistema)."""
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=BRT)
    eventos = obter_eventos(agora.year) + obter_eventos(agora.year + 1)
    ini = agora - dt.timedelta(hours=janela_horas_antes)
    fim = agora + dt.timedelta(hours=janela_horas_depois)
    return [e for e in eventos if ini <= e['datetime_brt'] <= fim]


def janela_de_noticia_ativa(agora: dt.datetime, margem_minutos: int = 30) -> dict | None:
    """Retorna o evento se `agora` cair dentro de `margem_minutos` (antes ou
    depois) de um evento de alto impacto — usado pro badge no dashboard."""
    if agora.tzinfo is None:
        agora = agora.replace(tzinfo=BRT)
    candidatos = proximos_eventos(agora, janela_horas_antes=2, janela_horas_depois=2)
    margem = dt.timedelta(minutes=margem_minutos)
    for e in candidatos:
        if abs(e['datetime_brt'] - agora) <= margem:
            return e
    return None
