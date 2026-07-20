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
import math
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
# 51-74 Ganância, 75-100 Ganância Extrema.
SENTIMENTO_MERCADO_FAIXAS = [
    (0, 24, "Medo Extremo", "red"),
    (25, 49, "Medo", "yellow"),
    (50, 50, "Neutro", "text2"),
    (51, 74, "Ganância", "yellow"),
    (75, 100, "Ganância Extrema", "green"),
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
# check-in + trades do dia (mesmo espírito de `avaliar_aderencia_sr`: nunca
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
]

# Idem — recomputável via `avaliar_aderencia_sr`, não é histórico do trade.
COLUNAS_ADERENCIA_SR = [
    "id_trade", "data_op", "ativo", "nivel_sr_referencia", "nivel_sr_tipo_informado",
    "lado_geometrico", "nivel_motor_preco", "nivel_motor_rank", "diferenca_atr",
    "forte", "status", "prob_bounce", "rompimento_hoje", "classificacao",
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

    resultado = {}
    for tag in ESTRATEGIA_TAGS_CANONICAS:
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
        resultado = pd.DataFrame(columns=COLUNAS_RECONCILIACAO)
        persistencia.salvar_reconciliacao(resultado)
        return resultado

    posicoes = mt5_loader.agrupar_posicoes(df_deals)
    posicoes["data"] = posicoes["datetime_entrada"].apply(lambda t: t.date() if t is not None else None)
    posicoes["usado"] = False

    trades = listar_trades(data_ini=inicio.date(), data_fim=fim.date(), is_simulated=False)

    linhas = []
    for _, trade in trades.iterrows():
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
    # execuções que aconteceram mas não foram registradas no diário.
    for _, pos in posicoes[~posicoes["usado"]].iterrows():
        linhas.append({
            "id_trade": None, "mt5_position_id": pos["position_id"],
            "mt5_ticket_entrada": pos["tickets_entrada"][0] if pos["tickets_entrada"] else None,
            "mt5_ticket_saida": pos["tickets_saida"][-1] if pos["tickets_saida"] else None,
            "mt5_divergencia_reais": None, "confianca": None,
            "mt5_match_status": "sem_correspondencia_diario",
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


# Thresholds de classificação de `avaliar_aderencia_sr` — primeiro palpite,
# não validado ainda contra casos reais. Ajustar aqui depois de conferir a
# olho 3-5 trades reais (mesmo padrão de validação empírica já usado no
# projeto — nunca confiar de olhos fechados numa métrica nova).
ADERENCIA_SR_ATR_PRECISO = 0.15
ADERENCIA_SR_ATR_PROXIMO = 0.5


def avaliar_aderencia_sr(dias_atras: int | None = None) -> pd.DataFrame:
    """
    Compara, para cada trade com `nivel_sr_referencia` preenchido, o nível
    marcado à mão por Jesué contra o que o motor via no instante do save
    (`snapshot_mercado_json`, capturado por `metricas.montar_snapshot_diario`
    em `registrar_trade`). Responde "o motor via esse nível?" — relatório
    de REVISÃO MANUAL, nunca ajuste automático de peso/lógica (volume de
    dados baixo demais pra isso ser seguro, mesma decisão já tomada pra
    reconciliação MT5).

    Só considera trades com `nivel_sr_referencia` E `snapshot_mercado_json`
    preenchidos — linhas migradas/antigas não têm snapshot e ficam de fora
    automaticamente. `dias_atras`, se informado, restringe por `data_op`
    (mesmo parâmetro de `reconciliar_com_mt5`).
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
        if preco_snapshot is None:
            continue

        lado = "abaixo" if nivel_ref < preco_snapshot else "acima"
        candidatos = snapshot.get(f"niveis_sr_{lado}") or []

        casado = None
        diferenca_atr = None
        if candidatos:
            casado = min(candidatos, key=lambda n: abs((n.get("preco") or 0) - nivel_ref))
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
            "nivel_motor_preco": casado.get("preco") if casado else None,
            "nivel_motor_rank": casado.get("rank") if casado else None,
            "diferenca_atr": diferenca_atr,
            "forte": casado.get("forte") if casado else None,
            "status": casado.get("status") if casado else None,
            "prob_bounce": casado.get("prob_bounce") if casado else None,
            "rompimento_hoje": casado.get("rompimento_hoje") if casado else None,
            "classificacao": classificacao,
        })

    resultado = pd.DataFrame(linhas, columns=COLUNAS_ADERENCIA_SR)
    persistencia.salvar_aderencia_sr(resultado)
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

    Relatório de REVISÃO MANUAL, mesmo espírito de `avaliar_aderencia_sr`/
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
            df_deals = mt5_loader.buscar_execucoes_periodo(inicio, agora)
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
    de `avaliar_aderencia_sr`/`detectar_padroes_comportamentais` (computada
    fresca a cada render, nunca persistida à parte).

    Considera elegíveis posições com status `aberta` OU
    `fechada_pendente` — um par pode existir mesmo que uma ponta ainda não
    tenha sido corrigida pelo ciclo de sync seguinte (ver auto-recuperação
    em `sincronizar_posicoes_abertas`).

    A perna aberta PRIMEIRO (menor `datetime_entrada`) é a "entrada" do
    trade líquido (direção dela vira a direção do trade); a perna mais
    NOVA é a "saída" (economicamente equivale a fechar a primeira no preço
    da segunda). Volume diferente = sem par nesta v1 (hedge parcial fica
    documentado como fora de escopo, não silenciosamente errado).

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
            short_pos = candidatos.iloc[0]
            usados.add(long_pos["id_posicao"])
            usados.add(short_pos["id_posicao"])

            if long_pos["datetime_entrada"] <= short_pos["datetime_entrada"]:
                perna_entrada, perna_saida = long_pos, short_pos
            else:
                perna_entrada, perna_saida = short_pos, long_pos

            pnl_pontos = _pnl_pontos(perna_entrada["tipo_operacao"],
                                      perna_entrada["preco_entrada"], perna_saida["preco_entrada"])
            valor_ponto = cfg.VALOR_PONTO.get(perna_entrada["ativo"], 0.0)
            pnl_reais = pnl_pontos * valor_ponto * perna_entrada["volume"]

            pares.append({
                "ticket_a": int(perna_entrada["id_posicao"]), "ticket_b": int(perna_saida["id_posicao"]),
                "contrato": contrato, "ativo": perna_entrada["ativo"], "volume": perna_entrada["volume"],
                "tipo_operacao": perna_entrada["tipo_operacao"],
                "preco_entrada": perna_entrada["preco_entrada"], "datetime_entrada": perna_entrada["datetime_entrada"],
                "preco_saida": perna_saida["preco_entrada"], "datetime_saida": perna_saida["datetime_entrada"],
                "pnl_pontos": pnl_pontos, "pnl_reais": pnl_reais,
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
    pra finalizar), `saida_estimada` sempre `False` (não é estimativa, é o
    preço real da perna que fechou) — `par_hedge=True` marca explicitamente
    a origem sintética, pra UI distinguir do card antigo de "saída não
    confirmada" (são situações diferentes: aqui a saída É confirmada, só
    que veio de uma posição oposta, não de uma perna OUT no mesmo ticket).
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
        "saida_estimada": False,
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
    with persistencia.lock_posicoes_abertas(tentativas=8, espera_segundos=0.15) as adquirida:
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
    with persistencia.lock_posicoes_abertas(tentativas=8, espera_segundos=0.15) as adquirida:
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
    ocupada, se `id_posicao` não existir, ou se `registrar_trade` falhar
    (trava do diário ocupada) — nesses casos o rascunho é preservado
    intacto, nunca perdido.
    """
    with persistencia.lock_posicoes_abertas(tentativas=8, espera_segundos=0.15) as adquirida:
        if not adquirida:
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

    with persistencia.lock_posicoes_abertas(tentativas=8, espera_segundos=0.15) as adquirida:
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
    algum dos 2 tickets não existir mais, ou se `registrar_trade` falhar
    (mesmas condições de `finalizar_posicao`) — nesses casos as 2 linhas
    do rascunho são preservadas intactas.
    """
    with persistencia.lock_posicoes_abertas(tentativas=8, espera_segundos=0.15) as adquirida:
        if not adquirida:
            return None
        df = persistencia.carregar_posicoes_abertas()
        if df is None or df.empty:
            return None
        ids_presentes = set(df["id_posicao"])
        if ticket_a not in ids_presentes or ticket_b not in ids_presentes:
            return None

        linha_a = df[df["id_posicao"] == ticket_a].iloc[0].to_dict()
        linha_b = df[df["id_posicao"] == ticket_b].iloc[0].to_dict()
        if linha_a["datetime_entrada"] <= linha_b["datetime_entrada"]:
            perna_entrada, perna_saida = linha_a, linha_b
        else:
            perna_entrada, perna_saida = linha_b, linha_a

        pnl_pontos = _pnl_pontos(perna_entrada["tipo_operacao"],
                                  perna_entrada["preco_entrada"], perna_saida["preco_entrada"])
        valor_ponto = cfg.VALOR_PONTO.get(perna_entrada["ativo"], 0.0)
        pnl_reais = pnl_pontos * valor_ponto * perna_entrada["volume"]

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
            "preco_saida": perna_saida["preco_entrada"], "datetime_saida": perna_saida["datetime_entrada"],
            "resultado_pontos": pnl_pontos, "saldo_operacao_reais": pnl_reais,
            "data_op": pd.Timestamp(perna_entrada["datetime_entrada"]).date(),
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

    with persistencia.lock_posicoes_abertas(tentativas=8, espera_segundos=0.15) as adquirida:
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
