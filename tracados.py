# =============================================================================
# tracados.py — Traçados manuais no gráfico (aba/janela "Traçados")
# =============================================================================
# Mesmo espírito de `diario_trade.py`/`metricas.py`: módulo de dados puro,
# compartilhado pelos dois front-ends (dashboard.py e dashboard_98.py), zero
# construção de HTML aqui. Planejado em 2026-07-12 — ver
# `C:\Users\jesue\.claude\plans\analise-o-bloco-de-glimmering-marble.md`.
#
# Um traçado = um objeto desenhado à mão no gráfico (nível de S/R horizontal,
# por enquanto) — Jesué marca no gráfico o que já marca hoje no ProfitPRO, e
# o motor roda a MESMA leitura quantitativa de sempre (toques/força/
# probabilidade) em cima do que foi desenhado, via
# `features.analisar_linha_generica` (motor já existente, construído em
# 2026-07-10 pensando exatamente neste caso de uso — ver
# `metricas.analisar_tracados`, que faz a ponte).
#
# Tabela SEPARADA de `diario_trades.parquet` (que continua append-only/
# imutável) e de `posicoes_abertas.parquet` — traçados de gráfico não são
# trade nem posição, são uma ferramenta de análise do motor quant. Escopados
# por `ativo` (WIN e WDO nunca compartilham traçados — instrumentos/escalas
# de preço diferentes).
#
# ROADMAP (registrado a pedido do Jesué — NÃO implementado nesta fase, ver
# seção G do plano):
#   - LTA/LTB (tipo='trendline'): 2 pontos (ponto1_tempo/preco,
#     ponto2_tempo/preco, já reservados no schema abaixo).
#     `features.analisar_linha_generica` JÁ aceita esse shape (`linha` = um
#     valor por candle, via interpolação linear entre os 2 pontos,
#     extrapolando pra fora do intervalo) — falta só a ferramenta de
#     desenho de 2 cliques no client-side e montar o array `linha` aqui.
#   - Canais (tipo='canal'): par de trendlines paralelas — provavelmente 2
#     traçados linkados por um `canal_id` comum, 2 chamadas de
#     `analisar_linha_generica` (uma por borda) + leitura combinada nova
#     (largura do canal, preço dentro/fora).
#   - Figuras gráficas (triângulos, OCO, bandeiras): NÃO se reduz a uma
#     única `linha` — polígonos/múltiplos segmentos com regras de
#     reconhecimento próprias, não reaproveitam `analisar_linha_generica`
#     diretamente. Escopo bem maior, fase própria futura (aparecem nas 122
#     imagens do diário.xlsm — ver CHANGELOG.md — junto com Fibonacci).
# =============================================================================

from __future__ import annotations

import math
import uuid
from datetime import datetime

import pandas as pd

import persistencia

COLUNAS_TRACADOS = [
    "id_tracado", "ativo", "tipo",
    "preco",                                    # usado por tipo='horizontal'
    "ponto1_tempo", "ponto1_preco",              # reservado p/ 'trendline'/'canal'
    "ponto2_tempo", "ponto2_preco",              # reservado p/ 'trendline'/'canal'
    "cor", "espessura", "estilo", "criado_em", "atualizado_em",
]

TIPOS_IMPLEMENTADOS = {"horizontal"}  # 'trendline'/'canal'/'figura' — ver ROADMAP acima
ESTILOS_LINHA = {"solid", "dashed", "dotted"}  # ver LightweightCharts.LineStyle no cliente
ESPESSURAS_VALIDAS = {1, 2, 3, 4}              # mesmo intervalo aceito por lineWidth na lib

_ESPESSURA_PADRAO = 2
_ESTILO_PADRAO = "solid"


