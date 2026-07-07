# =============================================================================
# persistencia.py — Salvar e carregar a base processada em Parquet
# =============================================================================
# Responsabilidade: única fonte de leitura/escrita em disco. Tanto o merge
# pesado (setup inicial) quanto a atualização leve (dia a dia) passam por
# aqui — nunca se toca em arquivo Parquet fora deste módulo.
# =============================================================================

import pandas as pd
from pathlib import Path
from datetime import datetime

import config_backtest as cfg


def _caminho_candles(ativo: str) -> Path:
    return cfg.PASTA_BASE_PROCESSADA / f"{ativo}_candles.parquet"


def _caminho_metadata(ativo: str) -> Path:
    return cfg.PASTA_BASE_PROCESSADA / f"{ativo}_metadata.parquet"


def salvar_candles(ativo: str, df: pd.DataFrame):
    """Salva a série de candles consolidada (CSV+MT5+features) em Parquet."""
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    caminho = _caminho_candles(ativo)
    if df.attrs:
        # df.attrs guarda plumbing interno do pipeline de features (catálogo de
        # clusters/zonas de S/R, arrays numpy) — nunca deve ser persistido, e
        # to_parquet tenta serializar attrs como JSON, o que quebra com ndarray.
        df = df.copy()
        df.attrs = {}
    df.to_parquet(caminho, index=False)
    print(f"[persistencia] {ativo}: {len(df)} candles salvos em {caminho}")


def carregar_candles(ativo: str) -> pd.DataFrame | None:
    """Carrega a série de candles salva. Retorna None se ainda não existir."""
    caminho = _caminho_candles(ativo)
    if not caminho.exists():
        return None
    df = pd.read_parquet(caminho)
    print(f"[persistencia] {ativo}: {len(df)} candles carregados de {caminho}")
    return df


def salvar_metadata(ativo: str, metadata: dict):
    """
    Salva metadados da última atualização: data/hora do merge, última
    data coberta, contrato usado, etc. Usado pela atualização incremental
    para saber de onde continuar.
    """
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    caminho = _caminho_metadata(ativo)
    df_meta = pd.DataFrame([metadata])
    df_meta.to_parquet(caminho, index=False)
    print(f"[persistencia] {ativo}: metadata salva em {caminho}")


def carregar_metadata(ativo: str) -> dict | None:
    """Carrega os metadados salvos. Retorna None se não existir."""
    caminho = _caminho_metadata(ativo)
    if not caminho.exists():
        return None
    df_meta = pd.read_parquet(caminho)
    return df_meta.iloc[0].to_dict()


def salvar_checkpoint(ativo: str, df_parcial: pd.DataFrame):
    """
    Salva um checkpoint PARCIAL durante processamento pesado (ex: cálculo
    de delta sobre 2 anos de candles). Usado para nunca perder trabalho se
    o processo travar ou for interrompido no meio — não é a base final,
    é um ponto de recuperação intermediário.
    """
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    caminho = cfg.PASTA_BASE_PROCESSADA / f"{ativo}_checkpoint.parquet"
    df_parcial.to_parquet(caminho, index=False)


def carregar_checkpoint(ativo: str) -> pd.DataFrame | None:
    """Carrega um checkpoint parcial salvo, se existir."""
    caminho = cfg.PASTA_BASE_PROCESSADA / f"{ativo}_checkpoint.parquet"
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


def limpar_checkpoint(ativo: str):
    """Remove o checkpoint após o processamento ter sido concluído com sucesso."""
    caminho = cfg.PASTA_BASE_PROCESSADA / f"{ativo}_checkpoint.parquet"
    if caminho.exists():
        caminho.unlink()


def base_existe(ativo: str) -> bool:
    """Checa se já existe base processada salva para o ativo."""
    return _caminho_candles(ativo).exists()
