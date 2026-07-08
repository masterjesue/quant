# =============================================================================
# persistencia.py — Salvar e carregar a base processada em Parquet
# =============================================================================
# Responsabilidade: única fonte de leitura/escrita em disco. Tanto o merge
# pesado (setup inicial) quanto a atualização leve (dia a dia) passam por
# aqui — nunca se toca em arquivo Parquet fora deste módulo.
# =============================================================================

import os
import time
import pandas as pd
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager

import config_backtest as cfg


def _caminho_candles(ativo: str) -> Path:
    return cfg.PASTA_BASE_PROCESSADA / f"{ativo}_candles.parquet"


# -----------------------------------------------------------------------------
# TRAVA DE ATUALIZAÇÃO ENTRE PROCESSOS
# -----------------------------------------------------------------------------
# Desde 2026-07-08 existem DOIS dashboards (dashboard.py e dashboard_98.py),
# cada um seu próprio processo do SO, cada um com sua própria thread de
# atualização incremental (na carga inicial e a cada 30s). O `threading.Lock`
# de cada dashboard.py só protege threads DENTRO do mesmo processo — não
# impede os dois processos de fazer update ao mesmo tempo. Isso corrompeu
# `WIN_candles.parquet` de verdade (2026-07-08: "Couldn't deserialize
# thrift", clássico de escrita concorrente truncada) — mesma classe de
# incidente já visto em 2026-07-02 (WDO), mas agora estrutural, não um
# acidente de script avulso. Trava de arquivo (criação atômica via O_EXCL,
# funciona em Windows e POSIX) garante só um processo atualizando por vez;
# o outro pula o ciclo em silêncio e continua servindo os dados já
# carregados em memória — só fica levemente desatualizado por até 30s, o
# que é um trade-off muito melhor que corromper o arquivo.

_LOCK_STALE_SEGUNDOS = 120  # bem acima do tempo normal de um update — proteção contra lock órfão de processo morto

def _caminho_lock() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / '.atualizacao.lock'


