# =============================================================================
# diario_trade.py — Diário de Trade: lógica pura, sem Dash/HTML
# =============================================================================
# Mesmo espírito de `metricas.py`/`calendario_economico.py`: módulo de dados
# compartilhado pelos dois front-ends (dashboard.py e dashboard_98.py), zero
# construção de HTML aqui. Planejado em 08/07/2026 — ver
# `C:\Users\jesue\.claude\plans\t-tulo-super-quant-eager-knuth.md`.
#
# Uma operação = uma linha em `data/base_processada/diario_trades.parquet`
# (via `persistencia.salvar_diario`/`carregar_diario`). Vem de duas fontes:
# migração única do `diario.xlsm` (`fonte_registro='importado_xlsm'`,
# ver `migrar_diario_xlsm.py`) ou lançamento manual no app
# (`fonte_registro='manual'`).
# =============================================================================

from __future__ import annotations

import io
import json
import logging
import math

# Mesmo logger nomeado configurado em dashboard.py/dashboard_98.py (que já
# rodou e anexou o FileHandler antes desta função ser chamada — ver
# `_sincronizar_posicoes_background` nos dois arquivos). Pegar pelo nome
# aqui reaproveita o handler existente sem reconfigurar nada.
_log_sync = logging.getLogger('sync_posicoes')
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
from PIL import Image

import config_backtest as cfg
import persistencia
import mt5_loader

# -----------------------------------------------------------------------------
# VOCABULÁRIOS CANÔNICOS
# -----------------------------------------------------------------------------
# `Estratégia`/`Sala Op.\Análise` na planilha original são texto livre com
# deriva de acentuação (~26 grafias pra ~15-18 conceitos reais, ver
# CHANGELOG.md da migração) — aqui viram um vocabulário fechado, revisado
# pelo Jesué via `migrar_diario_xlsm.py --dry-run` antes da escrita real.

ESTRATEGIA_TAGS_CANONICAS = [
    "suporte_resistencia",
    "lta_ltb",
    "canal",
    "inversao_polaridade",
    "bandeira",
    "pullback_mme21",
    "medias_moveis",
    "triangulo",
    "fibonacci",
    "rompimento",
    "reteste",
    "topo_fundo_duplo",
    "ombro_cabeca_ombro",
    "abc",
    "outro",
]

SALA_ANALISE_CANONICA = [
    "btv_igor",
    "btv_marquinhos",
    "btv_kita",
    "analise_propria",
    "outro",
]

TIPO_OPERACAO = ["long", "short"]

# 'Simulador' NÃO entra aqui — vira a flag `is_simulated` própria (unifica
# o que a planilha original espalhava entre a aba Diario e a aba Sim).
TIPO_SAIDA = ["stop", "breakeven", "gain", "manual"]
# Nomeado porque `status_do_dia` conta stops do dia — comparar com a string
# solta em outro arquivo é o tipo de acoplamento que quebra em silêncio.
TIPO_SAIDA_STOP = "stop"

# Vocabulário ABERTO (não travado) — a UI permite adicionar tags novas além
# desta lista inicial, que só serve de sugestão/autocomplete.
TAGS_SETUP_ERRO_INICIAIS = [
    "entrada_antecipada",
    "entrada_atrasada",
    "sem_confirmacao",
    "contra_tendencia",
    "protecao_tardia",
    "alvo_mal_dimensionado",
    "operou_fora_da_janela",
    "gerenciamento_correto",
    "disciplina_apos_loss",
]

# Vocabulário ABERTO pra "Print Emocional" (mapa emocional, feedback
# diário.txt 2026-07-11) — mesmo espírito de TAGS_SETUP_ERRO_INICIAIS,
# semente inicial pro cadastro de `tags_emocionais` (ver seção de CRUD
# de Estratégias/R:R/Tags mais abaixo).
TAGS_EMOCIONAIS_INICIAIS = [
    "ansiedade",
    "euforia",
    "medo",
    "confianca_excessiva",
    "raiva",
    "frustracao",
    "calma",
    "impaciencia",
    "duvida",
    "foco",
]

# Faixas do "Sentimento de Mercado" (0-100) — mesma escala do Fear & Greed
# Index pesquisado (CNN): 0-24 Medo Extremo, 25-49 Medo, 50 Neutro,
# 51-75 Ganância, 76-100 Ganância Extrema.
# Bug real 2026-07-21 (achado testando o slider ao vivo): as marcas visuais
# do componente ficam em 0/25/50/75/100 (ver `_MARCAS_SENTIMENTO_MERCADO`),
# mas o corte original entre Ganância/Ganância Extrema caía em 74/75 — o
# valor exato 75 (onde a marca diz "Ganância") classificava como "Ganância
# Extrema". As outras 2 marcas (25 e 50) já batiam certinho com o corte de
# faixa; só essa ficou 1 ponto deslocada. Corrigido movendo o corte pra
# 75/76, incluindo a marca na faixa que ela nomeia.
SENTIMENTO_MERCADO_FAIXAS = [
    (0, 24, "Medo Extremo", "red"),
    (25, 49, "Medo", "yellow"),
    (50, 50, "Neutro", "text2"),
    (51, 75, "Ganância", "yellow"),
    (76, 100, "Ganância Extrema", "green"),
]