def _normalizar_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Preenche colunas que faltam (arquivo criado antes de `espessura`/
    `estilo` existirem — 2026-07-13) com os valores padrão, sem exigir
    migração manual do parquet já em disco."""
    if "espessura" not in df.columns:
        df["espessura"] = _ESPESSURA_PADRAO
    if "estilo" not in df.columns:
        df["estilo"] = _ESTILO_PADRAO
    df["espessura"] = df["espessura"].fillna(_ESPESSURA_PADRAO)
    df["estilo"] = df["estilo"].fillna(_ESTILO_PADRAO)
    return df


def listar_tracados(ativo: str) -> pd.DataFrame:
    """Carrega os traçados do `ativo` informado. DataFrame vazio (colunas
    garantidas) se nada foi desenhado ainda."""
    df = persistencia.carregar_tracados()
    if df is None:
        return pd.DataFrame(columns=COLUNAS_TRACADOS)
    df = _normalizar_schema(df)
    return df[df["ativo"] == ativo].reset_index(drop=True)


def criar_tracado(ativo: str, tipo: str, preco: float, cor: str | None = None,
                   espessura: int = _ESPESSURA_PADRAO, estilo: str = _ESTILO_PADRAO) -> str | None:
    """
    Cria um traçado novo (hoje só `tipo='horizontal'`) e retorna o
    `id_tracado` gerado (uuid4 hex — não sequencial: o cliente pode
    referenciar esse id pra arrastar/apagar antes mesmo de qualquer leitura
    de volta ao servidor). Retorna None se a trava estava ocupada.
    """
    with persistencia.lock_tracados() as adquirida:
        if not adquirida:
            return None
        df = persistencia.carregar_tracados()
        df = _normalizar_schema(df) if df is not None else pd.DataFrame(columns=COLUNAS_TRACADOS)

        agora = datetime.now()
        id_tracado = uuid.uuid4().hex
        nova = {coluna: None for coluna in COLUNAS_TRACADOS}
        nova.update({
            "id_tracado": id_tracado, "ativo": ativo, "tipo": tipo,
            "preco": preco, "cor": cor, "espessura": espessura, "estilo": estilo,
            "criado_em": agora, "atualizado_em": agora,
        })
        df = pd.concat([df, pd.DataFrame([nova], columns=COLUNAS_TRACADOS)], ignore_index=True)
        persistencia.salvar_tracados(df)
        return id_tracado


def atualizar_preco_tracado(id_tracado: str, preco: float) -> bool:
    """Atualiza o preço de um traçado horizontal (chamado no `mouseup` do
    arraste, não a cada `mousemove` — ver assets/lwchart.js). Retorna False
    se a trava estava ocupada ou o id não existe."""
    with persistencia.lock_tracados() as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_tracados()
        if df is None or df.empty or id_tracado not in set(df["id_tracado"]):
            return False
        idx = df.index[df["id_tracado"] == id_tracado][0]
        df.at[idx, "preco"] = preco
        df.at[idx, "atualizado_em"] = datetime.now()
        persistencia.salvar_tracados(df)
        return True


def atualizar_tracado(id_tracado: str, preco: float, cor: str,
                       espessura: int, estilo: str) -> bool:
    """Atualiza preço/cor/espessura/estilo de um traçado de uma vez só —
    chamado pelo "Salvar" do popup de edição (duplo clique na linha, ver
    assets/lwchart.js e o modal em dashboard.py/dashboard_98.py). Diferente
    de `atualizar_preco_tracado` (só arraste, só preço): aqui sempre
    sobrescreve os 4 campos editáveis juntos, já que o popup sempre mostra
    e permite editar os 4. Retorna False se a trava estava ocupada ou o id
    não existe."""
    if not math.isfinite(preco):
        return False
    if estilo not in ESTILOS_LINHA:
        estilo = _ESTILO_PADRAO
    if espessura not in ESPESSURAS_VALIDAS:
        espessura = _ESPESSURA_PADRAO
    with persistencia.lock_tracados() as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_tracados()
        if df is None or df.empty or id_tracado not in set(df["id_tracado"]):
            return False
        df = _normalizar_schema(df)
        idx = df.index[df["id_tracado"] == id_tracado][0]
        df.at[idx, "preco"] = preco
        df.at[idx, "cor"] = cor
        df.at[idx, "espessura"] = espessura
        df.at[idx, "estilo"] = estilo
        df.at[idx, "atualizado_em"] = datetime.now()
        persistencia.salvar_tracados(df)
        return True


def remover_tracado(id_tracado: str) -> bool:
    """Remove um traçado. Retorna False se a trava estava ocupada ou o id
    não existe (idempotente do ponto de vista do chamador: se já não existe,
    o efeito desejado — traçado ausente — já está satisfeito, mas ainda
    assim reporta False pra UI não fingir que apagou algo que não achou)."""
    with persistencia.lock_tracados() as adquirida:
        if not adquirida:
            return False
        df = persistencia.carregar_tracados()
        if df is None or df.empty or id_tracado not in set(df["id_tracado"]):
            return False
        df = df[df["id_tracado"] != id_tracado].reset_index(drop=True)
        persistencia.salvar_tracados(df)
        return True