@contextmanager
def lock_atualizacao():
    """
    Uso: `with persistencia.lock_atualizacao() as adquirida: if adquirida: ...`
    Não bloqueia — se outro processo já segura a trava, retorna False na
    hora (o chamador decide pular o update deste ciclo).
    """
    caminho = _caminho_lock()
    caminho.parent.mkdir(parents=True, exist_ok=True)

    if caminho.exists():
        try:
            idade = time.time() - caminho.stat().st_mtime
        except FileNotFoundError:
            idade = 0
        if idade > _LOCK_STALE_SEGUNDOS:
            # provável processo morto sem limpar a trava — remove e segue
            try:
                caminho.unlink()
            except FileNotFoundError:
                pass

    try:
        fd = os.open(caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        yield False
        return

    try:
        yield True
    finally:
        try:
            caminho.unlink()
        except FileNotFoundError:
            pass


def _caminho_metadata(ativo: str) -> Path:
    return cfg.PASTA_BASE_PROCESSADA / f"{ativo}_metadata.parquet"


def _salvar_parquet_atomico(caminho: Path, df: pd.DataFrame):
    """
    Escreve em Parquet SEM NUNCA expor um arquivo truncado a um leitor
    concorrente. Descoberto em 2026-07-08: `df.to_parquet(caminho)` escreve
    DIRETO no destino final — um leitor (o próprio dashboard no próximo
    ciclo, ou qualquer script externo) que ler bem nesse instante vê um
    arquivo parcial ("Parquet magic bytes not found in footer"). Isso é
    DIFERENTE da corrupção de 2026-07-08 anterior (dois PROCESSOS
    escrevendo ao mesmo tempo, resolvida por `lock_atualizacao()`) — aqui
    o problema é escritor-vs-LEITOR, que a trava entre dashboards não
    cobre (ela só serializa os dois escritores entre si).

    Corrigido com o padrão clássico: grava num arquivo temporário e troca
    com `os.replace()`, que é atômico tanto no Windows quanto no POSIX —
    qualquer leitor concorrente sempre vê OU o arquivo antigo completo OU
    o novo completo, nunca um estado parcial. Nome do temporário inclui o
    PID pra nunca colidir mesmo se dois processos escreverem ao mesmo
    tempo fora da trava (ex.: `run_backtest.py --features` rodando enquanto
    um dashboard também escreve) — cada um troca o seu, "última escrita
    vence" continua sendo o comportamento, só que sem nunca expor um
    arquivo quebrado no meio do caminho.
    """
    caminho_tmp = caminho.with_name(f"{caminho.stem}.tmp{os.getpid()}{caminho.suffix}")
    df.to_parquet(caminho_tmp, index=False)
    os.replace(caminho_tmp, caminho)


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
    _salvar_parquet_atomico(caminho, df)
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
    _salvar_parquet_atomico(caminho, df_meta)
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
    _salvar_parquet_atomico(caminho, df_parcial)


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


# -----------------------------------------------------------------------------
# DIÁRIO DE TRADE
# -----------------------------------------------------------------------------
# Mesmo padrão de gateway único usado para candles acima, mas com um arquivo
# de lock PRÓPRIO — escrita do diário (disparada por clique do usuário) nunca
# deve esperar/ser bloqueada pela trava de atualização de candle, e vice-versa.

def _caminho_diario() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_trades.parquet"


def _caminho_diario_lock() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / '.diario.lock'


@contextmanager
def lock_diario():
    """
    Mesmo mecanismo de `lock_atualizacao()` (criação atômica via O_EXCL),
    trava separada. Uso: `with persistencia.lock_diario() as adquirida: if
    adquirida: ...` — se ocupada, quem chama decide como reagir (aqui, ao
    contrário do update de candle, a escrita foi disparada por um clique do
    usuário, então o chamador deve mostrar um erro/retry visível, não pular
    em silêncio).
    """
    caminho = _caminho_diario_lock()
    caminho.parent.mkdir(parents=True, exist_ok=True)

    if caminho.exists():
        try:
            idade = time.time() - caminho.stat().st_mtime
        except FileNotFoundError:
            idade = 0
        if idade > _LOCK_STALE_SEGUNDOS:
            try:
                caminho.unlink()
            except FileNotFoundError:
                pass

    try:
        fd = os.open(caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        yield False
        return

    try:
        yield True
    finally:
        try:
            caminho.unlink()
        except FileNotFoundError:
            pass


def salvar_diario(df: pd.DataFrame):
    """Salva a tabela completa do diário de trades (uma operação = uma linha)."""
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_diario(), index=False)
    print(f"[persistencia] diário: {len(df)} trades salvos em {_caminho_diario()}")


def carregar_diario() -> pd.DataFrame | None:
    """Carrega a tabela do diário. Retorna None se ainda não existir (antes da 1ª migração/trade)."""
    caminho = _caminho_diario()
    if not caminho.exists():
        return None
    df = pd.read_parquet(caminho)
    print(f"[persistencia] diário: {len(df)} trades carregados de {caminho}")
    return df


def _caminho_reconciliacao() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_reconciliacao.parquet"


def salvar_reconciliacao(df: pd.DataFrame):
    """
    Salva a tabela de reconciliação MT5×diário — tabela separada da
    principal (não colunas extras em `diario_trades.parquet`) pra manter o
    log de trades append-only/imutável; reconciliação é recomputável a
    qualquer momento, não histórico do próprio trade.
    """
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_reconciliacao(), index=False)
    print(f"[persistencia] reconciliação: {len(df)} linhas salvas em {_caminho_reconciliacao()}")


def carregar_reconciliacao() -> pd.DataFrame | None:
    """Carrega a tabela de reconciliação salva. Retorna None se ainda não existir."""
    caminho = _caminho_reconciliacao()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)