def classificar_sentimento_mercado(valor) -> tuple[str, str] | None:
    """(nome_da_faixa, chave_de_cor) pro valor 0-100 do slider de
    Sentimento de Mercado — `None` se `valor` não for um número válido
    (campo ainda não preenchido)."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    for minimo, maximo, nome, cor in SENTIMENTO_MERCADO_FAIXAS:
        if minimo <= v <= maximo:
            return nome, cor
    return None

# -----------------------------------------------------------------------------
# ESTRATÉGIAS / PRESETS R:R / TAGS SETUP-ERRO — cadastro editável (aba
# "Estratégias", feedback diário.txt 2026-07-11). Substitui as listas fixas
# acima (`ESTRATEGIA_TAGS_CANONICAS`/`TAGS_SETUP_ERRO_INICIAIS`) por tabelas
# persistidas que o usuário cria/edita/desativa — na 1ª chamada, cada tabela
# é semeada (bootstrap) a partir da lista antiga correspondente, pra todo
# trade histórico continuar resolvendo o mesmo slug de sempre. Nunca
# DELETA de verdade (`ativo=False`): trades antigos podem referenciar o
# slug, sumir da tabela quebraria o Histórico.
# -----------------------------------------------------------------------------

COLUNAS_ESTRATEGIAS = ["id", "slug", "nome", "descricao", "checklist", "ativo", "criado_em"]
COLUNAS_RR_PRESETS = ["id", "nome", "alvo_pontos", "stop_pontos", "breakeven_pontos", "ativo", "criado_em"]
COLUNAS_TAGS_SETUP_ERRO = ["id", "slug", "nome", "tipo", "ativo", "criado_em"]


def _slugificar(nome: str) -> str:
    """snake_case (minúsculas, sem acento — 'á'->'a' via NFKD, espaço/hífen
    vira underscore, remove o que sobrar fora de a-z0-9_) — mesma convenção
    das listas hardcoded originais (ex. 'suporte_resistencia'), mas sem
    simplesmente descartar a letra acentuada (nomes em português têm
    acento o tempo todo; descartar em vez de dobrar pra ascii deixaria o
    slug corrompido, ex. 'estratgia' em vez de 'estrategia')."""
    import re
    import unicodedata
    sem_acento = unicodedata.normalize("NFKD", nome.strip().lower())
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    s = re.sub(r"[\s\-]+", "_", sem_acento)
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s or "sem_nome"


def _proximo_id(df: pd.DataFrame | None) -> int:
    if df is None or df.empty:
        return 1
    return int(df["id"].max()) + 1


def listar_estrategias(somente_ativas: bool = True) -> pd.DataFrame:
    """Cadastro de estratégias — semeia a partir de `ESTRATEGIA_TAGS_
    CANONICAS` na 1ª chamada (tabela ainda não existe em disco)."""
    df = persistencia.carregar_estrategias()
    if df is None:
        agora = datetime.now()
        df = pd.DataFrame([
            {"id": i + 1, "slug": slug, "nome": slug.replace("_", " ").title(),
             "descricao": "", "checklist": [], "ativo": True, "criado_em": agora}
            for i, slug in enumerate(ESTRATEGIA_TAGS_CANONICAS)
        ], columns=COLUNAS_ESTRATEGIAS)
        persistencia.salvar_estrategias(df)
    df = df.reindex(columns=COLUNAS_ESTRATEGIAS)
    if somente_ativas:
        df = df[df["ativo"].astype(bool)]
    return df.sort_values("nome").reset_index(drop=True)


def criar_estrategia(nome: str, descricao: str = "", checklist: list[str] | None = None) -> int | None:
    with persistencia.lock_estrategias() as adquirida:
        if not adquirida:
            return None
        df = persistencia.carregar_estrategias()
        if df is None:
            df = pd.DataFrame(columns=COLUNAS_ESTRATEGIAS)
        nova = {
            "id": _proximo_id(df), "slug": _slugificar(nome), "nome": nome.strip(),
            "descricao": descricao or "", "checklist": list(checklist or []),
            "ativo": True, "criado_em": datetime.now(),
        }
        df = pd.concat([df, pd.DataFrame([nova])], ignore_index=True)
        persistencia.salvar_estrategias(df.reindex(columns=COLUNAS_ESTRATEGIAS))
        return nova["id"]


def editar_estrategia(id_estrategia: int, **campos) -> bool:
    with persistencia.lock_estrategias() as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_estrategias()
        if df is None or id_estrategia not in set(df["id"]):
            return False
        idx = df.index[df["id"] == id_estrategia][0]
        for chave, valor in campos.items():
            if chave in COLUNAS_ESTRATEGIAS:
                df.at[idx, chave] = valor
        persistencia.salvar_estrategias(df)
        return True


def desativar_estrategia(id_estrategia: int) -> bool:
    return editar_estrategia(id_estrategia, ativo=False)


def listar_rr_presets(somente_ativos: bool = True) -> pd.DataFrame:
    df = persistencia.carregar_rr_presets()
    if df is None:
        df = pd.DataFrame(columns=COLUNAS_RR_PRESETS)
        persistencia.salvar_rr_presets(df)
    df = df.reindex(columns=COLUNAS_RR_PRESETS)
    if somente_ativos:
        df = df[df["ativo"].astype(bool)]
    return df.sort_values("nome").reset_index(drop=True)


def obter_rr_preset(id_preset) -> dict | None:
    """Aceita id (int/float) — usado pelo callback que auto-preenche
    Alvo/Stop a partir do valor selecionado no dropdown de R:R."""
    if id_preset is None:
        return None
    df = persistencia.carregar_rr_presets()
    if df is None or df.empty:
        return None
    try:
        linhas = df[df["id"] == int(id_preset)]
    except (TypeError, ValueError):
        return None
    return linhas.iloc[0].to_dict() if not linhas.empty else None


def criar_rr_preset(nome: str, alvo_pontos: float, stop_pontos: float,
                     breakeven_pontos: float | None = None) -> int | None:
    with persistencia.lock_estrategias() as adquirida:
        if not adquirida:
            return None
        df = persistencia.carregar_rr_presets()
        if df is None:
            df = pd.DataFrame(columns=COLUNAS_RR_PRESETS)
        nova = {
            "id": _proximo_id(df), "nome": nome.strip(),
            "alvo_pontos": float(alvo_pontos), "stop_pontos": float(stop_pontos),
            "breakeven_pontos": float(breakeven_pontos) if breakeven_pontos not in (None, "") else None,
            "ativo": True, "criado_em": datetime.now(),
        }
        df = pd.concat([df, pd.DataFrame([nova])], ignore_index=True)
        persistencia.salvar_rr_presets(df.reindex(columns=COLUNAS_RR_PRESETS))
        return nova["id"]


def editar_rr_preset(id_preset: int, **campos) -> bool:
    with persistencia.lock_estrategias() as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_rr_presets()
        if df is None or id_preset not in set(df["id"]):
            return False
        idx = df.index[df["id"] == id_preset][0]
        for chave, valor in campos.items():
            if chave in COLUNAS_RR_PRESETS:
                df.at[idx, chave] = valor
        persistencia.salvar_rr_presets(df)
        return True


def desativar_rr_preset(id_preset: int) -> bool:
    return editar_rr_preset(id_preset, ativo=False)


def listar_tags_setup_erro(somente_ativas: bool = True) -> pd.DataFrame:
    df = persistencia.carregar_tags_setup_erro()
    if df is None:
        agora = datetime.now()
        df = pd.DataFrame([
            {"id": i + 1, "slug": slug, "nome": slug.replace("_", " ").title(),
             "tipo": "ambos", "ativo": True, "criado_em": agora}
            for i, slug in enumerate(TAGS_SETUP_ERRO_INICIAIS)
        ], columns=COLUNAS_TAGS_SETUP_ERRO)
        persistencia.salvar_tags_setup_erro(df)
    df = df.reindex(columns=COLUNAS_TAGS_SETUP_ERRO)
    if somente_ativas:
        df = df[df["ativo"].astype(bool)]
    return df.sort_values("nome").reset_index(drop=True)


def criar_tag_setup_erro(nome: str, tipo: str = "ambos") -> int | None:
    """Reaproveita a tag existente se o slug já foi cadastrado (pode ter
    sido criada ad-hoc na hora de um lançamento, antes deste cadastro
    central existir) — nunca duplica."""
    with persistencia.lock_estrategias() as adquirida:
        if not adquirida:
            return None
        df = persistencia.carregar_tags_setup_erro()
        if df is None:
            df = pd.DataFrame(columns=COLUNAS_TAGS_SETUP_ERRO)
        slug = _slugificar(nome)
        existente = df[df["slug"] == slug] if not df.empty else df
        if not existente.empty:
            return int(existente.iloc[0]["id"])
        nova = {
            "id": _proximo_id(df), "slug": slug, "nome": nome.strip(),
            "tipo": tipo, "ativo": True, "criado_em": datetime.now(),
        }
        df = pd.concat([df, pd.DataFrame([nova])], ignore_index=True)
        persistencia.salvar_tags_setup_erro(df.reindex(columns=COLUNAS_TAGS_SETUP_ERRO))
        return nova["id"]


def editar_tag_setup_erro(id_tag: int, **campos) -> bool:
    with persistencia.lock_estrategias() as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_tags_setup_erro()
        if df is None or id_tag not in set(df["id"]):
            return False
        idx = df.index[df["id"] == id_tag][0]
        for chave, valor in campos.items():
            if chave in COLUNAS_TAGS_SETUP_ERRO:
                df.at[idx, chave] = valor
        persistencia.salvar_tags_setup_erro(df)
        return True


def desativar_tag_setup_erro(id_tag: int) -> bool:
    return editar_tag_setup_erro(id_tag, ativo=False)


COLUNAS_TAGS_EMOCIONAIS = ["id", "slug", "nome", "ativo", "criado_em"]


def listar_tags_emocionais(somente_ativas: bool = True) -> pd.DataFrame:
    """Cadastro de tags de "Print Emocional" (mapa emocional, feedback
    diário.txt 2026-07-11) — semeia de `TAGS_EMOCIONAIS_INICIAIS` na 1ª
    chamada, mesmo padrão de `listar_tags_setup_erro`."""
    df = persistencia.carregar_tags_emocionais()
    if df is None:
        agora = datetime.now()
        df = pd.DataFrame([
            {"id": i + 1, "slug": slug, "nome": slug.replace("_", " ").title(),
             "ativo": True, "criado_em": agora}
            for i, slug in enumerate(TAGS_EMOCIONAIS_INICIAIS)
        ], columns=COLUNAS_TAGS_EMOCIONAIS)
        persistencia.salvar_tags_emocionais(df)
    df = df.reindex(columns=COLUNAS_TAGS_EMOCIONAIS)
    if somente_ativas:
        df = df[df["ativo"].astype(bool)]
    return df.sort_values("nome").reset_index(drop=True)


def criar_tag_emocional(nome: str) -> int | None:
    """Reaproveita a tag existente se o slug já foi cadastrado — nunca
    duplica (mesmo padrão de `criar_tag_setup_erro`)."""
    with persistencia.lock_estrategias() as adquirida:
        if not adquirida:
            return None
        df = persistencia.carregar_tags_emocionais()
        if df is None:
            df = pd.DataFrame(columns=COLUNAS_TAGS_EMOCIONAIS)
        slug = _slugificar(nome)
        existente = df[df["slug"] == slug] if not df.empty else df
        if not existente.empty:
            return int(existente.iloc[0]["id"])
        nova = {
            "id": _proximo_id(df), "slug": slug, "nome": nome.strip(),
            "ativo": True, "criado_em": datetime.now(),
        }
        df = pd.concat([df, pd.DataFrame([nova])], ignore_index=True)
        persistencia.salvar_tags_emocionais(df.reindex(columns=COLUNAS_TAGS_EMOCIONAIS))
        return nova["id"]


def editar_tag_emocional(id_tag: int, **campos) -> bool:
    with persistencia.lock_estrategias() as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_tags_emocionais()
        if df is None or id_tag not in set(df["id"]):
            return False
        idx = df.index[df["id"] == id_tag][0]
        for chave, valor in campos.items():
            if chave in COLUNAS_TAGS_EMOCIONAIS:
                df.at[idx, chave] = valor
        persistencia.salvar_tags_emocionais(df)
        return True


def desativar_tag_emocional(id_tag: int) -> bool:
    return editar_tag_emocional(id_tag, ativo=False)


# -----------------------------------------------------------------------------
# CHECK-IN DIÁRIO / RODA DA VIDA / ESCALA HAWKINS (aba "Bem-Estar", feedback
# diário.txt 2026-07-11) — primeiro caso do projeto de tabela chaveada por
# DATA (não `id_trade`/slug). Check-in é upsert (1 linha por dia, editável
# se o usuário reabrir no mesmo dia); Roda da Vida é append-only (cada
# registro é um retrato no tempo, a Roda "atual" é sempre o mais recente).
# Hawkins não persiste valor calculado — sempre recomputado a partir do
# check-in + trades do dia (mesmo espírito de `avaliar_aderencia_tracados`: nunca
# guardar um número derivado que pode ficar desatualizado).
# -----------------------------------------------------------------------------

COLUNAS_CHECKIN_DIARIO = [
    "data", "sono_qualidade", "sono_hora_dormir", "sono_hora_acordar",
    "sono_duracao_horas", "cafe_qualidade", "autocuidado_minutos",
    "meditacao_minutos", "gratidao_1", "gratidao_2", "gratidao_3",
    "gratidao_4", "gratidao_5", "observacoes",
]


def obter_checkin_dia(data) -> dict | None:
    """`data` aceita `date` ou string ISO. `None` se não houver check-in
    salvo pra esse dia."""
    if isinstance(data, str):
        data = date.fromisoformat(data)
    df = persistencia.carregar_checkin_diario()
    if df is None or df.empty:
        return None
    linhas = df[df["data"] == data]
    return linhas.iloc[0].to_dict() if not linhas.empty else None


def _calcular_duracao_sono(hora_dormir, hora_acordar) -> float | None:
    """Duração do sono em horas a partir de texto 'HH:MM' — assume que
    cruza a meia-noite se a hora de acordar for numericamente menor (ex.:
    dormiu 23:00, acordou 07:00 -> 8h)."""
    if not hora_dormir or not hora_acordar:
        return None
    try:
        h1, m1 = (int(x) for x in str(hora_dormir).strip().split(':'))
        h2, m2 = (int(x) for x in str(hora_acordar).strip().split(':'))
    except (ValueError, AttributeError):
        return None
    diferenca = (h2 * 60 + m2) - (h1 * 60 + m1)
    if diferenca <= 0:
        diferenca += 24 * 60
    return round(diferenca / 60, 1)


def salvar_checkin_dia(data, campos: dict) -> bool:
    """Upsert por data — remove o check-in existente daquele dia (se
    houver) e grava o novo, nunca duplica linha."""
    if isinstance(data, str):
        data = date.fromisoformat(data)
    with persistencia.lock_checkin_diario() as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_checkin_diario()
        if df is None:
            df = pd.DataFrame(columns=COLUNAS_CHECKIN_DIARIO)
        df = df[df["data"] != data]
        nova = {coluna: None for coluna in COLUNAS_CHECKIN_DIARIO}
        nova["data"] = data
        nova.update({k: v for k, v in campos.items() if k in COLUNAS_CHECKIN_DIARIO})
        nova["sono_duracao_horas"] = _calcular_duracao_sono(nova.get("sono_hora_dormir"), nova.get("sono_hora_acordar"))
        df = pd.concat([df, pd.DataFrame([nova])], ignore_index=True)
        persistencia.salvar_checkin_diario(df.reindex(columns=COLUNAS_CHECKIN_DIARIO))
        return True


def listar_checkins(data_ini=None, data_fim=None) -> pd.DataFrame:
    df = persistencia.carregar_checkin_diario()
    if df is None:
        return pd.DataFrame(columns=COLUNAS_CHECKIN_DIARIO)
    df = df.reindex(columns=COLUNAS_CHECKIN_DIARIO)
    if data_ini is not None:
        if isinstance(data_ini, str):
            data_ini = date.fromisoformat(data_ini)
        df = df[df["data"] >= data_ini]
    if data_fim is not None:
        if isinstance(data_fim, str):
            data_fim = date.fromisoformat(data_fim)
        df = df[df["data"] <= data_fim]
    return df.sort_values("data", ascending=False).reset_index(drop=True)


def calcular_medias_emocionais_dia(data) -> dict | None:
    """Média das 5 notas emocionais (Confiança/Humor/Energia/Atenção/
    Presença) dos trades do dia — entrada auxiliar de
    `calcular_nivel_hawkins`. `None` se não houver trade nenhum trade
    naquele dia com nota emocional preenchida."""
    if isinstance(data, str):
        data = date.fromisoformat(data)
    df = listar_trades(data_ini=data, data_fim=data)
    if df is None or df.empty:
        return None
    medias = {}
    for campo in ("emo_confianca", "emo_humor", "emo_energia", "emo_atencao", "emo_presenca"):
        if campo in df.columns:
            serie = pd.to_numeric(df[campo], errors="coerce").dropna()
            if not serie.empty:
                medias[campo] = float(serie.mean())
    return medias or None


# Lista fixa por enquanto (sem CRUD) — modelo clássico do Wheel of Life,
# pesquisado (SistemizeCoach). Roda da Vida é append-only: ver
# `criar_registro_roda_vida`.
AREAS_RODA_VIDA = [
    "saude", "carreira", "financas", "familia",
    "relacionamentos", "lazer", "desenvolvimento_pessoal", "espiritualidade",
]
NOMES_AREAS_RODA_VIDA = {
    "saude": "Saúde", "carreira": "Carreira", "financas": "Finanças",
    "familia": "Família", "relacionamentos": "Relacionamentos",
    "lazer": "Lazer", "desenvolvimento_pessoal": "Desenvolvimento Pessoal",
    "espiritualidade": "Espiritualidade",
}
COLUNAS_RODA_VIDA = ["id", "data", *AREAS_RODA_VIDA, "observacoes"]


def criar_registro_roda_vida(data, campos: dict) -> int | None:
    """Append-only — cada chamada cria um NOVO retrato no tempo, nunca
    sobrescreve um registro anterior (ao contrário do check-in)."""
    if isinstance(data, str):
        data = date.fromisoformat(data)
    with persistencia.lock_checkin_diario() as adquirida:
        if not adquirida:
            return None
        df = persistencia.carregar_roda_vida()
        if df is None:
            df = pd.DataFrame(columns=COLUNAS_RODA_VIDA)
        nova = {"id": _proximo_id(df), "data": data, "observacoes": campos.get("observacoes", "")}
        for area in AREAS_RODA_VIDA:
            nova[area] = campos.get(area)
        df = pd.concat([df, pd.DataFrame([nova])], ignore_index=True)
        persistencia.salvar_roda_vida(df.reindex(columns=COLUNAS_RODA_VIDA))
        return nova["id"]


def listar_registros_roda_vida() -> pd.DataFrame:
    df = persistencia.carregar_roda_vida()
    if df is None:
        return pd.DataFrame(columns=COLUNAS_RODA_VIDA)
    df = df.reindex(columns=COLUNAS_RODA_VIDA)
    return df.sort_values("data", ascending=False).reset_index(drop=True)


def obter_ultimo_registro_roda_vida() -> dict | None:
    df = listar_registros_roda_vida()
    return df.iloc[0].to_dict() if not df.empty else None


# 17 níveis da Escala Hawkins (PDFs em `diario/`, escala logarítmica de
# consciência, 20-1000), cor do gradiente vermelho/laranja/amarelo -> verde
# -> azul -> violeta. Limiar crítico de Coragem (200): abaixo disso a
# pessoa "perde poder", segundo a teoria original.
NIVEIS_HAWKINS = [
    (20, "Vergonha", "#4a0000"),
    (30, "Culpa", "#6b0000"),
    (50, "Apatia", "#8b0000"),
    (75, "Tristeza", "#b22222"),
    (100, "Medo", "#d2691e"),
    (125, "Desejo", "#e08214"),
    (150, "Raiva", "#f4a300"),
    (175, "Orgulho", "#f7d117"),
    (200, "Coragem", "#a3d900"),
    (250, "Neutralidade", "#6fcf3f"),
    (310, "Boa Vontade", "#3fbf6f"),
    (350, "Aceitação", "#2f9e8f"),
    (400, "Razão", "#2f7ea3"),
    (500, "Amor", "#2f5fbf"),
    (540, "Alegria", "#4040bf"),
    (600, "Paz", "#6a3fbf"),
    (700, "Iluminação", "#8f3fd9"),
]
LIMIAR_HAWKINS_CORAGEM = 200


def posicao_normalizada_hawkins(valor: float) -> float:
    """Posição 0-1 de `valor` na MESMA escala logarítmica usada por
    `calcular_nivel_hawkins` (`valor = 20 * 50 ** composto`, invertida aqui:
    `composto = log(valor/20, 50)`). Existe pra garantir que o marcador do
    nível atual e os 17 rótulos fixos de `NIVEIS_HAWKINS` fiquem plotados na
    mesma escala no funil visual (ver
    diario_app_classico.py::_funil_hawkins/diario_app_98.py::_funil_hawkins_98,
    2026-07-15 — substitui o velocímetro/gauge circular anterior)."""
    return math.log(max(valor, 20) / 20) / math.log(50)


def calcular_nivel_hawkins(checkin: dict | None, medias_emocionais_dia: dict | None = None) -> dict | None:
    """
    Proxy NÃO-OFICIAL, "primeiro palpite" — não existe fórmula publicada
    pra combinar autoavaliação numérica num nível de Hawkins; é uma
    adaptação própria, a recalibrar com uso real (mesmo espírito dos
    outros scores do Diário: nunca apresentar como certeza/calibrado).

    Composição: média simples dos campos 1-5 disponíveis (sono_qualidade e
    cafe_qualidade do check-in, mais confianca/humor/energia/atencao/
    presenca médios dos trades do dia quando existirem), normalizada pra
    0-1; autocuidado/meditação entram só como pequeno bônus aditivo (não
    como nota de peso igual às demais). Mapeamento contínuo pra escala log
    de Hawkins via `valor = 20 * 50 ** composto` (composto=0 -> 20,
    composto=1 -> 1000); nome do nível = mais próximo em `NIVEIS_HAWKINS`.
    Retorna `None` se não houver check-in do dia — sem dado, sem gauge,
    nunca um valor inventado.
    """
    if not checkin:
        return None
    notas = []
    for campo in ("sono_qualidade", "cafe_qualidade"):
        v = checkin.get(campo)
        if v is not None and not (isinstance(v, float) and pd.isna(v)):
            notas.append(float(v))
    if medias_emocionais_dia:
        for v in medias_emocionais_dia.values():
            if v is not None and not (isinstance(v, float) and pd.isna(v)):
                notas.append(float(v))
    if not notas:
        return None
    composto = (sum(notas) / len(notas) - 1) / 4  # normaliza escala 1-5 -> 0-1
    composto = min(max(composto, 0.0), 1.0)

    bonus = 0.0
    autocuidado = checkin.get("autocuidado_minutos")
    if autocuidado is not None and not (isinstance(autocuidado, float) and pd.isna(autocuidado)):
        bonus += min(float(autocuidado) / 60.0, 1.0) * 0.05
    meditacao = checkin.get("meditacao_minutos")
    if meditacao is not None and not (isinstance(meditacao, float) and pd.isna(meditacao)):
        bonus += min(float(meditacao) / 30.0, 1.0) * 0.05
    composto = min(composto + bonus, 1.0)

    valor = 20 * (50 ** composto)
    nivel_nome, cor = min(NIVEIS_HAWKINS, key=lambda item: abs(item[0] - valor))[1:]
    return {"valor": round(valor, 1), "nivel_nome": nivel_nome, "cor": cor, "normalizado": composto}


FONTE_MANUAL = "manual"
FONTE_IMPORTADO = "importado_xlsm"

# Ordem canônica de colunas de `diario_trades.parquet` — mantém o schema
# estável entre migração e lançamento manual.
COLUNAS_DIARIO = [
    "id_trade", "data_op", "datetime_entrada", "datetime_saida",
    "ativo", "contrato", "tempo_grafico", "janela_pregao",
    "estrategia_tags", "estrategia_raw",
    "sala_analise", "sala_analise_raw",
    "tipo_operacao", "alvo_pontos", "stop_pontos", "lote",
    "preco_entrada", "preco_saida", "tipo_saida", "is_simulated",
    "resultado_pontos", "saldo_operacao_reais",
    "resumo_operacional", "obs",
    "imagem_arquivo",
    "nivel_sr_referencia", "nivel_sr_tipo", "nivel_sr_forte",
    "snapshot_mercado_json",
    "imagens_adicionais",
    "tags_setup_erro",
    # Fundação do módulo emocional/comportamental (feedback diário.txt,
    # 2026-07-11) — autoavaliação 1-5 estrelas, tags de "print emocional"
    # (mesmo padrão de vocabulário aberto de tags_setup_erro) e Sentimento
    # de Mercado (0-100, mesmas faixas do Fear & Greed Index).
    "emo_confianca", "emo_humor", "emo_energia", "emo_atencao", "emo_presenca",
    "tags_emocionais", "sentimento_mercado",
    "conta", "data_replay",
    "fonte_registro", "criado_em",
]

# Tabela separada da principal (não colunas extras em COLUNAS_DIARIO) —
# mantém o log de trades append-only/imutável; reconciliação é recomputável
# a qualquer momento via `reconciliar_com_mt5`, não é histórico do trade.
COLUNAS_RECONCILIACAO = [
    "id_trade", "mt5_position_id", "mt5_ticket_entrada", "mt5_ticket_saida",
    "mt5_divergencia_reais", "confianca", "mt5_match_status",
    "ativo", "data", "saldo_estimado",
    # Campos completos (2026-07-21) só preenchidos pra `sem_correspondencia_
    # diario` — pré-carregam o pop-up de edição de pendência no Histórico
    # (ver `materializar_pendencia_como_rascunho`) com o que o MT5 já
    # capturou, sem o usuário precisar redigitar nada.
    "contrato", "tipo_operacao", "volume",
    "preco_entrada", "preco_saida", "datetime_entrada", "datetime_saida",
]

# Idem — recomputável via `avaliar_aderencia_tracados`, não é histórico do trade.
COLUNAS_ADERENCIA_TRACADOS = [
    "id_trade", "data_op", "ativo", "nivel_sr_referencia", "nivel_sr_tipo_informado",
    "lado_geometrico", "tracado_preco", "tracado_id", "diferenca_atr",
    "forte", "toques", "prob_bounce", "postura", "magnitude_esperada",
    "combustivel_score", "aproximacao_score", "rejeicao_score", "classificacao",
]


def _proximo_id_trade(df: pd.DataFrame | None) -> int:
    if df is None or df.empty:
        return 1
    return int(df["id_trade"].max()) + 1


def novo_trade(**campos) -> dict:
    """
    Monta um dict com todas as colunas do schema (default None/valor neutro
    onde o chamador não informar), pronto pra virar uma linha de
    `diario_trades.parquet`. Não grava nada em disco — ver `registrar_trade`.
    """
    # Base com None em toda coluna do schema, sobrescrita pelos defaults REAIS
    # (não dá pra usar dict.setdefault depois — a linha de baixo já teria
    # deixado a chave "presente" com valor None, e setdefault só age quando a
    # chave está AUSENTE, nunca aplicaria nada) e por fim pelo que o
    # chamador passou de verdade em `campos`.
    trade = {coluna: None for coluna in COLUNAS_DIARIO}
    trade.update({
        "tempo_grafico": "5 min.",
        "lote": 1,
        "is_simulated": False,
        "estrategia_tags": [],
        "tags_setup_erro": [],
        "fonte_registro": FONTE_MANUAL,
        "criado_em": datetime.now(),
    })
    trade.update(campos)
    # `dcc.Input(type='date')` devolve string ISO ('YYYY-MM-DD'), mas as
    # linhas migradas do xlsm têm `datetime.date` de verdade nessa coluna —
    # concatenar os dois tipos misturados quebra a inferência de schema do
    # pyarrow no `to_parquet` (arrow tenta serializar a coluna toda como
    # date32/int32 e trava na primeira string). Normaliza aqui, na origem.
    for campo_data in ("data_op", "data_replay"):
        valor = trade.get(campo_data)
        if isinstance(valor, str):
            trade[campo_data] = date.fromisoformat(valor)
    return trade


def registrar_trade(
    campos: dict, imagem_bytes: bytes | None = None,
    snapshot_mercado: dict | None = None,
) -> int | None:
    """
    Atribui `id_trade` sequencial e grava um trade novo no diário. Retorna o
    `id_trade` atribuído, ou None se a trava estava ocupada (chamador —
    tipicamente um callback de botão "Salvar" na UI — deve mostrar um erro/
    retry visível, ao contrário do update de candle que pula em silêncio).

    Se `imagem_bytes` for fornecido, salva a imagem e preenche
    `imagem_arquivo` ANTES de gravar a linha — tudo dentro da mesma trava,
    pra nunca deixar um trade órfão sem imagem (ou vice-versa) se algo falhar
    no meio do caminho.

    `snapshot_mercado`, se fornecido, é um dict JSON-serializável (montado
    por `metricas.montar_snapshot_diario()` a partir do estado do motor de
    quant no momento do save) — serializado aqui como string JSON e gravado
    em `snapshot_mercado_json`, imutável, junto da linha do trade. Este
    módulo não sabe de onde o dict veio (zero import do motor ao vivo/
    `estado` global) — só aceita um dict pronto ou None e serializa. Escopo
    desta v1: só CAPTURA — comparação/validação contra `nivel_sr_referencia`
    fica para um ciclo futuro.
    """
    with persistencia.lock_diario() as adquirida:
        if not adquirida:
            return None
        df_atual = persistencia.carregar_diario()
        extra = {}
        if snapshot_mercado is not None:
            extra["snapshot_mercado_json"] = json.dumps(snapshot_mercado, ensure_ascii=False)
        trade = novo_trade(**campos, **extra)
        trade["id_trade"] = _proximo_id_trade(df_atual)
        if imagem_bytes is not None:
            trade["imagem_arquivo"] = salvar_imagem(
                imagem_bytes, trade["id_trade"], trade["ativo"], trade["data_op"]
            )
        df_novo = pd.DataFrame([trade])[COLUNAS_DIARIO]
        df_final = (
            pd.concat([df_atual, df_novo], ignore_index=True)
            if df_atual is not None else df_novo
        )
        persistencia.salvar_diario(df_final)
        return trade["id_trade"]


# Campos que a edição de um trade JÁ GRAVADO pode tocar (ver
# `atualizar_trade`). É uma allowlist de propósito, não uma denylist: tudo
# que descreve a EXECUÇÃO (preços, resultado, saldo, lote, datas, tickets,
# `snapshot_mercado_json`) vem do MT5/da corretora e é fato, não opinião —
# reescrever isso pela UI abriria a porta pro Diário divergir do relatório
# de performance, exatamente o problema que a auditoria anual de 2026-07-28
# levou uma sessão inteira pra fechar. Só entra aqui o que é LEITURA do
# trade: classificação, texto, contexto emocional.
CAMPOS_EDITAVEIS_TRADE = (
    "estrategia_tags", "janela_pregao", "sala_analise",
    "resumo_operacional", "obs",
    "nivel_sr_referencia", "nivel_sr_tipo", "nivel_sr_forte",
    "tags_setup_erro", "tags_emocionais", "sentimento_mercado",
    "emo_confianca", "emo_humor", "emo_energia", "emo_atencao", "emo_presenca",
)

# Subconjunto de `CAMPOS_EDITAVEIS_TRADE` guardado como lista no parquet.
COLUNAS_LISTA_TRADE = ("estrategia_tags", "tags_setup_erro", "tags_emocionais")


def atualizar_trade(id_trade: int, campos: dict) -> bool:
    """
    Edita um trade JÁ FINALIZADO em `diario_trades.parquet` — a contraparte
    de `atualizar_rascunho`, que só mexe em `posicoes_abertas.parquet` e
    portanto não alcança nada que já virou registro permanente.

    Pedido do Jesué em 2026-07-30, junto da classificação retroativa das
    estratégias: "implemente o botão Editar dentro do histórico para que eu
    possa posteriormente avaliar os trades fora das estratégias e
    reclassificá-las". Sem isto, uma classificação errada (ou uma proposta
    automática que ele quisesse corrigir) só teria conserto editando o
    parquet na mão.

    Só aceita as chaves de `CAMPOS_EDITAVEIS_TRADE` — as demais são
    ignoradas em silêncio (o chamador é a UI, que nunca deveria mandar
    outras; se mandar, é bug dela e não pode corromper o histórico).

    Retorna False se a trava estava ocupada ou se `id_trade` não existe.
    """
    campos_validos = {k: v for k, v in (campos or {}).items() if k in CAMPOS_EDITAVEIS_TRADE}
    if not campos_validos:
        return False

    with persistencia.lock_diario(
            tentativas=cfg.DIARIO_LOCK_USUARIO_TENTATIVAS,
            espera_segundos=cfg.DIARIO_LOCK_USUARIO_ESPERA_S) as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_diario()
        if df is None or df.empty:
            return False
        df = df.reindex(columns=COLUNAS_DIARIO)
        alvo = df.index[df["id_trade"] == int(id_trade)]
        if len(alvo) == 0:
            return False
        idx = alvo[0]

        for chave, valor in campos_validos.items():
            if chave in COLUNAS_LISTA_TRADE:
                # Colunas de lista voltam do parquet como numpy array —
                # normaliza pra não gravar uma string solta onde o resto do
                # código itera esperando lista. E força dtype=object ANTES do
                # `.at`: se a coluna estiver 100% vazia ela vem como float64,
                # e atribuir uma lista nela levanta ValueError.
                valor = _para_lista(valor)
                if df[chave].dtype != object:
                    df[chave] = df[chave].astype(object)
            df.at[idx, chave] = valor

        persistencia.salvar_diario(df)
        return True


def salvar_imagem(conteudo_bytes: bytes, id_trade: int, ativo: str, data_op: date | datetime) -> str:
    """
    Salva um print (upload ou colado do clipboard) em
    `data/base_processada/diario_imagens/`, redimensionando pra no máximo
    `cfg.DIARIO_IMAGEM_LARGURA_MAX` de largura (evita crescimento sem
    controle do volume a cada print colado em resolução nativa). Retorna só
    o NOME do arquivo (não o path completo) — resolver o path sempre via
    `cfg.PASTA_DIARIO_IMAGENS / nome`.
    """
    cfg.PASTA_DIARIO_IMAGENS.mkdir(parents=True, exist_ok=True)

    imagem = Image.open(io.BytesIO(conteudo_bytes)).convert("RGB")
    if imagem.width > cfg.DIARIO_IMAGEM_LARGURA_MAX:
        nova_altura = int(imagem.height * cfg.DIARIO_IMAGEM_LARGURA_MAX / imagem.width)
        imagem = imagem.resize((cfg.DIARIO_IMAGEM_LARGURA_MAX, nova_altura), Image.LANCZOS)

    data_str = pd.Timestamp(data_op).strftime("%Y%m%d")
    nome_arquivo = f"{id_trade:04d}_{ativo}_{data_str}.png"
    imagem.save(cfg.PASTA_DIARIO_IMAGENS / nome_arquivo, format="PNG")
    return nome_arquivo


def listar_trades(
    ativo: str | None = None,
    janela: str | None = None,
    estrategia_tag: str | None = None,
    data_ini=None,
    data_fim=None,
    is_simulated: bool | None = None,
) -> pd.DataFrame:
    """Carrega o diário e aplica filtros opcionais. DataFrame vazio se nada existir ainda."""
    df = persistencia.carregar_diario()
    if df is None:
        return pd.DataFrame(columns=COLUNAS_DIARIO)

    # `COLUNAS_DIARIO` pode ganhar colunas novas (ex: `snapshot_mercado_json`)
    # depois que o parquet em disco já foi escrito pela última vez — o union
    # de schema do `pd.concat` em `registrar_trade` só preenche a coluna nova
    # quando um trade NOVO é gravado, não retroativamente no arquivo parado.
    # `reindex` garante que toda coluna do schema atual sempre exista aqui
    # (preenchida com NaN/None onde faltar), sem exigir gravar nada em disco.
    df = df.reindex(columns=COLUNAS_DIARIO)

    if ativo is not None:
        df = df[df["ativo"] == ativo]
    if janela is not None:
        df = df[df["janela_pregao"] == janela]
    if estrategia_tag is not None:
        # `estrategia_tags` volta do parquet como numpy array, não list Python
        # (arrow serializa listas assim) — `isinstance(tags, list)` nunca é
        # True pra dado real, o filtro sempre voltava vazio. `in` funciona
        # igual sobre array ou list, só a checagem de tipo precisa mudar.
        df = df[df["estrategia_tags"].apply(
            lambda tags: hasattr(tags, "__iter__") and estrategia_tag in tags
        )]
    if data_ini is not None or data_fim is not None:
        # `data_op` guarda `datetime.date` puro — comparar direto com
        # `pd.Timestamp` levanta TypeError (date vs datetime não compara em
        # Python puro). Normaliza os dois lados pra `date` antes de comparar.
        datas = df["data_op"].apply(lambda d: pd.Timestamp(d).date() if d is not None else None)
        if data_ini is not None:
            df = df[datas >= pd.Timestamp(data_ini).date()]
            datas = datas[df.index]
        if data_fim is not None:
            df = df[datas <= pd.Timestamp(data_fim).date()]
    if is_simulated is not None:
        df = df[df["is_simulated"] == is_simulated]

    return df.sort_values("data_op").reset_index(drop=True)


def _maior_sequencia(sinais: pd.Series, alvo: int) -> int:
    """Maior número de ocorrências consecutivas de `alvo` (1=win, -1=loss) na série, em ordem cronológica."""
    maior = atual = 0
    for s in sinais:
        if s == alvo:
            atual += 1
            maior = max(maior, atual)
        else:
            atual = 0
    return maior


def _agrupar_soma_contagem(df: pd.DataFrame, coluna: str) -> dict:
    """`{valor_da_coluna: {'n': contagem, 'saldo': soma de saldo_operacao_reais}}`, ordenado por saldo desc."""
    agrupado = df.groupby(coluna)["saldo_operacao_reais"].agg(["count", "sum"])
    agrupado = agrupado.sort_values("sum", ascending=False)
    return {
        str(indice): {"n": int(linha["count"]), "saldo": float(linha["sum"])}
        for indice, linha in agrupado.iterrows()
    }


def calcular_estatisticas(
    df: pd.DataFrame | None = None,
    ano: int | None = None,
    mes: int | None = None,
) -> dict:
    """
    Estatísticas agregadas equivalentes à aba "Dashboard" da planilha
    original — win rate, payoff, expectância matemática, sequências,
    breakdowns por ativo/janela/sala/estratégia, evolução de saldo. Trades
    simulados (`is_simulated=True`) são SEMPRE excluídos, mesmo critério da
    planilha (`COUNTIFS(...,"<>Simulador")`) — estatística real de
    performance não deve incluir prática/replay.

    `ano`/`mes` filtram `data_op` antes de agregar (usado pelo seletor de
    mês do calendário e pelo toggle "Ano Corrente" da aba Estatísticas).
    Sem os dois, o cálculo é sobre o histórico inteiro (comportamento
    anterior, preservado).

    Se `df` não for passado, carrega o diário inteiro via `listar_trades()`.
    """
    if df is None:
        df = listar_trades()

    reais = df[~df["is_simulated"].astype(bool)].copy()

    if ano is not None or mes is not None:
        data_op_dt = pd.to_datetime(reais["data_op"], errors="coerce")
        mascara = pd.Series(True, index=reais.index)
        if ano is not None:
            mascara &= data_op_dt.dt.year == ano
        if mes is not None:
            mascara &= data_op_dt.dt.month == mes
        reais = reais[mascara]

    n = len(reais)

    vazio = {
        "num_operacoes": 0, "saldo_total": 0.0, "max_gain": 0.0, "max_loss": 0.0,
        "ganho_total": 0.0, "perda_total": 0.0,
        "taxa_acerto": 0.0, "payoff": None, "expectativa": 0.0,
        "n_win": 0, "n_loss": 0, "n_breakeven": 0,
        "maior_sequencia_win": 0, "maior_sequencia_loss": 0,
        "por_ativo": {}, "por_janela": {}, "por_sala": {}, "por_estrategia": {},
        "por_tags_setup_erro": {}, "por_dia": {}, "por_mes": {}, "por_ano": {},
        "drawdown_maximo_reais": 0.0,
        "evolucao_data": [], "evolucao_saldo_acumulado": [],
    }
    if n == 0:
        return vazio

    reais = reais.sort_values("data_op")
    saldo = reais["saldo_operacao_reais"].astype(float)

    vencedoras = reais[saldo > 0]
    perdedoras = reais[saldo < 0]

    n_win = len(vencedoras)
    n_loss = len(perdedoras)
    n_be = n - n_win - n_loss

    taxa_acerto = n_win / n
    media_ganho = float(vencedoras["saldo_operacao_reais"].mean()) if n_win else 0.0
    media_perda = float(perdedoras["saldo_operacao_reais"].mean()) if n_loss else 0.0
    payoff = abs(media_ganho / media_perda) if media_perda else None
    expectativa = taxa_acerto * media_ganho + (1 - taxa_acerto) * media_perda

    sinais = saldo.apply(lambda v: 1 if v > 0 else (-1 if v < 0 else 0))

    tags_explodidas = reais.explode("estrategia_tags")
    tags_explodidas = tags_explodidas[tags_explodidas["estrategia_tags"].notna()]

    tags_erro_explodidas = reais.explode("tags_setup_erro")
    tags_erro_explodidas = tags_erro_explodidas[tags_erro_explodidas["tags_setup_erro"].notna()]

    data_op_dt_agrupamento = pd.to_datetime(reais["data_op"], errors="coerce")
    reais["mes_op"] = data_op_dt_agrupamento.dt.strftime("%Y-%m")
    reais["ano_op"] = data_op_dt_agrupamento.dt.strftime("%Y")

    evolucao = reais[["data_op", "saldo_operacao_reais"]].copy()
    evolucao["saldo_acumulado"] = evolucao["saldo_operacao_reais"].astype(float).cumsum()

    # Drawdown máximo pico-vale em R$, não em % (desvio deliberado do plano
    # original, que seguia a definição textual do Zella Score). Testado
    # contra o parquet real: a versão em % explode pra valores sem sentido
    # (ex: -1185%) porque o "pico" nos primeiros dias da série é um saldo
    # acumulado pequeno (dezenas de R$), e o TradeZella só consegue expressar
    # essa métrica em % porque eles têm saldo de CONTA sincronizado do
    # corretor como denominador — este projeto não rastreia capital/conta,
    # só P&L acumulado a partir de zero. R$ é robusto a isso e já é a
    # unidade de todo o resto do dict (saldo_total/max_gain/max_loss).
    pico_corrente = evolucao["saldo_acumulado"].cummax()
    drawdown_maximo_reais = float((evolucao["saldo_acumulado"] - pico_corrente).min())

    return {
        "num_operacoes": n,
        "saldo_total": float(saldo.sum()),
        "max_gain": float(saldo.max()),
        "max_loss": float(saldo.min()),
        # Ganho Total / Perda Total (2026-07-30, pedido do Jesué: "está
        # faltando"): soma de TODOS os trades vencedores/perdedores, distinto
        # de max_gain/max_loss (o melhor/pior trade ÚNICO). saldo_total já
        # existia, mas sem os dois lados separados não dá pra ver, por
        # exemplo, se o total positivo é grande e os losses é que destroem —
        # ou o contrário.
        "ganho_total": float(vencedoras["saldo_operacao_reais"].sum()) if n_win else 0.0,
        "perda_total": float(perdedoras["saldo_operacao_reais"].sum()) if n_loss else 0.0,
        "taxa_acerto": taxa_acerto,
        "payoff": payoff,
        "expectativa": expectativa,
        "n_win": n_win, "n_loss": n_loss, "n_breakeven": n_be,
        "maior_sequencia_win": _maior_sequencia(sinais, 1),
        "maior_sequencia_loss": _maior_sequencia(sinais, -1),
        "por_ativo": _agrupar_soma_contagem(reais, "ativo"),
        "por_janela": _agrupar_soma_contagem(reais, "janela_pregao"),
        "por_sala": _agrupar_soma_contagem(reais, "sala_analise"),
        "por_estrategia": _agrupar_soma_contagem(tags_explodidas, "estrategia_tags"),
        "por_tags_setup_erro": _agrupar_soma_contagem(tags_erro_explodidas, "tags_setup_erro"),
        "por_dia": _agrupar_soma_contagem(reais, "data_op"),
        "por_mes": _agrupar_soma_contagem(reais, "mes_op"),
        "por_ano": _agrupar_soma_contagem(reais, "ano_op"),
        "drawdown_maximo_reais": drawdown_maximo_reais,
        "evolucao_data": evolucao["data_op"].astype(str).tolist(),
        "evolucao_saldo_acumulado": evolucao["saldo_acumulado"].tolist(),
    }


# Pesos/caps de `calcular_score_confiabilidade` — primeiro palpite (pesos
# iguais entre os 4 componentes, caps de payoff/profit_factor em 2.5),
# mesmo espírito das constantes ADERENCIA_SR_ATR_* acima: ajustar depois
# de conferir os setups com amostra real contra o que Jesué já sabe
# empiricamente sobre cada um.
SCORE_MIN_AMOSTRA = 10
SCORE_PESO_TAXA_ACERTO = 0.25
SCORE_PESO_PAYOFF = 0.25
SCORE_PESO_PROFIT_FACTOR = 0.25
SCORE_PESO_CONSISTENCIA = 0.25
SCORE_CAP_PAYOFF = 2.5
SCORE_CAP_PROFIT_FACTOR = 2.5


def _tags_para_pontuar() -> list[str]:
    """Vocabulário que o Score de Confiabilidade percorre.

    BUG REAL corrigido em 2026-07-30: isto era `ESTRATEGIA_TAGS_CANONICAS`
    — a lista de BOOTSTRAP, congelada no código, usada só pra semear a
    tabela de estratégias na primeira execução. Como o cadastro é editável
    desde então (criar/desativar pela aba Estratégias), as duas listas
    divergiram: "Score de Confiabilidade" mostrava o vocabulário velho e
    "Por Estratégia" mostrava o cadastro, com nomes diferentes para os
    mesmos trades — exatamente a discordância entre painéis que o Jesué
    reportou. Depois da reclassificação de 2026-07-30 seria pior ainda: a
    tabela de score listaria 15 slugs todos com n=0.

    Fonte da verdade agora é o cadastro ATIVO, mais qualquer slug que
    apareça nos trades (para nunca esconder resultado de um setup que foi
    desativado depois de já ter sido usado).
    """
    ativas = [str(s) for s in listar_estrategias()["slug"]]
    return ativas


def calcular_score_confiabilidade(df: pd.DataFrame | None = None, min_amostra: int = SCORE_MIN_AMOSTRA) -> dict:
    """
    Score de Confiabilidade 0-100 por `estrategia_tags` (não por conta
    inteira, como o Zella Score original que inspirou isso — ver
    `analise-tradezella-para-super-quant.md` seção 3). Combina taxa de
    acerto, payoff, profit factor e um proxy de consistência (maior
    sequência de perdas relativa à amostra), em peso igual.

    Setups com `n < min_amostra` voltam com `score=None` e
    `amostra_suficiente=False` — os números brutos aparecem mesmo assim
    (referência), só o score não é calculado. Com o volume atual de
    trades, isso é a maioria dos casos, não uma borda rara.

    NÃO inclui "disciplina/aderência a regras" (6º componente do Zella
    Score original) — não há Playbook/regra formal cadastrada ainda,
    fabricar esse componente sem dado real seria pior que omiti-lo.
    """
    if df is None:
        df = listar_trades()
    reais = df[~df["is_simulated"].astype(bool)].copy()
    reais = reais.sort_values("data_op")
    tags_explodidas = reais.explode("estrategia_tags")
    tags_explodidas = tags_explodidas[tags_explodidas["estrategia_tags"].notna()]

    # Slug usado em algum trade mas já desativado no cadastro continua
    # aparecendo — esconder resultado real seria pior que uma linha a mais.
    usadas = {str(t) for t in tags_explodidas["estrategia_tags"].unique()}
    vocabulario = list(dict.fromkeys(_tags_para_pontuar() + sorted(usadas)))

    resultado = {}
    for tag in vocabulario:
        subset = tags_explodidas[tags_explodidas["estrategia_tags"] == tag]
        n = len(subset)
        if n == 0:
            resultado[tag] = {
                "n": 0, "taxa_acerto": None, "payoff": None, "profit_factor": None,
                "maior_sequencia_loss": 0, "score": None, "amostra_suficiente": False,
            }
            continue

        saldo = subset["saldo_operacao_reais"].astype(float)
        vencedoras = saldo[saldo > 0]
        perdedoras = saldo[saldo < 0]
        n_win = len(vencedoras)
        n_loss = len(perdedoras)

        taxa_acerto = n_win / n
        media_ganho = float(vencedoras.mean()) if n_win else 0.0
        media_perda = float(perdedoras.mean()) if n_loss else 0.0
        payoff = abs(media_ganho / media_perda) if media_perda else None
        soma_ganhos = float(vencedoras.sum())
        soma_perdas = float(perdedoras.sum())
        profit_factor = abs(soma_ganhos / soma_perdas) if soma_perdas else None

        sinais = saldo.apply(lambda v: 1 if v > 0 else (-1 if v < 0 else 0))
        maior_sequencia_loss = _maior_sequencia(sinais, -1)

        amostra_suficiente = n >= min_amostra
        score = None
        if amostra_suficiente:
            comp_taxa_acerto = taxa_acerto * 100
            comp_payoff = min((payoff or SCORE_CAP_PAYOFF) / SCORE_CAP_PAYOFF, 1.0) * 100
            comp_profit_factor = min((profit_factor or SCORE_CAP_PROFIT_FACTOR) / SCORE_CAP_PROFIT_FACTOR, 1.0) * 100
            comp_consistencia = (1 - maior_sequencia_loss / n) * 100
            score = (
                comp_taxa_acerto * SCORE_PESO_TAXA_ACERTO
                + comp_payoff * SCORE_PESO_PAYOFF
                + comp_profit_factor * SCORE_PESO_PROFIT_FACTOR
                + comp_consistencia * SCORE_PESO_CONSISTENCIA
            )

        resultado[tag] = {
            "n": n, "taxa_acerto": taxa_acerto, "payoff": payoff, "profit_factor": profit_factor,
            "maior_sequencia_loss": maior_sequencia_loss, "score": score,
            "amostra_suficiente": amostra_suficiente,
        }
    return resultado


def detalhe_estrategia(tag: str, df: pd.DataFrame | None = None) -> dict:
    """
    Tudo o que a linha EXPANDIDA do bloco "Confiabilidade por Estratégia"
    mostra sobre um setup — pedido do Jesué em 2026-07-30: "coloque uma seta
    no canto da linha para expandir os detalhes da estratégia, trazendo mais
    estatísticas (as mesmas do cabeçalho e outras que você achar
    importante)".

    Mora aqui, e não na UI, porque os dois skins renderizam o MESMO bloco —
    é a regra da arquitetura: lógica de dado em `diario_trade`, só a
    construção visual em `diario_app_*`.

    Além do que o cabeçalho de Estatísticas já traz (saldo, W/L/BE, taxa de
    acerto, payoff, expectativa, max gain/loss, drawdown, sequências),
    devolve três recortes que a amostra atual mostrou serem os que mais
    discriminam resultado neste Diário:
      - **por direção** (compra × venda): o mesmo setup pode ser lucrativo
        de um lado e ruinoso do outro — foi o achado mais forte do estudo
        de 2026-07-30;
      - **por janela de pregão**: `almoco` é a única janela positiva;
      - **por ativo**.
    Mais o `zero_a_zero`: 31% dos trades morrem exatamente em R$ 0,00
    (breakeven), então essa contagem é indispensável pra ler a taxa de
    acerto sem se enganar.
    """
    if df is None:
        df = listar_trades()
    reais = df[~df["is_simulated"].astype(bool)].copy()
    reais = reais[reais["estrategia_tags"].apply(
        lambda tags: hasattr(tags, "__iter__") and not isinstance(tags, str) and tag in list(tags)
    )]
    reais = reais.sort_values("data_op")

    vazio = {
        "n": 0, "saldo_total": 0.0, "n_win": 0, "n_loss": 0, "n_breakeven": 0,
        "taxa_acerto": None, "payoff": None, "expectativa": None,
        "max_gain": None, "max_loss": None, "drawdown_maximo_reais": 0.0,
        "maior_sequencia_win": 0, "maior_sequencia_loss": 0,
        "zero_a_zero": 0, "primeiro": None, "ultimo": None,
        "por_direcao": {}, "por_janela": {}, "por_ativo": {},
        "melhor_trade": None, "pior_trade": None,
    }
    if reais.empty:
        return vazio

    saldo = reais["saldo_operacao_reais"].astype(float)
    vencedoras, perdedoras = saldo[saldo > 0], saldo[saldo < 0]
    n, n_win, n_loss = len(reais), len(vencedoras), len(perdedoras)
    media_perda = float(perdedoras.mean()) if n_loss else 0.0

    acumulado = saldo.cumsum()
    drawdown = float((acumulado - acumulado.cummax()).min()) if n else 0.0
    sinais = saldo.apply(lambda v: 1 if v > 0 else (-1 if v < 0 else 0))

    def recorte(coluna: str) -> dict:
        sub = reais.copy()
        sub[coluna] = sub[coluna].fillna("(sem)")
        agrupado = sub.groupby(coluna)["saldo_operacao_reais"].agg(["count", "sum", "mean"])
        return {
            str(k): {"n": int(v["count"]), "saldo": float(v["sum"]), "media": float(v["mean"])}
            for k, v in agrupado.sort_values("sum", ascending=False).iterrows()
        }

    def extremo(indice) -> dict | None:
        if indice is None:
            return None
        linha = reais.loc[indice]
        return {
            "id_trade": int(linha["id_trade"]), "data_op": str(linha["data_op"]),
            "ativo": str(linha["ativo"]), "saldo": float(linha["saldo_operacao_reais"]),
        }

    return {
        "n": n,
        "saldo_total": float(saldo.sum()),
        "n_win": n_win, "n_loss": n_loss, "n_breakeven": int((saldo == 0).sum()),
        "taxa_acerto": n_win / n,
        "payoff": abs(float(vencedoras.mean()) / media_perda) if (n_win and media_perda) else None,
        "expectativa": float(saldo.mean()),
        "max_gain": float(saldo.max()), "max_loss": float(saldo.min()),
        "drawdown_maximo_reais": drawdown,
        "maior_sequencia_win": _maior_sequencia(sinais, 1),
        "maior_sequencia_loss": _maior_sequencia(sinais, -1),
        "zero_a_zero": int((saldo == 0).sum()),
        "primeiro": str(reais["data_op"].iloc[0]),
        "ultimo": str(reais["data_op"].iloc[-1]),
        "por_direcao": recorte("tipo_operacao"),
        "por_janela": recorte("janela_pregao"),
        "por_ativo": recorte("ativo"),
        "melhor_trade": extremo(saldo.idxmax()),
        "pior_trade": extremo(saldo.idxmin()),
    }


def status_do_dia(data=None, df: pd.DataFrame | None = None) -> dict:
    """
    Guarda-corpo operacional: quantas operações já saíram HOJE, quantos stops,
    qual o saldo, e o veredito ('livre' / 'atencao' / 'critico').

    Existe por causa do maior achado do estudo de 2026-07-30: dias com 1-2
    operações somaram +R$ 328, dias com 3-4 somaram -R$ 137, e dias com 5 ou
    mais destruíram R$ 627 em apenas 12 dias — mais do que a perda do ano
    inteiro. O plano do mês vira letra morta se esse número só aparecer no
    relatório do dia seguinte; ele precisa estar na tela ANTES da próxima
    entrada.

    É AVISO, não bloqueio — o sistema não impede nada. Mesmo princípio do
    Modo Aprendizagem: mede e mostra, quem decide é o Jesué.

    Conta trades JÁ FINALIZADOS (`diario_trades.parquet`) do dia, sem
    simulados. Posições ainda abertas não entram: a decisão que este bloco
    apoia é "abro mais uma?", e uma posição viva ainda não é uma operação
    concluída — mas ela aparece na tela logo acima, então o Jesué a vê.
    """
    dia = pd.Timestamp(data).date() if data is not None else date.today()
    if df is None:
        df = listar_trades(is_simulated=False)
    if df.empty:
        do_dia = df
    else:
        datas = df["data_op"].apply(lambda d: pd.Timestamp(d).date() if d is not None else None)
        do_dia = df[datas == dia]

    n = len(do_dia)
    saldo = float(do_dia["saldo_operacao_reais"].fillna(0).sum()) if n else 0.0
    stops = int((do_dia["tipo_saida"] == TIPO_SAIDA_STOP).sum()) if n else 0

    if n >= cfg.DIARIO_TRADES_DIA_CRITICO or stops >= cfg.DIARIO_LIMITE_STOPS_DIA:
        estado = "critico"
    elif n >= cfg.DIARIO_LIMITE_TRADES_DIA:
        estado = "atencao"
    else:
        estado = "livre"

    motivos = []
    if stops >= cfg.DIARIO_LIMITE_STOPS_DIA:
        motivos.append(f"{stops} stops no dia — o plano manda encerrar aqui.")
    if n >= cfg.DIARIO_TRADES_DIA_CRITICO:
        motivos.append(
            f"{n}ª operação do dia. Dias com {cfg.DIARIO_TRADES_DIA_CRITICO}+ trades "
            f"somaram prejuízo em toda a amostra."
        )
    elif n >= cfg.DIARIO_LIMITE_TRADES_DIA:
        motivos.append(
            f"Limite de {cfg.DIARIO_LIMITE_TRADES_DIA} operações/dia atingido. "
            f"Dias de 1-2 trades são os únicos lucrativos da amostra."
        )

    return {
        "data": str(dia),
        "n_trades": n,
        "n_stops": stops,
        "saldo": saldo,
        "limite": cfg.DIARIO_LIMITE_TRADES_DIA,
        "restantes": max(cfg.DIARIO_LIMITE_TRADES_DIA - n, 0),
        "estado": estado,
        "motivos": motivos,
    }


def avaliar_contexto_operacao(estrategias, ativo=None, janela=None) -> list[dict]:
    """
    Avisos sobre a operação que está sendo montada AGORA — setup suspenso,
    ativo/janela fora do contexto que se provou. Devolve uma lista de
    `{'nivel': 'alerta'|'info'|'ok', 'texto': ...}`, vazia quando não há nada
    a dizer.

    Mesma natureza de `status_do_dia`: é o estudo de 2026-07-30 falando na
    hora da decisão, não uma regra que o sistema imponha.
    """
    tags = [str(t) for t in (estrategias or [])]
    avisos = []

    suspensas = [t for t in tags if t in cfg.DIARIO_ESTRATEGIAS_SUSPENSAS]
    if suspensas:
        nomes = _nomes_de_slugs(suspensas)
        avisos.append({
            "nivel": "alerta",
            "texto": f"{', '.join(nomes)} está na lista de setups suspensos "
                     f"(expectativa negativa medida). Confirme que vale a exceção.",
        })

    vencedoras = [t for t in tags if t in cfg.DIARIO_ESTRATEGIAS_VENCEDORAS]
    if vencedoras:
        avisos.append({
            "nivel": "ok",
            "texto": f"{', '.join(_nomes_de_slugs(vencedoras))} — setup com expectativa positiva medida.",
        })

    if janela and janela != cfg.DIARIO_JANELA_PREFERIDA:
        avisos.append({
            "nivel": "info",
            "texto": f"Fora da janela do {cfg.DIARIO_JANELA_PREFERIDA} — prime e tarde "
                     f"somaram -R$ 259 na amostra, inclusive nos melhores setups.",
        })
    if ativo and ativo != cfg.DIARIO_ATIVO_PREFERIDO and vencedoras:
        avisos.append({
            "nivel": "info",
            "texto": f"Os setups vencedores renderam muito mais em "
                     f"{cfg.DIARIO_ATIVO_PREFERIDO} do que em {ativo}.",
        })
    return avisos


def _nomes_de_slugs(slugs) -> list[str]:
    """Slug -> nome legível do cadastro, com fallback pro próprio slug."""
    try:
        mapa = {str(r["slug"]): str(r["nome"])
                for _, r in listar_estrategias(somente_ativas=False).iterrows()}
    except Exception:
        mapa = {}
    return [mapa.get(s, s.replace("_", " ").title()) for s in slugs]


def _parear_hedge_deals(posicoes: pd.DataFrame) -> pd.DataFrame:
    """
    Funde pares de posições opostas (long+short, mesmo `raiz_contrato` e
    mesmo `volume`) que vieram de `mt5_loader.agrupar_posicoes` SEM perna
    OUT própria (`preco_saida` nulo) num único round-trip líquido — mesma
    lógica de `detectar_pares_hedge` (conta é hedging, não netting: uma
    ordem OCO/SL-TP que dispara cria uma posição NOVA em vez de fechar a
    original), só que adaptada às colunas de `agrupar_posicoes` (deals
    brutos, `tipo` = 'compra'/'venda', sem coluna `status`).

    Achado real 2026-07-21: sem isso, `reconciliar_com_mt5` reportava CADA
    metade de um hedge como uma "posição sem correspondência no diário"
    separada, sempre com `profit_total=0.0` (nenhuma perna OUT = fallback
    zero de `agrupar_posicoes`) — Jesué viu isso como uma enxurrada de
    pendências no Histórico, TODAS com R$0,00, o que era o sintoma certo
    (64 pendências reais, 100% delas exatamente zero — estatisticamente
    impossível pra day trade real, confirmou o diagnóstico antes de mexer
    no código). Posições que JÁ têm perna OUT própria (`preco_saida`
    preenchido, profit_total real vindo do MT5) não são tocadas aqui.
    """
    if posicoes.empty:
        return posicoes
    orfas = posicoes[posicoes["preco_saida"].isna()].copy()
    fechadas = posicoes[posicoes["preco_saida"].notna()].copy()
    if orfas.empty:
        # Sem perna órfã não há nada pro pareamento antigo fazer — mas as
        # pernas JÁ CRUZADAS pela corretora ainda precisam ser fundidas
        # (bug real 2026-07-27; este `return` antecipado era o que pulava
        # `_fundir_pernas_fechadas_espelhadas` justamente no caso mais comum,
        # que é o dia inteiro já fechado).
        return _fundir_pernas_fechadas_espelhadas(fechadas)

    usados = set()
    fundidas = []
    # Agrupa por CONTRATO exato (WDOQ26), não pela raiz (WDO) — senão pernas
    # de vencimentos diferentes podiam virar um "par". E as 2 pernas precisam
    # ter aberto no MESMO DIA. Ambas as travas espelham
    # `detectar_pares_hedge` (bug real 2026-07-27: sem elas o pareamento
    # fabricou o trade #167 de −R$225 juntando uma posição de 22/07 com uma de
    # 23/07). Esta função é o gêmeo histórico daquela — as regras têm que ser
    # as mesmas, senão a aba Posição e o Histórico discordam sobre o que é um
    # par.
    for contrato, grupo in orfas.groupby("contrato"):
        raiz = grupo["raiz_contrato"].iloc[0]
        compras = grupo[grupo["tipo"] == "compra"].sort_values("datetime_entrada")
        vendas = grupo[grupo["tipo"] == "venda"].sort_values("datetime_entrada")
        for _, compra in compras.iterrows():
            if compra["position_id"] in usados:
                continue
            candidatas = vendas[
                (vendas["volume"] == compra["volume"]) & (~vendas["position_id"].isin(usados))
            ]
            if candidatas.empty:
                continue
            mesmo_dia = candidatas["datetime_entrada"].apply(
                lambda t: pd.notna(t) and pd.notna(compra["datetime_entrada"])
                and pd.Timestamp(t).date() == pd.Timestamp(compra["datetime_entrada"]).date()
            )
            candidatas = candidatas[mesmo_dia]
            if candidatas.empty:
                continue
            venda = candidatas.iloc[0]
            usados.add(compra["position_id"])
            usados.add(venda["position_id"])

            if compra["datetime_entrada"] <= venda["datetime_entrada"]:
                entrada, saida, tipo_operacao = compra, venda, "long"
            else:
                entrada, saida, tipo_operacao = venda, compra, "short"

            pnl_pts = _pnl_pontos(tipo_operacao, entrada["preco_entrada"], saida["preco_entrada"])
            valor_ponto = cfg.VALOR_PONTO.get(raiz, 0.0)
            profit = pnl_pts * valor_ponto * entrada["volume"]

            fundidas.append({
                "position_id": entrada["position_id"], "raiz_contrato": raiz,
                "contrato": entrada["contrato"],
                "tipo": entrada["tipo"], "preco_entrada": entrada["preco_entrada"],
                "preco_saida": saida["preco_entrada"],
                "datetime_entrada": entrada["datetime_entrada"], "datetime_saida": saida["datetime_entrada"],
                "volume": entrada["volume"], "profit_total": profit,
                "tickets_entrada": entrada["tickets_entrada"], "tickets_saida": saida["tickets_entrada"],
            })

    restantes = orfas[~orfas["position_id"].isin(usados)]
    fechadas = _fundir_pernas_fechadas_espelhadas(fechadas)
    partes = [fechadas]
    if fundidas:
        partes.append(pd.DataFrame(fundidas, columns=posicoes.columns))
    partes.append(restantes)
    return pd.concat(partes, ignore_index=True)


def _fundir_pernas_fechadas_espelhadas(fechadas: pd.DataFrame) -> pd.DataFrame:
    """
    Funde as DUAS pernas de um hedge que a corretora já cruzou (DEAL_ENTRY_
    OUT_BY) — o caso que `_parear_hedge_deals` não cobria.

    BUG REAL 2026-07-27 (achado comparando o Diário com o relatório de
    performance do ProfitPro): `_parear_hedge_deals` só funde pernas SEM
    preço de saída. Quando o BTG cruza as duas pernas, AS DUAS ganham preço
    de saída e escapam do pareamento. Pior: o MT5 devolve `profit=0` nesse
    tipo de fechamento, então `mt5_loader.agrupar_posicoes` recalcula o
    resultado pelo movimento de preço — de cada perna SEPARADAMENTE, como se
    cada uma fosse um round-trip próprio. Uma operação real de −R$30 virava
    duas posições de −R$30, e a que não estava na lápide ficava para sempre
    como "operação sem registro no diário" no Histórico. Foi o que o Jesué
    viu como "ordens fantasmas de hedge".

    Assinatura usada pra identificar o par (bem apertada de propósito, pra
    nunca fundir dois round-trips independentes que por acaso caíram no mesmo
    dia com o mesmo volume): mesmo contrato, mesmo volume, MESMO DIA, direções
    opostas e preços ESPELHADOS — a entrada de uma é a saída da outra e
    vice-versa. Esse espelhamento exato é a marca do cruzamento OUT_BY; dois
    trades independentes não produzem isso.

    O resultado do par é o do trade REAL: entra pela perna que abriu primeiro
    e sai no preço da outra — exatamente o que o relatório da corretora mostra
    numa linha só.
    """
    if fechadas.empty:
        return fechadas

    def mesmo_preco(a, b):
        return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9

    usados = set()
    fundidas = []
    for _, grupo in fechadas.groupby("contrato"):
        linhas = [linha for _, linha in grupo.sort_values("datetime_entrada").iterrows()]
        for i, a in enumerate(linhas):
            if a["position_id"] in usados:
                continue
            for b in linhas[i + 1:]:
                if b["position_id"] in usados:
                    continue
                if a["tipo"] == b["tipo"] or a["volume"] != b["volume"]:
                    continue
                if pd.isna(a["datetime_entrada"]) or pd.isna(b["datetime_entrada"]):
                    continue
                if pd.Timestamp(a["datetime_entrada"]).date() != pd.Timestamp(b["datetime_entrada"]).date():
                    continue
                # Espelhamento exato: entrada de A == saída de B e vice-versa.
                if not (mesmo_preco(a["preco_entrada"], b["preco_saida"])
                        and mesmo_preco(a["preco_saida"], b["preco_entrada"])):
                    continue

                usados.add(a["position_id"])
                usados.add(b["position_id"])
                entrada, saida = (a, b) if a["datetime_entrada"] <= b["datetime_entrada"] else (b, a)
                tipo_operacao = "long" if entrada["tipo"] == "compra" else "short"
                raiz = entrada["raiz_contrato"]
                pnl_pts = _pnl_pontos(tipo_operacao, entrada["preco_entrada"], entrada["preco_saida"])
                profit = pnl_pts * cfg.VALOR_PONTO.get(raiz, 0.0) * entrada["volume"]
                fundidas.append({
                    "position_id": entrada["position_id"], "raiz_contrato": raiz,
                    "contrato": entrada["contrato"], "tipo": entrada["tipo"],
                    "preco_entrada": entrada["preco_entrada"],
                    "preco_saida": entrada["preco_saida"],
                    "datetime_entrada": entrada["datetime_entrada"],
                    "datetime_saida": saida["datetime_saida"],
                    "volume": entrada["volume"], "profit_total": profit,
                    "tickets_entrada": entrada["tickets_entrada"],
                    "tickets_saida": saida["tickets_saida"],
                })
                break

    if not fundidas:
        return fechadas
    sobraram = fechadas[~fechadas["position_id"].isin(usados)]
    return pd.concat([sobraram, pd.DataFrame(fundidas, columns=fechadas.columns)],
                     ignore_index=True)


def reconciliar_com_mt5(dias_atras: int = 30) -> pd.DataFrame:
    """
    Casa trades logados (reais, não simulados) dos últimos `dias_atras`
    dias contra execuções reais da conta MT5 (mesma conexão de
    `mt5_loader.py`), por `(ativo == raiz do contrato, mesma data)` —
    não exige horário/contrato exato (a maioria dos trades ainda não tem
    esses campos preenchidos), então a confiança do casamento fica marcada
    como `'aproximado'` sempre nesta v1 (ver riscos do plano — precisão
    maior só quando `contrato`/`datetime_entrada` existirem de verdade).
    Salva o resultado via `persistencia.salvar_reconciliacao` e retorna.
    """
    fim = datetime.now()
    inicio = fim - timedelta(days=dias_atras)

    df_deals = mt5_loader.buscar_execucoes_periodo(inicio, fim)
    if df_deals is None:
        # Achado real 2026-07-21: MT5 cai e volta sozinho o tempo todo
        # (IPC instável, ver mt5_loader.py) — NUNCA sobrescreve o cache
        # com um resultado vazio quando essa rodada específica não
        # conseguiu falar com o MT5. Antes disso, um ciclo de background
        # rodando bem na hora de uma queda apagava TODAS as pendências
        # reais já detectadas (cache virava 0 linhas), fazendo o botão
        # "Editar" de uma linha PENDENTE ainda visível na tela virar
        # no-op silencioso (`materializar_pendencia_como_rascunho` não
        # achava mais o ticket em `listar_pendencias_diario()`). Mantém o
        # último resultado bom — só o próximo ciclo com MT5 de verdade
        # acessível é que atualiza o cache.
        anterior = persistencia.carregar_reconciliacao()
        return anterior if anterior is not None else pd.DataFrame(columns=COLUNAS_RECONCILIACAO)

    posicoes = mt5_loader.agrupar_posicoes(df_deals)
    posicoes = _parear_hedge_deals(posicoes)
    posicoes["data"] = posicoes["datetime_entrada"].apply(lambda t: t.date() if t is not None else None)
    posicoes["usado"] = False

    trades = listar_trades(data_ini=inicio.date(), data_fim=fim.date(), is_simulated=False)

    linhas = []

    # PASSO 0 — casamento IDENTITÁRIO pela lápide, antes de qualquer heurística.
    #
    # BUG REAL 2026-07-27 (achado comparando o Diário com o relatório de
    # performance do ProfitPro: 46 operações contra 42): o casamento abaixo é
    # por `(ativo, data)` e consome UMA posição por trade. Só que um par de
    # hedge são DUAS posições do MT5 que viram UM trade — a segunda perna
    # sobrava e virava "sem_correspondencia_diario" PARA SEMPRE, enchendo a
    # lista PENDENTE do Histórico de operações que já estavam registradas.
    # Confirmado: os tickets 307609756/307636545/308768994 apareciam como
    # pendentes mesmo com os trades #147/#149/#173 já gravados. Foi isso que
    # levou 2 dessas "pendências" a serem finalizadas de novo, criando trades
    # duplicados em 03/07.
    #
    # `tickets_finalizados.parquet` guarda o vínculo exato ticket→trade, então
    # aqui ele resolve o que a heurística não tem como resolver: marca as N
    # posições que compõem um trade como já reconciliadas, de uma vez.
    mapa_lapide = _mapa_tickets_finalizados()
    if mapa_lapide and not posicoes.empty:
        ja_ligadas = posicoes["position_id"].isin(mapa_lapide.keys())
        posicoes.loc[ja_ligadas, "usado"] = True
        for _, pos in posicoes[ja_ligadas].iterrows():
            id_trade_ligado = mapa_lapide[int(pos["position_id"])]
            linhas.append({
                "id_trade": id_trade_ligado, "mt5_position_id": pos["position_id"],
                "mt5_ticket_entrada": pos["tickets_entrada"][0] if pos["tickets_entrada"] else None,
                "mt5_ticket_saida": pos["tickets_saida"][-1] if pos["tickets_saida"] else None,
                "mt5_divergencia_reais": None,
                "confianca": "exato",  # veio da lápide, não de heurística
                "mt5_match_status": "ok",
            })

    # Trades já reconciliados no passo 0 não entram na heurística — senão
    # consumiriam uma posição de OUTRO trade e recriariam o mesmo furo.
    trades_ja_ligados = set(mapa_lapide.values())

    for _, trade in trades.iterrows():
        if trade["id_trade"] in trades_ja_ligados:
            continue
        candidatas = posicoes[
            (posicoes["raiz_contrato"] == trade["ativo"])
            & (posicoes["data"] == trade["data_op"])
            & (~posicoes["usado"])
        ]
        if candidatas.empty:
            linhas.append({
                "id_trade": trade["id_trade"], "mt5_position_id": None,
                "mt5_ticket_entrada": None, "mt5_ticket_saida": None,
                "mt5_divergencia_reais": None, "confianca": None,
                "mt5_match_status": "sem_correspondencia_mt5",
            })
            continue

        # Sem horário exato pra desempatar entre candidatas do mesmo dia —
        # escolhe a de menor divergência absoluta contra o saldo logado
        # (melhor heurística disponível com os dados de hoje).
        saldo_logado = float(trade["saldo_operacao_reais"]) if pd.notna(trade["saldo_operacao_reais"]) else 0.0
        candidatas = candidatas.copy()
        candidatas["divergencia"] = (candidatas["profit_total"] - saldo_logado).abs()
        escolhida = candidatas.sort_values("divergencia").iloc[0]
        posicoes.loc[posicoes["position_id"] == escolhida["position_id"], "usado"] = True

        divergencia = float(escolhida["profit_total"]) - saldo_logado
        linhas.append({
            "id_trade": trade["id_trade"], "mt5_position_id": escolhida["position_id"],
            "mt5_ticket_entrada": escolhida["tickets_entrada"][0] if escolhida["tickets_entrada"] else None,
            "mt5_ticket_saida": escolhida["tickets_saida"][-1] if escolhida["tickets_saida"] else None,
            "mt5_divergencia_reais": divergencia,
            "confianca": "aproximado",
            "mt5_match_status": "ok" if abs(divergencia) < 1.0 else "divergente",
        })

    # Posições reais da conta sem nenhum trade logado correspondente —
    # execuções que aconteceram mas não foram registradas no diário. Ganha
    # ativo/data/saldo_estimado (2026-07-21) pra virar uma linha PENDENTE
    # de verdade no Histórico (ver listar_pendencias_diario), não só um
    # número agregado no badge da aba Estatísticas.
    for _, pos in posicoes[~posicoes["usado"]].iterrows():
        linhas.append({
            "id_trade": None, "mt5_position_id": pos["position_id"],
            "mt5_ticket_entrada": pos["tickets_entrada"][0] if pos["tickets_entrada"] else None,
            "mt5_ticket_saida": pos["tickets_saida"][-1] if pos["tickets_saida"] else None,
            "mt5_divergencia_reais": None, "confianca": None,
            "mt5_match_status": "sem_correspondencia_diario",
            "ativo": pos.get("raiz_contrato"), "data": pos.get("data"),
            "saldo_estimado": float(pos["profit_total"]) if pd.notna(pos.get("profit_total")) else None,
            "contrato": pos.get("contrato"),
            "tipo_operacao": "long" if pos.get("tipo") == "compra" else ("short" if pos.get("tipo") == "venda" else None),
            "volume": float(pos["volume"]) if pd.notna(pos.get("volume")) else None,
            "preco_entrada": float(pos["preco_entrada"]) if pd.notna(pos.get("preco_entrada")) else None,
            "preco_saida": float(pos["preco_saida"]) if pd.notna(pos.get("preco_saida")) else None,
            "datetime_entrada": pos.get("datetime_entrada"), "datetime_saida": pos.get("datetime_saida"),
        })

    resultado = pd.DataFrame(linhas, columns=COLUNAS_RECONCILIACAO)
    persistencia.salvar_reconciliacao(resultado)
    return resultado


def resumo_reconciliacao() -> dict:
    """Resumo de 1 linha da última reconciliação rodada em background (ver
    `_reconciliar_background` nos dois dashboards) — usado pelo badge
    discreto da aba Estatísticas, não mais uma tabela navegável própria
    (feedback diário.txt, 2026-07-11). `None` se nenhuma reconciliação
    rodou ainda (processo recém-iniciado, primeiro ciclo do Interval de
    5min ainda não disparou)."""
    df = persistencia.carregar_reconciliacao()
    if df is None:
        return None
    contagens = df["mt5_match_status"].value_counts().to_dict()
    return {
        "total": len(df),
        "ok": int(contagens.get("ok", 0)),
        "divergente": int(contagens.get("divergente", 0)),
        "sem_correspondencia_mt5": int(contagens.get("sem_correspondencia_mt5", 0)),
        "sem_correspondencia_diario": int(contagens.get("sem_correspondencia_diario", 0)),
    }


def listar_pendencias_diario() -> pd.DataFrame:
    """
    Execuções REAIS na conta MT5 que não têm nenhum trade correspondente
    logado no Diário (`mt5_match_status == 'sem_correspondencia_diario'`,
    calculado por `reconciliar_com_mt5`, cache lido daqui — não dispara
    reconciliação nova). 2026-07-21: pedido direto do Jesué depois de notar
    que operações reais do dia não apareciam em lugar nenhum do Diário —
    iam parar só num contador agregado no badge da aba Estatísticas
    ("N sem registro no diário"), fácil de não notar. Agora o Histórico
    (`_construir_diario_historico`/`_98`) funde essas linhas junto das
    reais, com uma badge "PENDENTE" e um botão "Editar".

    2026-07-21 (revisão): o botão "Editar" antes só trocava pra aba Novo
    Trade sem carregar nada (Jesué reportou: "vai para a tela de novo trade
    sem carregar as informações já capturadas do trade") — agora abre um
    pop-up reaproveitando o MESMO formulário da aba Posição (`_bloco_
    posicao_live`/`_bloco_posicao_form`), via `materializar_pendencia_como_
    rascunho` — por isso esta função agora devolve os campos completos
    (contrato/tipo_operacao/volume/preços/datas) capturados pelo MT5, não
    só o resumo pro badge.

    Retorna vazio se nenhuma reconciliação rodou ainda (mesmo caso de
    `resumo_reconciliacao`) ou se não há pendência nenhuma.
    """
    colunas = ["mt5_position_id", "ativo", "data", "saldo_estimado",
               "contrato", "tipo_operacao", "volume",
               "preco_entrada", "preco_saida", "datetime_entrada", "datetime_saida"]
    df = persistencia.carregar_reconciliacao()
    if df is None or df.empty:
        return pd.DataFrame(columns=colunas)
    pendentes = df[df["mt5_match_status"] == "sem_correspondencia_diario"]
    return pendentes[colunas].reset_index(drop=True)


def listar_pendencias_historico() -> pd.DataFrame:
    """
    União de TODAS as posições/execuções reais que ainda precisam virar um
    trade classificado no Diário — 2026-07-21, pedido direto do Jesué
    ("Posição fechada deve ir para o histórico automaticamente, com edição
    pendente"). Antes, um fechamento capturado AO VIVO pela sincronização
    (`sincronizar_posicoes_abertas`) só aparecia na aba Posição ("Pronta
    pra Finalizar"); esta função traz ELE TAMBÉM pro Histórico, no mesmo
    formato de pendência editável já usado pelas descobertas históricas da
    reconciliação de 30 dias (ver `listar_pendencias_diario`).

    Combina 2 fontes, sem duplicar:
    1. `posicoes_abertas.parquet` com `status='fechada_pendente'` — pares
       de hedge já mesclados (`detectar_pares_hedge`/`montar_posicao_
       sintetica_par`) + avulsas fechadas sem par. Já são rascunhos reais,
       "Editar" abre o pop-up direto, sem materializar nada.
    2. `listar_pendencias_diario()` (reconciliação) — só as que NÃO
       aparecem em (1) (a sincronização ao vivo ainda não as viu — coisas
       de antes do processo estar rodando, ou fora da janela de 3s do
       sync). "Editar" precisa materializar primeiro (ver
       `materializar_pendencia_como_rascunho`).

    `id_editar` é o valor usado como `index` do botão "Editar" — composto
    `"ticket_a-ticket_b"` pra par de hedge (mesma convenção de
    `finalizar_par_hedge`/`_parse_index_par`), ticket avulso (int) nos
    outros 2 casos.
    """
    colunas = ["id_editar", "ativo", "data", "saldo_estimado"]
    linhas = []
    # BUG REAL 2026-07-27 (reincidência dos duplicados #154-158, agora #169-182):
    # a lápide `tickets_finalizados.parquet` — que existe exatamente pra impedir
    # finalizar o MESMO ticket duas vezes — era lida SÓ por
    # `sincronizar_posicoes_abertas`, nunca aqui. Como `finalizar_posicao`
    # REMOVE a linha de `posicoes_abertas.parquet` como último passo, e este
    # `ids_ja_cobertos` era montado 100% a partir desse arquivo, finalizar
    # DESPROTEGIA o ticket: a linha do cache de reconciliação (atualizado só a
    # cada 5min, pelo processo PAI) voltava a aparecer como PENDENTE, e cada
    # clique novo em "✓"/"Finalizar" gravava outro trade idêntico. O ticket
    # 308536027 foi finalizado 14x assim. Começar o conjunto pela lápide
    # resolve na raiz — e também some com pendências que já viraram trade há
    # dias mas seguiam listadas (307609756 → #147, 307636545 → #149, ambos de
    # 2026-07-16, ainda na tela em 2026-07-27).
    # Além da lápide (ticket que já virou trade), o conjunto começa também com
    # os tickets que o usuário marcou explicitamente como "não entra no
    # Diário" (`ignorar_pendencia`, 2026-07-27) — senão uma operação real que
    # ele conscientemente recusou voltaria como PENDENTE a cada ciclo.
    ids_ja_cobertos = set(_tickets_ja_finalizados()) | set(_tickets_ignorados())

    posicoes = listar_posicoes_abertas()
    if posicoes is not None and not posicoes.empty:
        # Qualquer ticket que a sincronização ao vivo já rastreia — mesmo
        # ainda `aberta` (não fechada), sem par — nunca deve vir TAMBÉM da
        # reconciliação histórica com um valor calculado por uma fórmula
        # independente (`_parear_hedge_deals`/`agrupar_posicoes`, sobre
        # deal history bruto, ciclo próprio de 5min). Reforço de robustez
        # 2026-07-21 (varredura de bugs): antes só os tickets já pareados/
        # fechados entravam aqui — um ticket simplesmente `aberta` e sem
        # par ainda não era protegido, deixando margem pra um bug futuro
        # em `agrupar_posicoes` classificar errado um ticket genuinamente
        # ainda aberto como "pendente de classificação".
        ids_ja_cobertos |= set(posicoes["id_posicao"])
        fechadas = posicoes[posicoes["status"] == STATUS_POSICAO_FECHADA_PENDENTE]
        pares = detectar_pares_hedge(posicoes)
        tickets_pareados = {p["ticket_a"] for p in pares} | {p["ticket_b"] for p in pares}
        # A data da pendência vem da ENTRADA, não da saída.
        #
        # BUG REAL 2026-07-28: usava `datetime_saida`, mas quando o fechamento
        # real não é achado no MT5 o sync grava uma saída ESTIMADA com
        # `datetime_saida = agora` (ver `sincronizar_posicoes_abertas`). Ou
        # seja: uma operação de 27/07 aparecia na lista como 28/07, e ia
        # "andando" de dia em dia enquanto não fosse registrada — justamente
        # quando o usuário está conferindo o Diário contra o relatório da
        # corretora POR DATA. Pior, era inconsistente com o que acontece ao
        # finalizar: `finalizar_posicao`/`finalizar_par_hedge` gravam
        # `data_op` a partir da ENTRADA. Agora a lista mostra a mesma data que
        # o trade vai receber.
        for par in pares:
            data_entrada = par.get("datetime_entrada")
            linhas.append({
                "id_editar": f"{par['ticket_a']}-{par['ticket_b']}",
                "ativo": par["ativo"],
                "data": pd.Timestamp(data_entrada).date() if data_entrada is not None else None,
                "saldo_estimado": par["pnl_reais"],
            })
            ids_ja_cobertos.add(par["ticket_a"])
            ids_ja_cobertos.add(par["ticket_b"])
        avulsas_fechadas = fechadas[~fechadas["id_posicao"].isin(tickets_pareados)]
        for _, p in avulsas_fechadas.iterrows():
            data_entrada = p.get("datetime_entrada")
            linhas.append({
                "id_editar": p["id_posicao"], "ativo": p["ativo"],
                "data": pd.Timestamp(data_entrada).date() if pd.notna(data_entrada) else None,
                "saldo_estimado": p.get("pnl_reais_final"),
            })
            ids_ja_cobertos.add(p["id_posicao"])

    # `ids_ja_cobertos` mistura int puro (lápide), numpy.int64
    # (`posicoes_abertas.parquet`) e a coluna `mt5_position_id` chega como
    # float64 do parquet de reconciliação — normalizar os dois lados pra int
    # de propósito, em vez de confiar na igualdade de hash entre tipos
    # numéricos diferentes (funciona no CPython, mas é acidental demais pra
    # sustentar uma trava contra gravação duplicada de dado real).
    ids_ja_cobertos = {int(t) for t in ids_ja_cobertos if pd.notna(t)}
    pend_reconciliacao = listar_pendencias_diario()
    for _, p in pend_reconciliacao.iterrows():
        if pd.isna(p["mt5_position_id"]):
            continue
        mt5_position_id = int(p["mt5_position_id"])
        if mt5_position_id in ids_ja_cobertos:
            continue  # sync ao vivo OU lápide já cobre esse ticket, não duplica
        linhas.append({
            "id_editar": mt5_position_id, "ativo": p["ativo"],
            "data": p["data"], "saldo_estimado": p["saldo_estimado"],
        })

    resultado = pd.DataFrame(linhas, columns=colunas)
    if not resultado.empty:
        resultado = resultado.sort_values("data", ascending=False, na_position="last").reset_index(drop=True)
    return resultado


def materializar_pendencia_como_rascunho(id_editar) -> str | int | None:
    """
    Resolve um `id_editar` (ver `listar_pendencias_historico`) pro pop-up
    de edição de pendência do Histórico — idempotente pros 3 casos
    possíveis:

    1. Composto `"ticket_a-ticket_b"` (par de hedge já capturado AO VIVO
       pela sincronização) — os 2 tickets já existem de verdade em
       `posicoes_abertas.parquet`, nada pra materializar; só confirma que
       o par ainda existe (`detectar_pares_hedge`) e devolve o id como
       veio.
    2. Ticket avulso já em `posicoes_abertas.parquet` (sync ao vivo já
       capturou, ou clique anterior já materializou) — devolve direto,
       sem duplicar.
    3. Ticket avulso só descoberto pela reconciliação histórica de 30 dias
       (execução MT5 real sem trade logado, ver `listar_pendencias_
       diario`) — materializa como um rascunho novo em `posicoes_abertas.
       parquet` (`status='fechada_pendente'`), pra reaproveitar 100% do
       formulário de classificação já construído pra aba Posição
       (`_bloco_posicao_live`/`_bloco_posicao_form`/`finalizar_posicao`)
       no pop-up, em vez de duplicar campos/callbacks inteiros só pra
       esse caso. `saida_estimada=False` sempre aqui — ao contrário do
       card "AGUARDANDO CONFIRMAÇÃO" da sincronização ao vivo (que pode
       ter um preço de saída ainda não confirmado pela corretora), o
       preço/data de saída já vêm de um deal HISTÓRICO real do MT5
       (`mt5_loader.agrupar_posicoes`/`_parear_hedge_deals`), nunca uma
       estimativa.

    Retorna `None` se não conseguir resolver de nenhuma forma (ticket já
    finalizado antes — ver a trava da lápide logo abaixo, par não existe
    mais, ticket não existe em lugar nenhum, ou a trava do rascunho está
    ocupada) — nesses casos o chamador simplesmente não abre o pop-up
    (sem side-effect nenhum).
    """
    # BUG REAL 2026-07-27: sem esta trava, um clique numa linha PENDENTE que
    # JÁ virou trade (tela stale, ou o cache de reconciliação de 5min ainda
    # não tinha atualizado) recriava o rascunho do zero a partir do cache e
    # deixava `finalizar_posicao` gravar o MESMO trade de novo — foi assim que
    # o ticket 308536027 virou 14 trades. A checagem de idempotência que já
    # existia aqui (linha do `df_existente` abaixo) só olhava
    # `posicoes_abertas.parquet`, de onde `finalizar_posicao` justamente ACABA
    # de remover a linha. A lápide é a única fonte que sobrevive ao finalizar.
    # Ignorado explicitamente pelo usuário conta igual a já finalizado aqui:
    # nos dois casos aquele ticket não pode virar rascunho de novo.
    ja_finalizados = _tickets_ja_finalizados() | _tickets_ignorados()

    texto = str(id_editar)
    partes = texto.split("-") if "-" in texto else []
    if len(partes) == 2:
        try:
            ticket_a, ticket_b = int(partes[0]), int(partes[1])
        except ValueError:
            return None
        # Par: basta UMA perna já finalizada pra recusar — o trade líquido
        # das duas já foi gravado (`finalizar_par_hedge` marca as 2 juntas).
        if ticket_a in ja_finalizados or ticket_b in ja_finalizados:
            return None
        posicoes = listar_posicoes_abertas()
        if posicoes is None or posicoes.empty:
            return None
        pares = detectar_pares_hedge(posicoes)
        existe = any(p["ticket_a"] == ticket_a and p["ticket_b"] == ticket_b for p in pares)
        return id_editar if existe else None

    try:
        mt5_position_id = int(id_editar)
    except (TypeError, ValueError):
        return None

    if mt5_position_id in ja_finalizados:
        return None

    df_existente = persistencia.carregar_posicoes_abertas()
    if df_existente is not None and not df_existente.empty and mt5_position_id in set(df_existente["id_posicao"]):
        return mt5_position_id  # já é um rascunho real (sync ao vivo ou clique anterior) — reaproveita

    df_pend = listar_pendencias_diario()
    linha = df_pend[df_pend["mt5_position_id"] == mt5_position_id]
    if linha.empty:
        return None
    p = linha.iloc[0]

    with persistencia.lock_posicoes_abertas(
            tentativas=cfg.DIARIO_LOCK_USUARIO_TENTATIVAS,
            espera_segundos=cfg.DIARIO_LOCK_USUARIO_ESPERA_S) as adquirida:
        if not adquirida:
            return None
        df = persistencia.carregar_posicoes_abertas()
        if df is None:
            df = pd.DataFrame(columns=COLUNAS_POSICOES_ABERTAS)
        if not df.empty and mt5_position_id in set(df["id_posicao"]):
            return mt5_position_id  # corrida com outro clique — já materializado nesse meio tempo

        tipo_operacao = p.get("tipo_operacao")
        preco_entrada = p.get("preco_entrada")
        preco_saida = p.get("preco_saida")
        pnl_pontos_final = None
        if tipo_operacao and pd.notna(preco_entrada) and pd.notna(preco_saida):
            pnl_pontos_final = _pnl_pontos(tipo_operacao, preco_entrada, preco_saida)

        agora = datetime.now()
        nova = {coluna: None for coluna in COLUNAS_POSICOES_ABERTAS}
        nova.update({
            "id_posicao": mt5_position_id, "ativo": p["ativo"], "contrato": p.get("contrato"),
            "tipo_operacao": tipo_operacao, "volume": p.get("volume"),
            "preco_entrada": preco_entrada, "datetime_entrada": p.get("datetime_entrada"),
            "preco_atual": preco_saida,
            "status": STATUS_POSICAO_FECHADA_PENDENTE,
            "preco_saida": preco_saida, "datetime_saida": p.get("datetime_saida"),
            "pnl_pontos_final": pnl_pontos_final,
            "pnl_reais_final": float(p["saldo_estimado"]) if pd.notna(p.get("saldo_estimado")) else None,
            "saida_estimada": False,
            "criado_em": agora, "atualizado_em": agora,
        })
        df = pd.concat([df, pd.DataFrame([nova], columns=COLUNAS_POSICOES_ABERTAS)], ignore_index=True)
        persistencia.salvar_posicoes_abertas(df)
    return mt5_position_id


_CAMPOS_CLASSIFICACAO_LISTA = (
    "notas_cronologicas", "imagens_em_andamento",
    "estrategia_tags_preliminar", "tags_emocionais_preliminar",
)
_CAMPOS_CLASSIFICACAO_ESCALAR = (
    "sala_analise_preliminar", "alvo_pontos_preliminar", "stop_pontos_preliminar",
    "nivel_sr_referencia_preliminar", "resumo_operacional_preliminar",
    "emo_confianca_preliminar", "emo_humor_preliminar", "emo_energia_preliminar",
    "emo_atencao_preliminar", "emo_presenca_preliminar", "sentimento_mercado_preliminar",
)


def descartar_rascunho_pendencia(id_posicao: int) -> bool:
    """
    Remove um rascunho `fechada_pendente` de `posicoes_abertas.parquet` sem
    gravar nada no Diário — usado quando o usuário abre "Editar" numa
    pendência do Histórico (ver `materializar_pendencia_como_rascunho`) e
    confirma que aquele registro NÃO é uma operação real. Bug real
    2026-07-21: sem isso, um rascunho aberto no pop-up e nunca finalizado
    ficava órfão pra sempre em "AGUARDANDO CONFIRMAÇÃO" — indistinguível de
    uma posição real (achado na varredura: ticket 306272514, 176925→176775,
    congelado desde 2026-07-21 16:44).

    Guarda-corpos deliberados (nunca some com dado do usuário em silêncio):
    - Só remove linhas `status == STATUS_POSICAO_FECHADA_PENDENTE` — uma
      posição `aberta` de verdade NUNCA é removida por esta função.
    - Só remove se o rascunho ainda não tem NENHUMA classificação
      preenchida (notas, tags, sala/análise, resumo, emocional etc.) — se o
      usuário já começou a documentar a operação, esta função recusa e o
      chamador deve orientar a passar por `finalizar_posicao`/
      `finalizar_par_hedge` em vez de descartar.

    Retorna True se removeu, False se não achou o ticket, se não está
    elegível (aberta, ou já tem classificação), ou se a trava está ocupada.
    """
    with persistencia.lock_posicoes_abertas(
            tentativas=cfg.DIARIO_LOCK_USUARIO_TENTATIVAS,
            espera_segundos=cfg.DIARIO_LOCK_USUARIO_ESPERA_S) as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_posicoes_abertas()
        if df is None or df.empty or id_posicao not in set(df["id_posicao"]):
            return False
        linha = df[df["id_posicao"] == id_posicao].iloc[0]
        if linha["status"] != STATUS_POSICAO_FECHADA_PENDENTE:
            return False
        if any(len(_para_lista(linha.get(campo))) > 0 for campo in _CAMPOS_CLASSIFICACAO_LISTA):
            return False
        for campo in _CAMPOS_CLASSIFICACAO_ESCALAR:
            valor = linha.get(campo)
            if valor is not None and pd.notna(valor) and valor != '':
                return False
        df_restante = df[df["id_posicao"] != id_posicao]
        persistencia.salvar_posicoes_abertas(df_restante)
    return True


# Thresholds de classificação de `avaliar_aderencia_tracados` — movidos
# pra config_backtest.py (2026-07-21, Fase 3.3) junto das outras
# constantes candidatas a ajuste fino automático do Modo Aprendizagem —
# ver cfg.ADERENCIA_SR_ATR_PRECISO/_PROXIMO e cfg.AJUSTE_FINO_ALLOWLIST.
ADERENCIA_SR_ATR_PRECISO = cfg.ADERENCIA_SR_ATR_PRECISO
ADERENCIA_SR_ATR_PROXIMO = cfg.ADERENCIA_SR_ATR_PROXIMO


def avaliar_aderencia_tracados(dias_atras: int | None = None) -> pd.DataFrame:
    """
    Compara, para cada trade com `nivel_sr_referencia` preenchido, o nível
    marcado à mão por Jesué contra os TRAÇADOS que estavam ativos no
    instante do save (`snapshot_mercado_json['tracados']`, capturado por
    `metricas.montar_snapshot_diario` em `registrar_trade`). Responde "o
    motor leu direito a força do traçado perto desse nível?" — relatório
    de REVISÃO MANUAL, nunca ajuste automático de peso/lógica (volume de
    dados baixo demais pra isso ser seguro, mesma decisão já tomada pra
    reconciliação MT5).

    Substitui `avaliar_aderencia_sr` (2026-07-21, Fase 3.2 do Modo
    Aprendizagem) — o motor automático de S/R foi desativado
    (`cfg.AUTO_DETECCAO_SR_ATIVA=False`), então a pergunta antiga ("o motor
    via esse nível SOZINHO?", contra `niveis_sr_acima/abaixo`) deixou de
    fazer sentido; a nova compara contra os traçados que o próprio Jesué
    desenhou.

    Só considera trades com `nivel_sr_referencia` E `snapshot_mercado_json`
    preenchidos E o snapshot contendo pelo menos 1 traçado — trades
    migrados/antigos (sem a chave `tracados`, ou salvos num instante sem
    nenhum traçado ativo) ficam de fora automaticamente, mesmo raciocínio
    de sempre pra dado que não dá pra comparar. `dias_atras`, se
    informado, restringe por `data_op` (mesmo parâmetro de
    `reconciliar_com_mt5`).
    """
    df = listar_trades()
    if dias_atras is not None:
        limite = (datetime.now() - timedelta(days=dias_atras)).date()
        df = df[df["data_op"].apply(lambda d: d is not None and pd.Timestamp(d).date() >= limite)]

    elegiveis = df[df["nivel_sr_referencia"].notna() & df["snapshot_mercado_json"].notna()]

    linhas = []
    for _, trade in elegiveis.iterrows():
        nivel_ref = float(trade["nivel_sr_referencia"])
        try:
            snapshot = json.loads(trade["snapshot_mercado_json"])
        except (TypeError, ValueError):
            continue

        preco_snapshot = snapshot.get("preco")
        atr = snapshot.get("atr")
        tracados_snap = snapshot.get("tracados")
        if preco_snapshot is None or not tracados_snap:
            continue

        lado = "abaixo" if nivel_ref < preco_snapshot else "acima"
        tipo_esperado = "suporte" if lado == "abaixo" else "resistencia"
        candidatos = [t for t in tracados_snap if t.get("tipo") == tipo_esperado and t.get("preco") is not None]

        casado = None
        diferenca_atr = None
        if candidatos:
            casado = min(candidatos, key=lambda t: abs(t["preco"] - nivel_ref))
            diferenca_pts = abs(casado["preco"] - nivel_ref)
            diferenca_atr = (diferenca_pts / atr) if atr else None

        if diferenca_atr is None:
            classificacao = "nao_visto"
        elif diferenca_atr <= ADERENCIA_SR_ATR_PRECISO:
            classificacao = "visto_preciso"
        elif diferenca_atr <= ADERENCIA_SR_ATR_PROXIMO:
            classificacao = "visto_proximo"
        else:
            classificacao = "nao_visto"

        linhas.append({
            "id_trade": trade["id_trade"], "data_op": trade["data_op"], "ativo": trade["ativo"],
            "nivel_sr_referencia": nivel_ref, "nivel_sr_tipo_informado": trade.get("nivel_sr_tipo"),
            "lado_geometrico": lado,
            "tracado_preco": casado.get("preco") if casado else None,
            "tracado_id": casado.get("id_tracado") if casado else None,
            "diferenca_atr": diferenca_atr,
            "forte": casado.get("forte") if casado else None,
            "toques": casado.get("toques") if casado else None,
            "prob_bounce": casado.get("prob_bounce") if casado else None,
            "postura": casado.get("postura") if casado else None,
            "magnitude_esperada": casado.get("magnitude_esperada") if casado else None,
            "combustivel_score": casado.get("combustivel_score") if casado else None,
            "aproximacao_score": casado.get("aproximacao_score") if casado else None,
            "rejeicao_score": casado.get("rejeicao_score") if casado else None,
            "classificacao": classificacao,
        })

    resultado = pd.DataFrame(linhas, columns=COLUNAS_ADERENCIA_TRACADOS)
    persistencia.salvar_aderencia_tracados(resultado)
    return resultado


# Idem — recomputável via `detectar_padroes_comportamentais`, não é
# histórico do trade.
COLUNAS_PADROES_COMPORTAMENTAIS = [
    "tipo", "id_trade", "id_trade_anterior", "data_op", "ativo", "precisao", "detalhe",
]

# Janelas de `detectar_padroes_comportamentais` — primeiro palpite, não
# calibrado, mesmo espírito de `ADERENCIA_SR_ATR_*`/`SCORE_*`: ajustar
# depois que Jesué conferir os casos reais.
REVENGE_JANELA_MINUTOS = 30
TILT_MIN_TRADES = 3
TILT_JANELA_MINUTOS = 60


def _num_ou_zero(v) -> float:
    return 0.0 if v is None or (isinstance(v, float) and pd.isna(v)) else float(v)


def _avaliar_janela_temporal(anterior: dict, atual: dict, janela_minutos: int) -> tuple[str, str] | None:
    """
    Decide se `atual` está "logo depois" de `anterior` (mesmo ativo, já em
    ordem cronológica) — usa `datetime_entrada` real se ambos tiverem
    (precisão 'minuto', dentro de `janela_minutos`), senão cai pro proxy de
    dia (precisão 'dia', só `data_op` igual — a granularidade disponível
    hoje, já que nenhum trade real tem horário preciso ainda). Retorna
    `(precisao, descrição)` ou `None` se não estiver dentro da janela.
    """
    dt_ant, dt_atual = anterior.get("datetime_entrada"), atual.get("datetime_entrada")
    if pd.notna(dt_ant) and pd.notna(dt_atual):
        delta_min = (pd.Timestamp(dt_atual) - pd.Timestamp(dt_ant)).total_seconds() / 60
        if 0 <= delta_min <= janela_minutos:
            return "minuto", f"{delta_min:.0f} min depois"
        return None
    if anterior.get("data_op") == atual.get("data_op"):
        return "dia", "no mesmo dia"
    return None


def detectar_padroes_comportamentais(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Detecta revenge trading e tilt cascade nos trades reais, por ativo, em
    ordem cronológica (`data_op` + `id_trade` como desempate — assume que a
    ordem de `id_trade` reflete a ordem real dentro do dia, o que vale para
    a migração do xlsm por ter processado as linhas em ordem; não é
    garantia absoluta, só a melhor aproximação disponível sem horário real).

    Relatório de REVISÃO MANUAL, mesmo espírito de `avaliar_aderencia_tracados`/
    `reconciliar_com_mt5` — Jesué julga, nada se ajusta sozinho.

    Cada comparação decide sozinha a precisão via `_avaliar_janela_
    temporal`: usa minutos reais quando ambos os trades comparados têm
    `datetime_entrada` (hoje, nenhum tem — só a aba "Posição", sincronizada
    com o MT5, vai gerar isso daqui pra frente), senão cai pro proxy de dia
    (a granularidade real disponível agora). Não são dois sistemas
    separados — o mesmo código já funciona nos dois casos.

    FOMO entries NÃO é detectado aqui (ver plano da Fase 4): exige cruzar
    horário exato de entrada com movimento de preço, sem proxy de dia que
    faça sentido, e hoje não há nenhum trade com horário real pra validar.
    """
    if df is None:
        df = listar_trades()
    reais = df[~df["is_simulated"].astype(bool)].copy()

    linhas = []
    for ativo, grupo in reais.groupby("ativo", sort=False):
        trades = grupo.sort_values(["data_op", "id_trade"]).to_dict("records")

        # --- Revenge trading: trade M vs. o trade IMEDIATAMENTE anterior N
        # do mesmo ativo — reage à perda mais recente, não a uma perda
        # antiga qualquer.
        for i in range(1, len(trades)):
            n_trade, m_trade = trades[i - 1], trades[i]
            saldo_n = n_trade["saldo_operacao_reais"]
            if pd.isna(saldo_n) or saldo_n >= 0:
                continue
            lote_n, lote_m = _num_ou_zero(n_trade.get("lote")), _num_ou_zero(m_trade.get("lote"))
            if not (lote_m > lote_n):
                continue
            janela = _avaliar_janela_temporal(n_trade, m_trade, REVENGE_JANELA_MINUTOS)
            if janela is None:
                continue
            precisao, quando = janela
            linhas.append({
                "tipo": "revenge_trading", "id_trade": m_trade["id_trade"],
                "id_trade_anterior": n_trade["id_trade"], "data_op": m_trade["data_op"],
                "ativo": ativo, "precisao": precisao,
                "detalhe": (f"lote subiu de {lote_n:g} para {lote_m:g} {quando} após perda "
                            f"de R$ {saldo_n:+.2f} (trade #{n_trade['id_trade']})"),
            })

        # --- Tilt cascade: sequências de TILT_MIN_TRADES+ trades
        # consecutivos, começando numa perda, com lote NUNCA decrescendo E
        # crescendo de verdade em algum ponto (`houve_escalada`) — só
        # "não-decrescente" não basta: com lote=1 constante em TODO o
        # histórico real (nenhum trade jamais variou tamanho), essa
        # condição sozinha marcava qualquer dia de 3+ trades começando em
        # perda como "tilt", sem nenhuma escalada de verdade. "Tamanho
        # crescente" é a assinatura que o benchmark descreve — exigir isso
        # de verdade é o que distingue tilt de um dia comum com várias
        # entradas.
        i = 0
        while i < len(trades):
            if pd.isna(trades[i]["saldo_operacao_reais"]) or trades[i]["saldo_operacao_reais"] >= 0:
                i += 1
                continue
            sequencia = [trades[i]]
            precisoes_seq = []
            houve_escalada = False
            j = i + 1
            while j < len(trades):
                anterior, atual = sequencia[-1], trades[j]
                lote_ant = _num_ou_zero(anterior.get("lote"))
                lote_atual = _num_ou_zero(atual.get("lote"))
                if lote_atual < lote_ant:
                    break
                if lote_atual > lote_ant:
                    houve_escalada = True
                janela = _avaliar_janela_temporal(anterior, atual, TILT_JANELA_MINUTOS)
                if janela is None:
                    break
                precisoes_seq.append(janela[0])
                sequencia.append(atual)
                j += 1
            if len(sequencia) >= TILT_MIN_TRADES and houve_escalada:
                precisao = "minuto" if all(p == "minuto" for p in precisoes_seq) else "dia"
                ids_str = ", ".join(f"#{t['id_trade']}" for t in sequencia)
                lotes_str = "→".join(f"{_num_ou_zero(t.get('lote')):g}" for t in sequencia)
                linhas.append({
                    "tipo": "tilt_cascade", "id_trade": sequencia[-1]["id_trade"],
                    "id_trade_anterior": sequencia[0]["id_trade"], "data_op": sequencia[-1]["data_op"],
                    "ativo": ativo, "precisao": precisao,
                    "detalhe": (f"{len(sequencia)} trades em sequência ({ids_str}), lote {lotes_str}, "
                                f"iniciada em perda de R$ {sequencia[0]['saldo_operacao_reais']:+.2f}"),
                })
                i = j
            else:
                i += 1

    resultado = pd.DataFrame(linhas, columns=COLUNAS_PADROES_COMPORTAMENTAIS)
    persistencia.salvar_padroes_comportamentais(resultado)
    return resultado


def resumo_recente_para_ia(ativo: str, n: int = 5) -> str:
    """
    Bloco terso com as últimas `n` operações REAIS (não simuladas) do
    ativo, no mesmo estilo "linha objetiva" já usado por
    `ai_analyst.montar_contexto` — dá ao Mentor IA consciência de como
    Jesué tem operado ultimamente. Sem nenhuma lógica de correlação com o
    motor de S/R (isso é Fase 2/F do plano, deliberadamente fora de
    escopo aqui). Retorna string vazia se não houver trades ainda (diário
    recém-criado, ou nenhuma operação real deste ativo) — o chamador
    (`ai_analyst._montar_prompt`) simplesmente omite o bloco nesse caso.
    """
    df = listar_trades(ativo=ativo, is_simulated=False)
    if df.empty:
        return ""

    recentes = df.sort_values("data_op", ascending=False).head(n)
    linhas = []
    for _, trade in recentes.iterrows():
        tags = trade["estrategia_tags"]
        tags_str = ", ".join(tags) if hasattr(tags, "__iter__") and len(tags) else "—"
        saldo = trade["saldo_operacao_reais"]
        saldo_str = f"R$ {float(saldo):+.2f}" if pd.notna(saldo) else "—"
        linhas.append(
            f"- {trade['data_op']} | {trade.get('tipo_operacao') or '—'} | "
            f"{tags_str} | saída: {trade.get('tipo_saida') or '—'} | {saldo_str}"
        )
    return "ÚLTIMAS OPERAÇÕES REGISTRADAS NO DIÁRIO:\n" + "\n".join(linhas)


# =============================================================================
# POSIÇÕES ABERTAS — rascunho ao vivo do Diário (ver plano em
# C:\Users\jesue\.claude\plans\analise-o-bloco-de-glimmering-marble.md)
# =============================================================================
# Tabela SEPARADA de `diario_trades.parquet` (`posicoes_abertas.parquet`,
# ver persistencia.py) — uma linha por posição MT5 atualmente aberta ou
# recém-fechada aguardando confirmação. Ao contrário do diário principal,
# ESTA tabela é deliberadamente mutável: o dashboard principal escreve os
# campos vindos do MT5 a cada ciclo de sincronização, e o próprio usuário
# (via UI do Diário) vai escrevendo notas/tags/imagens enquanto acompanha
# a operação — inclusive depois que ela fecha, até decidir "Finalizar".
# Nada aqui grava em `diario_trades.parquet` diretamente; só
# `finalizar_posicao` faz a ponte, chamando `registrar_trade()` já
# existente, sem alterá-lo.

STATUS_POSICAO_ABERTA = "aberta"
STATUS_POSICAO_FECHADA_PENDENTE = "fechada_pendente"

COLUNAS_POSICOES_ABERTAS = [
    "id_posicao", "ativo", "contrato", "tipo_operacao", "volume",
    "preco_entrada", "datetime_entrada",
    "preco_atual", "pnl_pontos", "pnl_reais", "sl", "tp",
    "status",
    "preco_saida", "datetime_saida", "pnl_pontos_final", "pnl_reais_final",
    "saida_estimada",
    "notas_cronologicas", "imagens_em_andamento",
    "estrategia_tags_preliminar", "sala_analise_preliminar",
    "alvo_pontos_preliminar", "stop_pontos_preliminar",
    "nivel_sr_referencia_preliminar", "resumo_operacional_preliminar",
    "emo_confianca_preliminar", "emo_humor_preliminar", "emo_energia_preliminar",
    "emo_atencao_preliminar", "emo_presenca_preliminar",
    "tags_emocionais_preliminar", "sentimento_mercado_preliminar",
    "criado_em", "atualizado_em",
]


def _pnl_pontos(tipo_operacao: str, preco_entrada: float, preco_ref: float) -> float:
    """Pontos ganhos/perdidos com o sinal certo conforme a direção — long:
    preço subiu = positivo; short: preço caiu = positivo."""
    bruto = preco_ref - preco_entrada
    return bruto if tipo_operacao == "long" else -bruto


# Tolerância pra considerar que o preço de saída "bateu" no stop/alvo/entrada
# — expressa como fração da distância entrada→alvo (ou entrada→stop, se não
# houver alvo), não um valor fixo em pontos, porque WIN e WDO têm escalas de
# preço bem diferentes. Primeiro palpite, não calibrado contra dado real
# ainda (poucas posições fechadas via MT5 até agora) — fácil de ajustar aqui.
TOLERANCIA_SAIDA_FRACAO = 0.1


def derivar_tipo_saida(tipo_operacao: str, preco_entrada, preco_saida, sl, tp) -> str:
    """Deriva `tipo_saida` comparando o preço de saída real com stop/alvo/
    entrada do MT5, em vez de depender de escolha manual do usuário — pedido
    do Jesué ("isso será feito na plataforma de operação"). Ordem de
    checagem: bateu o alvo -> 'gain'; bateu o stop -> 'stop'; saiu perto da
    entrada -> 'breakeven'; nenhum dos três -> 'manual' (fechou por conta
    própria, sem bater nível nenhum)."""
    if preco_saida is None or pd.isna(preco_saida) or preco_entrada is None or pd.isna(preco_entrada):
        return "manual"

    tem_tp = tp is not None and not pd.isna(tp)
    tem_sl = sl is not None and not pd.isna(sl)
    dist_referencia = abs(tp - preco_entrada) if tem_tp else (abs(sl - preco_entrada) if tem_sl else 0.0)
    tolerancia = dist_referencia * TOLERANCIA_SAIDA_FRACAO

    if tem_tp and abs(preco_saida - tp) <= tolerancia:
        return "gain"
    if tem_sl and abs(preco_saida - sl) <= tolerancia:
        return "stop"
    if tolerancia and abs(preco_saida - preco_entrada) <= tolerancia:
        return "breakeven"
    return "manual"


def _salvar_imagem_rascunho(conteudo_bytes: bytes, id_posicao: int, sequencia: int) -> str:
    """
    Salva um print da galeria de uma posição AINDA em rascunho — ao
    contrário de `salvar_imagem` (que assume 1 trade = 1 imagem, nome fixo
    por `id_trade`), aqui pode haver VÁRIOS prints pra mesma posição ao
    longo da operação, então `sequencia` entra no nome pra nunca um
    sobrescrever o outro. Mesmo redimensionamento de `salvar_imagem`,
    função separada de propósito (regra de nomeação diferente, e evita
    qualquer risco de colisão com mudanças em `salvar_imagem` feitas em
    paralelo por outra sessão).
    """
    cfg.PASTA_DIARIO_IMAGENS.mkdir(parents=True, exist_ok=True)
    imagem = Image.open(io.BytesIO(conteudo_bytes)).convert("RGB")
    if imagem.width > cfg.DIARIO_IMAGEM_LARGURA_MAX:
        nova_altura = int(imagem.height * cfg.DIARIO_IMAGEM_LARGURA_MAX / imagem.width)
        imagem = imagem.resize((cfg.DIARIO_IMAGEM_LARGURA_MAX, nova_altura), Image.LANCZOS)
    nome_arquivo = f"rascunho_{id_posicao}_{sequencia:02d}.png"
    imagem.save(cfg.PASTA_DIARIO_IMAGENS / nome_arquivo, format="PNG")
    return nome_arquivo


def _para_lista(valor) -> list:
    """`notas_cronologicas`/`imagens_em_andamento` voltam do parquet como
    numpy array (mesma pegadinha já documentada em `listar_trades` sobre
    `estrategia_tags`), ou `None` se a coluna nunca foi tocada — normaliza
    pra list Python de verdade antes de fazer `.append`."""
    if valor is None or isinstance(valor, str):
        return []
    return list(valor) if hasattr(valor, "__iter__") else []


COLUNAS_TICKETS_FINALIZADOS = ["id_posicao", "id_trade", "finalizado_em"]


def _marcar_tickets_finalizados(tickets: list[int], id_trade: int) -> None:
    """Registra que estes tickets MT5 já viraram um trade definitivo
    (`id_trade`) — chamado por `finalizar_posicao`/`finalizar_par_hedge`
    logo após `registrar_trade` ter sucesso. Usado por
    `sincronizar_posicoes_abertas` pra NUNCA MAIS ressuscitar esses tickets
    em `posicoes_abertas.parquet`, mesmo que o MT5 continue mostrando eles
    como abertos (achado real 2026-07-16: o BTG demora 1-2h pra cruzar as
    2 pernas de um par hedge via `DEAL_ENTRY_OUT_BY` — até lá, o ticket
    segue "aberto" pro MT5 mesmo já finalizado no Diário; sem essa marca,
    o próximo ciclo de sync redescobria o ticket como posição "nova" e
    reabria a pendência, permitindo finalizar o MESMO trade em duplicidade
    se clicado de novo — bug real, gerou os trades duplicados #154-158)."""
    df = persistencia.carregar_tickets_finalizados()
    if df is None:
        df = pd.DataFrame(columns=COLUNAS_TICKETS_FINALIZADOS)
    agora = datetime.now()
    novas = pd.DataFrame(
        [{"id_posicao": t, "id_trade": id_trade, "finalizado_em": agora} for t in tickets],
        columns=COLUNAS_TICKETS_FINALIZADOS,
    )
    df = pd.concat([df, novas], ignore_index=True)
    persistencia.salvar_tickets_finalizados(df)


def _tickets_ja_finalizados() -> set[int]:
    df = persistencia.carregar_tickets_finalizados()
    if df is None or df.empty:
        return set()
    return set(int(t) for t in df["id_posicao"])


COLUNAS_TICKETS_IGNORADOS = ["id_posicao", "motivo", "ignorado_em"]


def ignorar_pendencia(id_editar, motivo: str = "") -> bool:
    """Marca uma operação REAL do MT5 como "não entra no Diário", em
    definitivo — some da lista PENDENTE e nunca mais volta.

    Necessário desde 2026-07-27: a reconciliação reporta como pendente toda
    operação real sem trade correspondente, então uma que o usuário
    conscientemente NÃO quer registrar reaparecia a cada ciclo, pra sempre. A
    única forma de calar era registrá-la — ou seja, aceitar justamente o que
    se queria recusar.

    Caso real que motivou: o relatório de performance da corretora é filtrado
    por CARTEIRA, então operações da conta que estão fora da carteira
    acompanhada não constam nele. O Jesué decidiu que o relatório é o guia até
    27/07 — essas operações existem no MT5, mas não fazem parte do Diário.

    `id_editar` aceita as duas formas da lista de pendências: ticket avulso ou
    par de hedge `"a-b"` (aí os 2 tickets são marcados). Reversível apagando a
    linha de `tickets_ignorados.parquet`.
    """
    texto = str(id_editar)
    partes = texto.split("-") if "-" in texto else []
    try:
        tickets = ([int(partes[0]), int(partes[1])] if len(partes) == 2
                   else [int(id_editar)])
    except (TypeError, ValueError):
        return False

    df = persistencia.carregar_tickets_ignorados()
    if df is None:
        df = pd.DataFrame(columns=COLUNAS_TICKETS_IGNORADOS)
    ja = set(df["id_posicao"]) if not df.empty else set()
    novas = [{"id_posicao": t, "motivo": motivo or None, "ignorado_em": datetime.now()}
             for t in tickets if t not in ja]
    if not novas:
        return True  # já estava ignorado — idempotente
    df = pd.concat([df, pd.DataFrame(novas, columns=COLUNAS_TICKETS_IGNORADOS)],
                   ignore_index=True)
    persistencia.salvar_tickets_ignorados(df)
    return True


def _tickets_ignorados() -> set[int]:
    df = persistencia.carregar_tickets_ignorados()
    if df is None or df.empty:
        return set()
    return set(int(t) for t in df["id_posicao"])


def _mapa_tickets_finalizados() -> dict[int, int]:
    """`{id_posicao_mt5: id_trade}` — o vínculo IDENTITÁRIO entre um ticket do
    MT5 e o trade que ele virou, gravado por `_marcar_tickets_finalizados`.

    Usado por `reconciliar_com_mt5`: é infinitamente mais confiável que o
    casamento heurístico por `(ativo, data)` de lá, e é a única forma de saber
    que as DUAS pernas de um par de hedge pertencem ao MESMO trade."""
    df = persistencia.carregar_tickets_finalizados()
    if df is None or df.empty:
        return {}
    return {int(t): int(i) for t, i in zip(df["id_posicao"], df["id_trade"])}


def sincronizar_posicoes_abertas(df_posicoes_mt5: pd.DataFrame | None) -> pd.DataFrame | None:
    """
    Diff entre as posições REALMENTE abertas agora no MT5
    (`mt5_loader.buscar_posicoes_abertas()`) e a tabela de rascunhos salva:
    ticket novo -> cria linha (`status='aberta'`); ticket que já era
    rastreado e continua aberto -> atualiza só os campos vindos do MT5
    (preço atual, pnl, sl/tp), nunca mexe nas notas/tags/imagens do
    usuário; ticket que sumiu -> resolve o fechamento real via
    `mt5_loader.buscar_execucoes_periodo`/`agrupar_posicoes` (últimas 24h)
    e marca `status='fechada_pendente'`.

    Não bloqueante (`persistencia.lock_posicoes_abertas()`): se outro
    processo está escrevendo, pula este ciclo em silêncio — isso roda num
    timer automático, não por ação do usuário, mesma filosofia do lock de
    atualização de candle. Retorna a tabela atualizada, ou None se pulou o
    ciclo por trava ocupada.
    """
    with persistencia.lock_posicoes_abertas() as adquirida:
        if not adquirida:
            return None

        df_atual = persistencia.carregar_posicoes_abertas()
        if df_atual is None:
            df_atual = pd.DataFrame(columns=COLUNAS_POSICOES_ABERTAS)

        linhas_por_id = {
            int(linha["id_posicao"]): linha.to_dict() for _, linha in df_atual.iterrows()
        }
        tickets_mt5 = set(int(t) for t in df_posicoes_mt5["ticket"]) if df_posicoes_mt5 is not None and not df_posicoes_mt5.empty else set()
        tickets_ja_finalizados = _tickets_ja_finalizados()

        # Purga qualquer ticket já finalizado que ainda esteja na tabela de
        # rascunhos por engano (ex: um ciclo de sync de um processo ainda
        # rodando com código antigo, entre finalizar e reiniciar — bug real
        # 2026-07-16: o guard abaixo, sozinho, só impedia CRIAR uma linha
        # nova pra um ticket tombstoned, mas não limpava uma que já tivesse
        # voltado por outro caminho). O tombstone é sempre a fonte de
        # verdade final — nunca deixa um ticket já finalizado "grudado" na
        # tabela de rascunhos pra sempre, autocura no ciclo seguinte.
        for ticket_zumbi in (set(linhas_por_id.keys()) & tickets_ja_finalizados):
            del linhas_por_id[ticket_zumbi]
        tickets_rastreados = set(linhas_por_id.keys())

        agora = datetime.now()

        if df_posicoes_mt5 is not None:
            for _, pos in df_posicoes_mt5.iterrows():
                ticket = int(pos["ticket"])
                pnl_pts = _pnl_pontos(pos["tipo_operacao"], pos["preco_entrada"], pos["preco_atual"])
                valor_ponto = cfg.VALOR_PONTO.get(pos["ativo"], 0.0)
                pnl_reais = float(pos["profit"]) if pd.notna(pos["profit"]) else pnl_pts * valor_ponto * pos["volume"]

                if ticket in linhas_por_id:
                    linha = linhas_por_id[ticket]
                    # Auto-recuperação (2026-07-15): o MT5 é a fonte de
                    # verdade sobre o que está aberto AGORA — se este ticket
                    # aparece aqui mas a última sincronização o classificou
                    # como fechado, isso prova que aquela classificação
                    # estava errada (falha transitória de 1 ciclo do MT5,
                    # já documentada neste projeto como recorrente — ver
                    # buscar_posicoes_abertas). Reabre e limpa os campos de
                    # fechamento, nunca deixa um "fechamento fantasma"
                    # travado pra sempre esperando correção manual.
                    #
                    # BUG REAL (2026-07-17): esta condição não distinguia um
                    # fechamento genuinamente ESTIMADO/não confirmado
                    # (`saida_estimada=True` — aí sim pode ser um "fechamento
                    # fantasma" de 1 ciclo) de um fechamento REAL já
                    # confirmado via `buscar_execucoes_periodo`
                    # (`saida_estimada=False`, `preco_saida`/`datetime_saida`
                    # vieram de negócios de verdade). Como este ciclo roda a
                    # cada 3s (`interval-posicao-sync`) e o MT5 pode levar
                    # 1-2h pra parar de listar o ticket como aberto (mesmo
                    # atraso do BTG já documentado acima), QUALQUER posição
                    # `fechada_pendente` confirmada era revertida pra
                    # `aberta` (apagando `preco_saida`/`datetime_saida`
                    # reais) enquanto o usuário ainda estava preenchendo/
                    # revisando o formulário de classificação — clicar em
                    # "Finalizar e gravar no Diário" então lia dados já
                    # apagados e nunca gravava o trade (achado real: Jesué
                    # preencheu a correção de saída, clicou em finalizar, e
                    # nada foi salvo em `diario_trades.parquet`). Só
                    # auto-recuperar o que nunca foi confirmado.
                    if linha["status"] != STATUS_POSICAO_ABERTA and linha.get("saida_estimada"):
                        linha.update({
                            "status": STATUS_POSICAO_ABERTA,
                            "preco_saida": None, "datetime_saida": None,
                            "pnl_pontos_final": None, "pnl_reais_final": None,
                            "saida_estimada": False,
                        })
                    linha.update({
                        "preco_atual": pos["preco_atual"], "pnl_pontos": pnl_pts,
                        "pnl_reais": pnl_reais, "sl": pos["sl"], "tp": pos["tp"],
                        "atualizado_em": agora,
                    })
                elif ticket in tickets_ja_finalizados:
                    # Já virou trade definitivo antes (ver
                    # _marcar_tickets_finalizados) — o MT5 ainda mostra
                    # aberto (BTG demora 1-2h pra cruzar as pernas), mas
                    # NUNCA mais rastreia como posição "nova"/pendente de
                    # novo. Sem isso, o mesmo trade podia ser finalizado
                    # em duplicidade (bug real 2026-07-16, gerou #154-158).
                    continue
                else:
                    novo = {coluna: None for coluna in COLUNAS_POSICOES_ABERTAS}
                    novo.update({
                        "id_posicao": ticket, "ativo": pos["ativo"], "contrato": pos["contrato"],
                        "tipo_operacao": pos["tipo_operacao"], "volume": pos["volume"],
                        "preco_entrada": pos["preco_entrada"], "datetime_entrada": pos["datetime_entrada"],
                        "preco_atual": pos["preco_atual"], "pnl_pontos": pnl_pts, "pnl_reais": pnl_reais,
                        "sl": pos["sl"], "tp": pos["tp"],
                        "status": STATUS_POSICAO_ABERTA, "saida_estimada": False,
                        "notas_cronologicas": [], "imagens_em_andamento": [],
                        "criado_em": agora, "atualizado_em": agora,
                    })
                    linhas_por_id[ticket] = novo

        # BUG REAL (2026-07-17): quando `buscar_posicoes_abertas()` falha de
        # verdade (retorna `None` — não "zero posições", mas "não sabemos"),
        # `tickets_mt5` virava um `set()` vazio (linha acima) e TODO ticket
        # rastreado como `aberta` era lido como "sumiu do MT5 agora" — uma
        # falha transitória de conexão virava um fechamento fantasma em
        # massa (achado real: 6 tickets WDO marcados `fechada_pendente`/
        # `saida_estimada=True` no mesmo microssegundo, todos genuinamente
        # ainda abertos no MT5). Detecção de fechamento só pode rodar
        # quando `df_posicoes_mt5` é um resultado de verdade (mesmo que
        # vazio) — `None` significa "não sabemos", não "zero posições", e
        # deve pular este passo em silêncio, igual ao resto da função já
        # faz pra falha do MT5.
        tickets_fechados_agora = [
            tid for tid in tickets_rastreados
            if tid not in tickets_mt5 and (
                linhas_por_id[tid]["status"] == STATUS_POSICAO_ABERTA
                or (linhas_por_id[tid]["status"] == STATUS_POSICAO_FECHADA_PENDENTE
                    and linhas_por_id[tid]["saida_estimada"])
            )
        ] if df_posicoes_mt5 is not None else []
        if tickets_fechados_agora:
            # A janela precisa cobrir desde a perna IN mais antiga entre os
            # tickets a resolver — não um fixo "últimas 24h" — porque um
            # ticket pode ficar `saida_estimada=True` por mais de 1 dia
            # esperando o BTG rodar o cruzamento OUT_BY (achado real
            # 2026-07-16: uma janela fixa de 24h perdia a perna IN de um
            # ticket aberto há mais de 1 dia, e o retry nunca encontrava o
            # fechamento real por faltar a perna de entrada no resultado).
            entrada_mais_antiga = min(
                pd.Timestamp(linhas_por_id[tid]["datetime_entrada"]) for tid in tickets_fechados_agora
            )
            inicio = min(entrada_mais_antiga, agora - timedelta(hours=24)) - timedelta(minutes=5)
            try:
                df_deals = mt5_loader.buscar_execucoes_periodo(inicio, agora)
            except Exception:
                # Mesmo espírito do resto da função pra falha de MT5: uma
                # falha SÓ nesta etapa (detecção de fechamento) não pode
                # abortar a função inteira e impedir que posições novas/
                # ainda abertas (já lidas com sucesso acima) sejam
                # persistidas — bug real 2026-07-21, ver
                # dashboard.py::_sincronizar_posicoes_background.
                _log_sync.exception('Falha em buscar_execucoes_periodo (detecção de fechamento)')
                df_deals = None
            posicoes_fechadas = (
                mt5_loader.agrupar_posicoes(df_deals) if df_deals is not None
                else pd.DataFrame(columns=["position_id"])
            )
            for tid in tickets_fechados_agora:
                linha = linhas_por_id[tid]
                status_original = linha["status"]
                fechamento = posicoes_fechadas[posicoes_fechadas["position_id"] == tid] if not posicoes_fechadas.empty else posicoes_fechadas
                # `agrupar_posicoes` pode achar a perna IN do position_id mas
                # nenhuma perna OUT ainda (preco_saida=None) — isso NÃO é um
                # fechamento real, é o mesmo "não achei nada" do caso vazio
                # (achado real 2026-07-15, ao lado do bug de fuso horário).
                if not fechamento.empty and pd.notna(fechamento.iloc[0]["preco_saida"]):
                    f = fechamento.iloc[0]
                    pnl_pts_final = _pnl_pontos(linha["tipo_operacao"], linha["preco_entrada"], f["preco_saida"])
                    linha.update({
                        "preco_saida": f["preco_saida"], "datetime_saida": f["datetime_saida"],
                        "pnl_pontos_final": pnl_pts_final, "pnl_reais_final": float(f["profit_total"]),
                        "saida_estimada": False,
                    })
                elif status_original == STATUS_POSICAO_ABERTA:
                    # Não achou o fechamento real (raro — falha da API do MT5
                    # nesse ciclo, ou o negócio de fechamento nunca aparece no
                    # histórico da corretora pra esse ticket, caso confirmado
                    # em 2026-07-12 com 8 posições reais). Usa o último preço
                    # "ao vivo" como estimativa (melhor que nada pra exibição),
                    # mas `saida_estimada=True` marca explicitamente que este
                    # valor NÃO é confirmado — a UI deve avisar e travar a
                    # finalização até o usuário confirmar/corrigir manualmente
                    # (`corrigir_saida_rascunho`), nunca gravar isso no Diário
                    # como se fosse dado real.
                    linha.update({
                        "preco_saida": linha["preco_atual"], "datetime_saida": agora,
                        "pnl_pontos_final": linha["pnl_pontos"], "pnl_reais_final": linha["pnl_reais"],
                        "saida_estimada": True,
                    })
                # else: já estava fechada_pendente/estimada e ainda não achou
                # o fechamento real desta vez (achado 2026-07-16: o BTG pode
                # levar 1-2h pra rodar o cruzamento OUT_BY) — deixa como está
                # e tenta de novo no próximo ciclo, sem "chacoalhar" os campos
                # estimados à toa a cada 3s.
                linha["status"] = STATUS_POSICAO_FECHADA_PENDENTE
                linha["atualizado_em"] = agora

        df_final = pd.DataFrame(list(linhas_por_id.values()), columns=COLUNAS_POSICOES_ABERTAS)
        persistencia.salvar_posicoes_abertas(df_final)
        return df_final


def detectar_pares_hedge(df_posicoes: pd.DataFrame) -> list[dict]:
    """
    Agrupa posições opostas (long+short) do MESMO contrato exato (ex:
    WDOQ26, não só WDO — evita casar meses de vencimento diferentes) e
    MESMO volume como 1 trade líquido — achado real 2026-07-15: a conta é
    hedging (não netting), então uma ordem OCO (SL/TP) que dispara vira uma
    posição NOVA e independente no MT5 em vez de fechar a original (2
    posições opostas coexistindo, nunca 1 só). Pura, sem I/O — mesmo padrão
    de `avaliar_aderencia_tracados`/`detectar_padroes_comportamentais` (computada
    fresca a cada render, nunca persistida à parte).

    Considera elegíveis posições com status `aberta` OU
    `fechada_pendente` — um par pode existir mesmo que a perna de ENTRADA
    ainda não tenha sido corrigida pelo ciclo de sync seguinte (ver
    auto-recuperação em `sincronizar_posicoes_abertas`).

    A perna aberta PRIMEIRO (menor `datetime_entrada`) é a "entrada" do
    trade líquido (direção dela vira a direção do trade); a perna mais
    NOVA é a "saída" (economicamente equivale a fechar a primeira no preço
    da segunda). Volume diferente = sem par nesta v1 (hedge parcial fica
    documentado como fora de escopo, não silenciosamente errado).

    Achado real 2026-07-22 (dia em que todas as posições fecharam): quando
    a perna de SAÍDA (a que nasceu do SL/TP de A) TAMBÉM fecha por conta
    própria, ela passa a ter um resultado econômico PRÓPRIO (da entrada
    dela até o fechamento real/estimado dela) — diferente de simplesmente
    usar o preço de ENTRADA dela como "saída confirmada" de A (o que é só
    uma aproximação válida enquanto ela ainda está aberta). As duas coisas
    continuam representando 1 ÚNICA operação do ponto de vista do usuário
    (pedido dele: "não quero sujeira do MT5 no diário, quero 1 linha por
    operação"), então o par continua sendo formado nos dois casos — só o
    CÁLCULO muda:
    - Se a perna de saída ainda está `aberta`: usa o preço de ENTRADA dela
      como fechamento de A (matemática exata pra um hedge travado — o P&L
      combinado das 2 pernas é invariante ao preço de mercado atual).
    - Se a perna de saída TAMBÉM já fechou (`fechada_pendente`, e a perna
      de entrada também): soma o resultado JÁ REAL/estimado de cada perna
      (`pnl_reais_final` de cada uma) — nunca descarta o que a perna de
      saída rendeu depois de "nascer". `saida_estimada` do par vira True
      se qualquer uma das pernas ainda depende de estimativa (preço real
      de fechamento ainda não confirmado pelo cruzamento do MT5).

    Considera elegíveis posições com status `aberta` OU
    `fechada_pendente` — um par pode existir mesmo que a perna de ENTRADA
    ainda não tenha sido corrigida pelo ciclo de sync seguinte (ver
    auto-recuperação em `sincronizar_posicoes_abertas`).

    A perna aberta PRIMEIRO (menor `datetime_entrada`) é a "entrada" do
    trade líquido (direção dela vira a direção do trade); a perna mais
    NOVA é a "saída" (economicamente equivale a fechar a primeira no preço
    da segunda). Volume diferente = sem par nesta v1 (hedge parcial fica
    documentado como fora de escopo, não silenciosamente errado).

    As 2 pernas também precisam ter aberto no MESMO DIA de pregão (bug real
    2026-07-27, ver comentário no loop de candidatos) — um par de hedge nasce
    sempre no mesmo pregão, e sem essa trava o pareamento fabricava trades
    misturando entrada de um dia com saída de outro.

    Retorna uma lista de dicts, um por par encontrado — cada posição só
    entra em NO MÁXIMO 1 par (a primeira correspondência de mesmo volume
    disponível).
    """
    if df_posicoes is None or df_posicoes.empty:
        return []

    elegiveis = df_posicoes[df_posicoes["status"].isin(
        [STATUS_POSICAO_ABERTA, STATUS_POSICAO_FECHADA_PENDENTE]
    )]
    if elegiveis.empty:
        return []

    pares = []
    usados = set()
    for contrato, grupo in elegiveis.groupby("contrato"):
        longs = grupo[grupo["tipo_operacao"] == "long"]
        shorts = grupo[grupo["tipo_operacao"] == "short"]
        for _, long_pos in longs.iterrows():
            if long_pos["id_posicao"] in usados:
                continue
            candidatos = shorts[
                (shorts["volume"] == long_pos["volume"]) & (~shorts["id_posicao"].isin(usados))
            ]
            if candidatos.empty:
                continue

            # Tenta cada candidato até achar um em que a perna de ENTRADA
            # esteja pronta (aberta, ou já fechada — ver docstring): se a
            # de SAÍDA ainda está aberta, forma o par direto; se ela
            # também já fechou, só forma o par se a de ENTRADA também já
            # tiver um resultado próprio resolvido (senão a entrada ainda
            # está genuinamente em aberto, não dá pra combinar um número
            # final ainda).
            short_pos = None
            perna_entrada = perna_saida = None
            for _, candidato in candidatos.iterrows():
                if long_pos["datetime_entrada"] <= candidato["datetime_entrada"]:
                    entrada_cand, saida_cand = long_pos, candidato
                else:
                    entrada_cand, saida_cand = candidato, long_pos
                # BUG REAL 2026-07-27 (trade #167, R$ -225 fabricado): sem
                # nenhuma restrição de tempo, este pareamento casou um WDO
                # short aberto em 22/07 11:55 com uma posição de 23/07 —
                # entrada de um dia, saída de outro, gerando um prejuízo que
                # nunca existiu. Um par de hedge SEMPRE nasce no mesmo pregão
                # (é a ordem OCO/SL-TP da própria posição disparando, minutos
                # ou horas depois), então exigir o mesmo dia elimina a classe
                # inteira de par falso sem perder nenhum par legítimo. Sem
                # par, cada perna segue pelo caminho avulso normal — que
                # mostra o número real do MT5, nunca um valor inventado.
                mesmo_dia = (
                    pd.Timestamp(entrada_cand["datetime_entrada"]).date()
                    == pd.Timestamp(saida_cand["datetime_entrada"]).date()
                )
                if not mesmo_dia:
                    continue
                saida_aberta = saida_cand["status"] == STATUS_POSICAO_ABERTA
                ambas_resolvidas = (
                    saida_cand["status"] == STATUS_POSICAO_FECHADA_PENDENTE
                    and entrada_cand["status"] == STATUS_POSICAO_FECHADA_PENDENTE
                )
                if saida_aberta or ambas_resolvidas:
                    short_pos = candidato
                    perna_entrada, perna_saida = entrada_cand, saida_cand
                    break
            if short_pos is None:
                continue

            usados.add(long_pos["id_posicao"])
            usados.add(short_pos["id_posicao"])

            valor_ponto = cfg.VALOR_PONTO.get(perna_entrada["ativo"], 0.0)
            if perna_saida["status"] == STATUS_POSICAO_ABERTA:
                # Perna de saída ainda aberta: preço de ENTRADA dela é um
                # fato confirmado, serve de "fechamento" de A.
                pnl_pontos = _pnl_pontos(perna_entrada["tipo_operacao"],
                                          perna_entrada["preco_entrada"], perna_saida["preco_entrada"])
                pnl_reais = pnl_pontos * valor_ponto * perna_entrada["volume"]
                preco_saida, datetime_saida = perna_saida["preco_entrada"], perna_saida["datetime_entrada"]
                saida_estimada = False
            else:
                # As duas já fecharam por conta própria: soma o resultado
                # JÁ REAL/estimado de cada uma (nunca descarta o que a
                # perna de saída rendeu depois de "nascer" do hedge).
                pnl_reais_entrada = perna_entrada.get("pnl_reais_final")
                if pnl_reais_entrada is None or pd.isna(pnl_reais_entrada):
                    pnl_reais_entrada = perna_entrada.get("pnl_reais") or 0.0
                pnl_reais_saida = perna_saida.get("pnl_reais_final")
                if pnl_reais_saida is None or pd.isna(pnl_reais_saida):
                    pnl_reais_saida = perna_saida.get("pnl_reais") or 0.0
                pnl_reais = float(pnl_reais_entrada) + float(pnl_reais_saida)
                pnl_pontos = pnl_reais / (valor_ponto * perna_entrada["volume"]) if valor_ponto else None
                preco_saida, datetime_saida = perna_saida.get("preco_saida"), perna_saida.get("datetime_saida")
                saida_estimada = bool(perna_entrada.get("saida_estimada")) or bool(perna_saida.get("saida_estimada"))

            pares.append({
                "ticket_a": int(perna_entrada["id_posicao"]), "ticket_b": int(perna_saida["id_posicao"]),
                "contrato": contrato, "ativo": perna_entrada["ativo"], "volume": perna_entrada["volume"],
                "tipo_operacao": perna_entrada["tipo_operacao"],
                "preco_entrada": perna_entrada["preco_entrada"], "datetime_entrada": perna_entrada["datetime_entrada"],
                "preco_saida": preco_saida, "datetime_saida": datetime_saida,
                "pnl_pontos": pnl_pontos, "pnl_reais": pnl_reais, "saida_estimada": saida_estimada,
            })
    return pares


def calcular_exposicao_residual(df_posicoes: pd.DataFrame) -> dict[str, float]:
    """
    Soma líquida de volume por `contrato`, considerando só posições
    genuinamente abertas agora (`status == aberta` — não `fechada_pendente`,
    que já são posições que sumiram do MT5 e estão só aguardando o
    fechamento oficial). Long soma positivo, short soma negativo.

    Zero = hedge completo pra aquele contrato (par balanceado, sem risco
    real de mercado). Diferente de zero = sobra real de exposição — única
    situação que de fato merece alerta (Requisito 4 da especificação de
    pareamento hedge, 2026-07-16). Pura, sem I/O — mesmo padrão de
    `detectar_pares_hedge`, recomputada a cada render.
    """
    if df_posicoes is None or df_posicoes.empty:
        return {}
    abertas = df_posicoes[df_posicoes["status"] == STATUS_POSICAO_ABERTA]
    if abertas.empty:
        return {}
    sinal = abertas["tipo_operacao"].map({"long": 1, "short": -1}).fillna(0)
    liquido = (abertas["volume"] * sinal).groupby(abertas["contrato"]).sum()
    return {contrato: float(valor) for contrato, valor in liquido.items()}


def montar_posicao_sintetica_par(par: dict, df_posicoes: pd.DataFrame) -> dict:
    """
    Monta um dict no mesmo formato de uma linha de `posicoes_abertas.parquet`
    (mesmas chaves de `COLUNAS_POSICOES_ABERTAS`) a partir de um par de
    hedge (ver `detectar_pares_hedge`) — pra reaproveitar as MESMAS funções
    de render de card/formulário que já existem pra 1 ticket (UI), sem
    duplicar HTML/callback nenhum. `id_posicao` vira uma string composta
    `"{ticket_a}-{ticket_b}"` (`ticket_a` sempre a perna de ENTRADA, por
    construção de `detectar_pares_hedge`) — funciona como índice de
    `dash.MATCH` normalmente, Dash não exige que seja `int`.

    Herda notas/imagens/tags preliminares da perna de ENTRADA (é "o trade"
    do ponto de vista do usuário — mesma decisão de `finalizar_par_hedge`).
    `status` sempre `fechada_pendente` (o par É um fechamento real, pronto
    pra finalizar). `saida_estimada` vem do PRÓPRIO par (ver
    `detectar_pares_hedge` — bug real 2026-07-22: antes vinha sempre
    `False`, mesmo quando a perna de saída já tinha fechado sozinha usando
    uma estimativa, o que mostrava "✓ confirmado" pra um valor que ainda
    dependia de confirmação real do MT5) — `par_hedge=True` marca
    explicitamente a origem sintética, pra UI distinguir do card antigo de
    "saída não confirmada" (são situações diferentes: aqui a saída pode
    ser confirmada OU estimada, só que vem de uma posição oposta, não de
    uma perna OUT no mesmo ticket).
    """
    linha_entrada = df_posicoes[df_posicoes["id_posicao"] == par["ticket_a"]].iloc[0].to_dict()
    sintetica = dict(linha_entrada)
    sintetica.update({
        "id_posicao": f"{par['ticket_a']}-{par['ticket_b']}",
        "contrato": par["contrato"], "ativo": par["ativo"], "volume": par["volume"],
        "tipo_operacao": par["tipo_operacao"],
        "preco_entrada": par["preco_entrada"], "datetime_entrada": par["datetime_entrada"],
        "preco_atual": par["preco_saida"],
        "pnl_pontos": par["pnl_pontos"], "pnl_reais": par["pnl_reais"],
        "status": STATUS_POSICAO_FECHADA_PENDENTE,
        "preco_saida": par["preco_saida"], "datetime_saida": par["datetime_saida"],
        "pnl_pontos_final": par["pnl_pontos"], "pnl_reais_final": par["pnl_reais"],
        "saida_estimada": par.get("saida_estimada", False),
        "par_hedge": True,
    })
    return sintetica


def listar_posicoes_abertas() -> pd.DataFrame:
    """Carrega a tabela de posições abertas/rascunhos. DataFrame vazio
    (colunas garantidas) se nada foi sincronizado ainda."""
    df = persistencia.carregar_posicoes_abertas()
    if df is None:
        return pd.DataFrame(columns=COLUNAS_POSICOES_ABERTAS)
    return df


def atualizar_rascunho(
    id_posicao: int,
    nota_texto: str | None = None,
    campos: dict | None = None,
    imagem_bytes: bytes | None = None,
) -> bool:
    """
    Único ponto de edição "em andamento" — só mexe em
    `posicoes_abertas.parquet`, nunca em `diario_trades.parquet`. Funciona
    igual com a posição ainda `status='aberta'` ou já `'fechada_pendente'`
    (só `finalizar_posicao` encerra o rascunho de vez) — é assim que a
    análise que o Jesué faz DURANTE e DEPOIS do trade (antes de finalizar)
    fica coberta.

    - `nota_texto`: SEMPRE um `append` em `notas_cronologicas` (nunca
      sobrescreve as notas anteriores) — o "diário de bordo" da própria
      operação.
    - `campos`: atualiza in-place os campos preliminares (chaves aceitas:
      `estrategia_tags`, `sala_analise`, `alvo_pontos`, `stop_pontos`,
      `nivel_sr_referencia`, `resumo_operacional`, `emo_confianca`,
      `emo_humor`, `emo_energia`, `emo_atencao`, `emo_presenca`,
      `tags_emocionais`, `sentimento_mercado` — vira `<chave>_preliminar`
      automaticamente).
    - `imagem_bytes`: salva via `_salvar_imagem_rascunho` e ANEXA o nome do
      arquivo em `imagens_em_andamento` (galeria, não substitui).

    Retorna False se a trava estava ocupada (chamador — botão de salvar na
    UI do Diário — deve mostrar erro/retry visível) ou se `id_posicao` não
    existe na tabela.
    """
    with persistencia.lock_posicoes_abertas(
            tentativas=cfg.DIARIO_LOCK_USUARIO_TENTATIVAS,
            espera_segundos=cfg.DIARIO_LOCK_USUARIO_ESPERA_S) as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_posicoes_abertas()
        if df is None or df.empty or id_posicao not in set(df["id_posicao"]):
            return False

        idx = df.index[df["id_posicao"] == id_posicao][0]
        agora = datetime.now()

        if nota_texto:
            notas = _para_lista(df.at[idx, "notas_cronologicas"])
            notas.append({"hora": agora.isoformat(), "texto": nota_texto})
            df.at[idx, "notas_cronologicas"] = notas

        if campos:
            for chave, valor in campos.items():
                coluna = chave if chave.endswith("_preliminar") else f"{chave}_preliminar"
                if coluna in COLUNAS_POSICOES_ABERTAS:
                    df.at[idx, coluna] = valor

        if imagem_bytes is not None:
            imagens = _para_lista(df.at[idx, "imagens_em_andamento"])
            nome_arquivo = _salvar_imagem_rascunho(imagem_bytes, id_posicao, len(imagens) + 1)
            imagens.append(nome_arquivo)
            df.at[idx, "imagens_em_andamento"] = imagens

        df.at[idx, "atualizado_em"] = agora
        persistencia.salvar_posicoes_abertas(df)
        return True


def remover_imagem_rascunho(id_posicao: int, nome_arquivo: str) -> bool:
    """
    Tira UM print da galeria de um rascunho (`imagens_em_andamento`) —
    pedido do Jesué em 2026-07-27: "a thumb do print deve ter um X que
    aparece ao passar o mouse, caso eu queira remover um print do trade".
    Antes só dava pra anexar; um print colado por engano ficava lá até o
    trade ser finalizado.

    O arquivo em disco só é apagado se NENHUM outro registro citar aquele
    nome — nem outro rascunho (`imagens_em_andamento`), nem um trade já
    gravado (`imagem_arquivo`/`imagens_adicionais`). É barato conferir e
    evita o pior caso possível aqui: sumir com a capa de um trade antigo
    por causa de um clique numa tela de rascunho. Se estiver em uso, só a
    referência deste rascunho sai e o arquivo continua onde está.

    Retorna False se a trava estiver ocupada, se `id_posicao` não existir
    ou se aquele nome não estiver na galeria dele.
    """
    with persistencia.lock_posicoes_abertas(
            tentativas=cfg.DIARIO_LOCK_USUARIO_TENTATIVAS,
            espera_segundos=cfg.DIARIO_LOCK_USUARIO_ESPERA_S) as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_posicoes_abertas()
        if df is None or df.empty or id_posicao not in set(df["id_posicao"]):
            return False
        idx = df.index[df["id_posicao"] == id_posicao][0]
        imagens = _para_lista(df.at[idx, "imagens_em_andamento"])
        if nome_arquivo not in imagens:
            return False
        df.at[idx, "imagens_em_andamento"] = [i for i in imagens if i != nome_arquivo]
        df.at[idx, "atualizado_em"] = datetime.now()
        persistencia.salvar_posicoes_abertas(df)
        df_restante = df

    if not _imagem_em_uso(nome_arquivo, df_restante):
        caminho = cfg.PASTA_DIARIO_IMAGENS / nome_arquivo
        try:
            caminho.unlink()
        except (FileNotFoundError, OSError):
            pass  # arquivo já sumiu ou está travado — a referência já saiu, que é o que importa
    return True


def _imagem_em_uso(nome_arquivo: str, df_posicoes: pd.DataFrame | None) -> bool:
    """`nome_arquivo` ainda é citado por algum rascunho ou trade gravado?
    Usado por `remover_imagem_rascunho` antes de apagar o arquivo do disco."""
    if df_posicoes is not None and not df_posicoes.empty:
        for valor in df_posicoes["imagens_em_andamento"]:
            if nome_arquivo in _para_lista(valor):
                return True
    df_trades = persistencia.carregar_diario()
    if df_trades is None or df_trades.empty:
        return False
    if (df_trades["imagem_arquivo"] == nome_arquivo).any():
        return True
    for valor in df_trades["imagens_adicionais"]:
        if nome_arquivo in _para_lista(valor):
            return True
    return False


def corrigir_saida_rascunho(id_posicao: int, preco_saida: float, datetime_saida) -> bool:
    """
    Corrige `preco_saida`/`datetime_saida` de um rascunho cuja saída veio do
    fallback de `sincronizar_posicoes_abertas` (`saida_estimada=True` — o
    fechamento real não foi encontrado no MT5, então o valor exibido era só
    o último preço "ao vivo" antes do ticket sumir, não o preço real de
    saída). Recalcula `pnl_pontos_final`/`pnl_reais_final` a partir do preço
    corrigido e marca `saida_estimada=False` (correção manual confirmada
    pelo usuário) — nunca deixa `finalizar_posicao` gravar o valor fabricado
    no Diário oficial sem essa confirmação.

    Retorna False se a trava estiver ocupada ou `id_posicao` não existir.
    """
    with persistencia.lock_posicoes_abertas(
            tentativas=cfg.DIARIO_LOCK_USUARIO_TENTATIVAS,
            espera_segundos=cfg.DIARIO_LOCK_USUARIO_ESPERA_S) as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_posicoes_abertas()
        if df is None or df.empty or id_posicao not in set(df["id_posicao"]):
            return False

        idx = df.index[df["id_posicao"] == id_posicao][0]
        tipo_operacao = df.at[idx, "tipo_operacao"]
        preco_entrada = df.at[idx, "preco_entrada"]
        volume = df.at[idx, "volume"]
        ativo = df.at[idx, "ativo"]

        pnl_pts = _pnl_pontos(tipo_operacao, preco_entrada, preco_saida)
        valor_ponto = cfg.VALOR_PONTO.get(ativo, 0.0)

        df.at[idx, "preco_saida"] = preco_saida
        df.at[idx, "datetime_saida"] = datetime_saida
        df.at[idx, "pnl_pontos_final"] = pnl_pts
        df.at[idx, "pnl_reais_final"] = pnl_pts * valor_ponto * volume
        df.at[idx, "saida_estimada"] = False
        df.at[idx, "atualizado_em"] = datetime.now()
        persistencia.salvar_posicoes_abertas(df)
        return True


def finalizar_posicao(
    id_posicao: int,
    campos_classificacao: dict,
    imagem_principal: str | None = None,
) -> int | None:
    """
    Encerra um rascunho de posição e grava o trade definitivo em
    `diario_trades.parquet` via `registrar_trade()` (reaproveitado, SEM
    nenhuma alteração) — o rascunho nunca é escrito lá diretamente, só
    serve de origem dos dados pra montar `campos`.

    `campos_classificacao` é o mesmo dict que o formulário "Novo Trade" já
    monta hoje (`estrategia_tags`, `sala_analise`, `tipo_saida`,
    `tags_setup_erro`, etc.) — os valores `*_preliminar` do rascunho
    servem só de DEFAULT (pré-preenchimento do lado da UI); esta função
    recebe o que o usuário confirmou, não decide sozinha. Os campos vindos
    do MT5 (ativo/contrato/tipo_operacao/lote/preços/datas/resultado) são
    injetados aqui a partir do rascunho — o chamador não precisa repassá-los.

    `imagem_principal`, se informado, deve ser um dos nomes já presentes em
    `imagens_em_andamento` — vira a `imagem_arquivo` oficial do trade
    (coluna existente, inalterada). As demais imagens da galeria ficam em
    `imagens_adicionais` (coluna nova, aditiva) e o log cronológico de
    notas é concatenado em `obs`.

    Retorna o `id_trade` criado, ou None se a trava do rascunho estiver
    ocupada, se `id_posicao` não existir, se o ticket JÁ tiver sido
    finalizado antes (ver a trava de idempotência abaixo), ou se
    `registrar_trade` falhar (trava do diário ocupada) — nesses casos o
    rascunho é preservado intacto, nunca perdido.
    """
    with persistencia.lock_posicoes_abertas(
            tentativas=cfg.DIARIO_LOCK_USUARIO_TENTATIVAS,
            espera_segundos=cfg.DIARIO_LOCK_USUARIO_ESPERA_S) as adquirida:
        if not adquirida:
            return None
        # TRAVA DE IDEMPOTÊNCIA (bug real 2026-07-27) — dentro da trava, antes
        # de qualquer escrita. As guardas de UI (botão que some, lista que
        # filtra) são defesa secundária e sempre furam: clique duplo rápido,
        # aba stale, e os DOIS processos do Diário (8052/8053) rodando ao mesmo
        # tempo contra os mesmos parquets. Só uma checagem aqui, no caminho de
        # escrita, garante de fato "1 ticket = no máximo 1 trade". Sem ela o
        # ticket 308536027 virou 14 trades (#169-182) em 90 segundos.
        if int(id_posicao) in _tickets_ja_finalizados():
            return None
        df = persistencia.carregar_posicoes_abertas()
        if df is None or df.empty or id_posicao not in set(df["id_posicao"]):
            return None
        linha = df[df["id_posicao"] == id_posicao].iloc[0].to_dict()

        notas = _para_lista(linha.get("notas_cronologicas"))
        narrativa = "\n".join(f"[{n['hora']}] {n['texto']}" for n in notas) or None

        imagens_galeria = _para_lista(linha.get("imagens_em_andamento"))
        imagens_restantes = [img for img in imagens_galeria if img != imagem_principal]

        # Defaults vindos do rascunho (`*_preliminar`, preenchidos ao longo da
        # operação) — `campos_classificacao` sobrescreve por cima com o que o
        # usuário confirmou/ajustou nesse último passo, mas se ele não
        # mencionar de novo uma chave (ex: só ajustou `tipo_saida`), o valor
        # preliminar já escrito durante a operação não se perde.
        campos = {
            "estrategia_tags": _para_lista(linha.get("estrategia_tags_preliminar")),
            "sala_analise": linha.get("sala_analise_preliminar"),
            "alvo_pontos": linha.get("alvo_pontos_preliminar"),
            "stop_pontos": linha.get("stop_pontos_preliminar"),
            "nivel_sr_referencia": linha.get("nivel_sr_referencia_preliminar"),
            "resumo_operacional": linha.get("resumo_operacional_preliminar"),
            "emo_confianca": linha.get("emo_confianca_preliminar"),
            "emo_humor": linha.get("emo_humor_preliminar"),
            "emo_energia": linha.get("emo_energia_preliminar"),
            "emo_atencao": linha.get("emo_atencao_preliminar"),
            "emo_presenca": linha.get("emo_presenca_preliminar"),
            "tags_emocionais": _para_lista(linha.get("tags_emocionais_preliminar")),
            "sentimento_mercado": linha.get("sentimento_mercado_preliminar"),
            "ativo": linha["ativo"], "contrato": linha["contrato"],
            "tipo_operacao": linha["tipo_operacao"], "lote": linha["volume"],
            "preco_entrada": linha["preco_entrada"], "datetime_entrada": linha["datetime_entrada"],
            "preco_saida": linha["preco_saida"], "datetime_saida": linha["datetime_saida"],
            "resultado_pontos": linha["pnl_pontos_final"], "saldo_operacao_reais": linha["pnl_reais_final"],
            "data_op": pd.Timestamp(linha["datetime_entrada"]).date(),
            # Derivado do MT5 (sl/tp/preço de saída), não mais escolhido à
            # mão — ver `derivar_tipo_saida`. `campos_classificacao` ainda
            # pode sobrescrever numa exceção pontual, mas a UI não oferece
            # mais esse seletor (feedback diário.txt).
            "tipo_saida": derivar_tipo_saida(
                linha["tipo_operacao"], linha["preco_entrada"], linha["preco_saida"],
                linha.get("sl"), linha.get("tp"),
            ),
            "obs": narrativa,
            "imagens_adicionais": imagens_restantes,
        }
        campos.update(campos_classificacao)  # estrategia_tags/sala_analise/tags_setup_erro/etc. — decisão do usuário

        imagem_bytes = None
        if imagem_principal:
            caminho_img = cfg.PASTA_DIARIO_IMAGENS / imagem_principal
            if caminho_img.exists():
                imagem_bytes = caminho_img.read_bytes()

    # `registrar_trade` abre sua PRÓPRIA trava (`lock_diario`, arquivo
    # diferente de `lock_posicoes_abertas`) — chamado fora do `with` acima
    # de propósito, pra nunca segurar as duas travas ao mesmo tempo (risco
    # de deadlock cruzado se outro processo pedir na ordem inversa).
    id_trade = registrar_trade(campos, imagem_bytes=imagem_bytes)
    if id_trade is None:
        return None  # trava do diário ocupada — rascunho preservado, usuário tenta de novo
    _marcar_tickets_finalizados([id_posicao], id_trade)

    with persistencia.lock_posicoes_abertas(
            tentativas=cfg.DIARIO_LOCK_USUARIO_TENTATIVAS,
            espera_segundos=cfg.DIARIO_LOCK_USUARIO_ESPERA_S) as adquirida:
        if not adquirida:
            return id_trade  # trade já gravado; remoção do rascunho fica pro próximo ciclo/tentativa
        df = persistencia.carregar_posicoes_abertas()
        if df is not None and not df.empty:
            df_restante = df[df["id_posicao"] != id_posicao]
            persistencia.salvar_posicoes_abertas(df_restante)
    return id_trade


def finalizar_par_hedge(
    ticket_a: int,
    ticket_b: int,
    campos_classificacao: dict,
    imagem_principal: str | None = None,
) -> int | None:
    """
    Equivalente a `finalizar_posicao`, mas pra um PAR de hedge (ver
    `detectar_pares_hedge`) — grava 1 único trade líquido em
    `diario_trades.parquet`, não 2 linhas separadas. Mesma doutrina: nunca
    escreve direto no diário, só monta `campos` e reaproveita
    `registrar_trade()` sem alteração.

    A classificação (tags/sala/notas/imagens) vem da perna que abriu
    PRIMEIRO (é "o trade" do ponto de vista do usuário — a perna mais nova
    só contribui o preço/hora de saída, mesmo critério de
    `detectar_pares_hedge`). O ticket da perna de saída entra no `obs` só
    pra rastreabilidade (qual ordem MT5 gerou o fechamento).

    Retorna o `id_trade` criado, ou None se a trava estava ocupada, se
    algum dos 2 tickets não existir mais, se qualquer uma das 2 pernas JÁ
    tiver sido finalizada antes (ver a trava de idempotência abaixo), ou se
    `registrar_trade` falhar (mesmas condições de `finalizar_posicao`) —
    nesses casos as 2 linhas do rascunho são preservadas intactas.

    Bug real 2026-07-22: esta função recalculava pnl/preço de saída do
    zero (entrada-vs-entrada), duplicando — e divergindo de — a lógica já
    corrigida em `detectar_pares_hedge` (que soma o resultado REAL de cada
    perna quando ambas já fecharam, em vez de descartar o que a perna de
    saída rendeu depois). Agora reaproveita o par já calculado por
    `detectar_pares_hedge` como ÚNICA fonte de verdade — nunca recalcula
    por conta própria.
    """
    with persistencia.lock_posicoes_abertas(
            tentativas=cfg.DIARIO_LOCK_USUARIO_TENTATIVAS,
            espera_segundos=cfg.DIARIO_LOCK_USUARIO_ESPERA_S) as adquirida:
        if not adquirida:
            return None
        # TRAVA DE IDEMPOTÊNCIA — ver `finalizar_posicao` pro raciocínio
        # completo (bug real 2026-07-27). Basta UMA perna já marcada pra
        # recusar: as 2 são marcadas juntas quando o par é finalizado, então
        # uma perna na lápide já significa "este trade líquido já foi gravado".
        ja_finalizados = _tickets_ja_finalizados()
        if int(ticket_a) in ja_finalizados or int(ticket_b) in ja_finalizados:
            return None
        df = persistencia.carregar_posicoes_abertas()
        if df is None or df.empty:
            return None
        ids_presentes = set(df["id_posicao"])
        if ticket_a not in ids_presentes or ticket_b not in ids_presentes:
            return None

        par = next((p for p in detectar_pares_hedge(df)
                    if {p["ticket_a"], p["ticket_b"]} == {ticket_a, ticket_b}), None)
        if par is None:
            return None  # não são mais um par elegível (ex: mudou de eligibilidade entre a leitura e o clique)

        linha_a = df[df["id_posicao"] == ticket_a].iloc[0].to_dict()
        linha_b = df[df["id_posicao"] == ticket_b].iloc[0].to_dict()
        if linha_a["datetime_entrada"] <= linha_b["datetime_entrada"]:
            perna_entrada, perna_saida = linha_a, linha_b
        else:
            perna_entrada, perna_saida = linha_b, linha_a

        pnl_pontos, pnl_reais = par["pnl_pontos"], par["pnl_reais"]
        preco_saida_par, datetime_saida_par = par["preco_saida"], par["datetime_saida"]

        notas = _para_lista(perna_entrada.get("notas_cronologicas"))
        narrativa = "\n".join(f"[{n['hora']}] {n['texto']}" for n in notas) or None
        narrativa_par = (
            f"[par hedge — tickets MT5 {int(perna_entrada['id_posicao'])} (entrada) "
            f"→ {int(perna_saida['id_posicao'])} (saída)]"
            + (f"\n{narrativa}" if narrativa else "")
        )

        imagens_galeria = _para_lista(perna_entrada.get("imagens_em_andamento"))
        imagens_restantes = [img for img in imagens_galeria if img != imagem_principal]

        campos = {
            "estrategia_tags": _para_lista(perna_entrada.get("estrategia_tags_preliminar")),
            "sala_analise": perna_entrada.get("sala_analise_preliminar"),
            "alvo_pontos": perna_entrada.get("alvo_pontos_preliminar"),
            "stop_pontos": perna_entrada.get("stop_pontos_preliminar"),
            "nivel_sr_referencia": perna_entrada.get("nivel_sr_referencia_preliminar"),
            "resumo_operacional": perna_entrada.get("resumo_operacional_preliminar"),
            "emo_confianca": perna_entrada.get("emo_confianca_preliminar"),
            "emo_humor": perna_entrada.get("emo_humor_preliminar"),
            "emo_energia": perna_entrada.get("emo_energia_preliminar"),
            "emo_atencao": perna_entrada.get("emo_atencao_preliminar"),
            "emo_presenca": perna_entrada.get("emo_presenca_preliminar"),
            "tags_emocionais": _para_lista(perna_entrada.get("tags_emocionais_preliminar")),
            "sentimento_mercado": perna_entrada.get("sentimento_mercado_preliminar"),
            "ativo": perna_entrada["ativo"], "contrato": perna_entrada["contrato"],
            "tipo_operacao": perna_entrada["tipo_operacao"], "lote": perna_entrada["volume"],
            "preco_entrada": perna_entrada["preco_entrada"], "datetime_entrada": perna_entrada["datetime_entrada"],
            # `preco_saida`/`datetime_saida` exibidos vêm do PAR (real/
            # estimado combinado, ver detectar_pares_hedge) — nunca mais
            # só o preço de entrada da perna de saída, que ficou
            # desatualizado assim que ela também fecha por conta própria.
            "preco_saida": preco_saida_par, "datetime_saida": datetime_saida_par,
            "resultado_pontos": pnl_pontos, "saldo_operacao_reais": pnl_reais,
            "data_op": pd.Timestamp(perna_entrada["datetime_entrada"]).date(),
            # `tipo_saida` (stop/alvo/manual) continua comparando contra o
            # preço de ENTRADA da perna de saída de propósito — é o que
            # realmente fechou A (o SL/TP de A disparando é o que criou a
            # perna B), independente do que aconteceu com B depois.
            "tipo_saida": derivar_tipo_saida(
                perna_entrada["tipo_operacao"], perna_entrada["preco_entrada"], perna_saida["preco_entrada"],
                perna_entrada.get("sl"), perna_entrada.get("tp"),
            ),
            "obs": narrativa_par,
            "imagens_adicionais": imagens_restantes,
        }
        campos.update(campos_classificacao)

        imagem_bytes = None
        if imagem_principal:
            caminho_img = cfg.PASTA_DIARIO_IMAGENS / imagem_principal
            if caminho_img.exists():
                imagem_bytes = caminho_img.read_bytes()

    id_trade = registrar_trade(campos, imagem_bytes=imagem_bytes)
    if id_trade is None:
        return None
    _marcar_tickets_finalizados([ticket_a, ticket_b], id_trade)

    with persistencia.lock_posicoes_abertas(
            tentativas=cfg.DIARIO_LOCK_USUARIO_TENTATIVAS,
            espera_segundos=cfg.DIARIO_LOCK_USUARIO_ESPERA_S) as adquirida:
        if not adquirida:
            return id_trade
        df = persistencia.carregar_posicoes_abertas()
        if df is not None and not df.empty:
            df_restante = df[~df["id_posicao"].isin([ticket_a, ticket_b])]
            persistencia.salvar_posicoes_abertas(df_restante)
    return id_trade


# =============================================================================
# ORDENS PENDENTES (ainda não executadas) — mesma seção "Posição" do Diário
# =============================================================================
# Ao contrário de `posicoes_abertas`, aqui não há nada pra o usuário editar
# (nota/tags/imagem) — é puramente informativo até a ordem executar (vira
# posição, capturada por `sincronizar_posicoes_abertas`) ou ser cancelada.
# Por isso o sync é um simples "substitui tudo" a cada ciclo, sem diff
# nem preservar campo nenhum entre chamadas.

COLUNAS_ORDENS_PENDENTES = [
    "ticket", "ativo", "contrato", "tipo_ordem", "volume",
    "preco_pedido", "sl", "tp", "datetime_criacao", "comment",
]


def sincronizar_ordens_pendentes(df_ordens_mt5: pd.DataFrame | None) -> None:
    """Substitui `ordens_pendentes.parquet` pelo snapshot atual de
    `mt5_loader.buscar_ordens_pendentes()`. `None` (falha de MT5) não apaga
    o que já existe — só um DataFrame vazio de verdade (sem ordens agora)
    zera a tabela."""
    if df_ordens_mt5 is None:
        return
    persistencia.salvar_ordens_pendentes(df_ordens_mt5[COLUNAS_ORDENS_PENDENTES] if not df_ordens_mt5.empty
                                          else pd.DataFrame(columns=COLUNAS_ORDENS_PENDENTES))


def listar_ordens_pendentes() -> pd.DataFrame:
    """Carrega a tabela de ordens pendentes. DataFrame vazio (colunas
    garantidas) se nada foi sincronizado ainda."""
    df = persistencia.carregar_ordens_pendentes()
    if df is None:
        return pd.DataFrame(columns=COLUNAS_ORDENS_PENDENTES)
    return df
