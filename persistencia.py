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


def _caminho_aderencia_sr() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_aderencia_sr.parquet"


def salvar_aderencia_sr(df: pd.DataFrame):
    """
    Salva a tabela de aderência S/R (nível marcado à mão × snapshot do
    motor) — tabela separada da principal, mesmo motivo de
    `salvar_reconciliacao`: recomputável a qualquer momento, não histórico
    imutável do próprio trade.
    """
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_aderencia_sr(), index=False)
    print(f"[persistencia] aderência S/R: {len(df)} linhas salvas em {_caminho_aderencia_sr()}")


def carregar_aderencia_sr() -> pd.DataFrame | None:
    """Carrega a tabela de aderência S/R salva. Retorna None se ainda não existir."""
    caminho = _caminho_aderencia_sr()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


def _caminho_padroes_comportamentais() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_padroes_comportamentais.parquet"


def salvar_padroes_comportamentais(df: pd.DataFrame):
    """
    Salva a tabela de padrões comportamentais detectados (revenge trading/
    tilt cascade) — tabela separada da principal, mesmo motivo de
    `salvar_reconciliacao`/`salvar_aderencia_sr`: recomputável a qualquer
    momento, não histórico imutável do próprio trade.
    """
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_padroes_comportamentais(), index=False)
    print(f"[persistencia] padrões comportamentais: {len(df)} linhas salvas em {_caminho_padroes_comportamentais()}")


def carregar_padroes_comportamentais() -> pd.DataFrame | None:
    """Carrega a tabela de padrões comportamentais salva. Retorna None se ainda não existir."""
    caminho = _caminho_padroes_comportamentais()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


# -----------------------------------------------------------------------------
# ESTRATÉGIAS / PRESETS R:R / TAGS SETUP-ERRO (cadastro editável, aba
# "Estratégias" — feedback diário.txt, 2026-07-11) — ao contrário das tabelas
# acima (recomputáveis a qualquer momento), estas são editadas diretamente
# pelo usuário (CRUD), então levam trava própria (mesmo padrão de
# `lock_diario`) — escrita disparada por clique, não por um ciclo automático,
# então quem chama deve mostrar erro/retry visível se ocupada. Uma trava só
# cobre as 3 tabelas (mesma tela "Estratégias", nunca editadas
# concorrentemente por dois escritores).

def _caminho_estrategias() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_estrategias.parquet"


def _caminho_rr_presets() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_rr_presets.parquet"


def _caminho_tags_setup_erro() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_tags_setup_erro.parquet"


def _caminho_tags_emocionais() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_tags_emocionais.parquet"


def _caminho_estrategias_lock() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / '.estrategias.lock'


@contextmanager
def lock_estrategias():
    """Mesmo mecanismo de `lock_diario()` (criação atômica via O_EXCL) —
    cobre as 3 tabelas de cadastro (estratégias, presets R:R, tags de
    setup/erro), editadas na mesma tela."""
    caminho = _caminho_estrategias_lock()
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


def salvar_estrategias(df: pd.DataFrame):
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_estrategias(), index=False)


def carregar_estrategias() -> pd.DataFrame | None:
    caminho = _caminho_estrategias()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


def salvar_rr_presets(df: pd.DataFrame):
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_rr_presets(), index=False)


def carregar_rr_presets() -> pd.DataFrame | None:
    caminho = _caminho_rr_presets()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


def salvar_tags_setup_erro(df: pd.DataFrame):
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_tags_setup_erro(), index=False)


def carregar_tags_setup_erro() -> pd.DataFrame | None:
    caminho = _caminho_tags_setup_erro()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


def salvar_tags_emocionais(df: pd.DataFrame):
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_tags_emocionais(), index=False)


def carregar_tags_emocionais() -> pd.DataFrame | None:
    caminho = _caminho_tags_emocionais()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


# -----------------------------------------------------------------------------
# POSIÇÕES ABERTAS (rascunho ao vivo do Diário) — ver plano em
# C:\Users\jesue\.claude\plans\analise-o-bloco-de-glimmering-marble.md
# -----------------------------------------------------------------------------
# Tabela SEPARADA de `diario_trades.parquet` (que continua append-only/
# imutável) — aqui uma linha = 1 posição atualmente aberta ou recém-fechada
# aguardando confirmação, escrita tanto pelo dashboard principal (campos
# vindos do MT5, a cada ciclo de sincronização) quanto pelo processo do
# Diário (notas/tags/imagens do usuário, enquanto ele acompanha a operação)
# — dois escritores diferentes, por isso a trava própria e a escrita
# atômica (`_salvar_parquet_atomico`) desde o primeiro dia, diferente de
# `salvar_diario` (que hoje tem um único escritor por vez).

def _caminho_posicoes_abertas() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "posicoes_abertas.parquet"


def _caminho_posicoes_abertas_lock() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / '.posicoes_abertas.lock'


@contextmanager
def lock_posicoes_abertas(tentativas: int = 1, espera_segundos: float = 0.15):
    """
    Mesmo mecanismo de `lock_atualizacao()`/`lock_diario()` (criação atômica
    via O_EXCL). Uso: `with persistencia.lock_posicoes_abertas() as adquirida:
    if adquirida: ...`. O sync automático (dashboard principal, a cada poucos
    segundos) deve pular o ciclo em silêncio se ocupado — usa o default
    (`tentativas=1`, sem espera), igual de sempre.

    `tentativas`/`espera_segundos`: ações do usuário (salvar campo, colar
    print, finalizar) devem pedir algumas tentativas com espera curta antes
    de desistir. Achado real 2026-07-20: o auto-save contínuo (cada campo
    salva sozinho, ver `atualizar_rascunho`) aumentou muito a frequência de
    disputa por esta trava contra o ciclo de sync (que roda a cada poucos
    segundos) — sem nenhuma espera, isso virava "⚠ Ocupado — tente de novo"
    com frequência real, inclusive no clique de "Finalizar e gravar no
    Diário" (que parecia "não fazer nada" — na verdade estava sendo
    recusado na hora por essa disputa)."""
    caminho = _caminho_posicoes_abertas_lock()
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

    fd = None
    for tentativa in range(max(1, tentativas)):
        try:
            fd = os.open(caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if tentativa + 1 >= max(1, tentativas):
                yield False
                return
            time.sleep(espera_segundos)
    os.close(fd)

    try:
        yield True
    finally:
        try:
            caminho.unlink()
        except FileNotFoundError:
            pass


def salvar_posicoes_abertas(df: pd.DataFrame):
    """Salva a tabela de posições abertas/rascunhos — escrita atômica
    (dois escritores possíveis, ver comentário da seção)."""
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    _salvar_parquet_atomico(_caminho_posicoes_abertas(), df)


def carregar_posicoes_abertas() -> pd.DataFrame | None:
    """Carrega a tabela de posições abertas/rascunhos. Retorna None se
    ainda não existir (nenhuma posição foi sincronizada ainda)."""
    caminho = _caminho_posicoes_abertas()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


# -----------------------------------------------------------------------------
# TICKETS FINALIZADOS — "tumbas" de tickets MT5 que já viraram trade
# definitivo no Diário (bug real 2026-07-16: um par de hedge pode ser
# finalizado no Diário mas continuar aparecendo como ABERTO no MT5 por 1-2h
# até o BTG cruzar as 2 pernas via DEAL_ENTRY_OUT_BY — sem essa lista,
# `sincronizar_posicoes_abertas` redescobria o ticket como posição "nova" e
# a mesma operação virava pendente de novo, podendo ser finalizada em
# DUPLICIDADE se o usuário clicasse Finalizar outra vez). Ver
# `diario_trade._marcar_tickets_finalizados`/`_tickets_ja_finalizados`.
# -----------------------------------------------------------------------------

def _caminho_tickets_finalizados() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "tickets_finalizados.parquet"


def salvar_tickets_finalizados(df: pd.DataFrame):
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    _salvar_parquet_atomico(_caminho_tickets_finalizados(), df)


def carregar_tickets_finalizados() -> pd.DataFrame | None:
    caminho = _caminho_tickets_finalizados()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


# -----------------------------------------------------------------------------
# ORDENS PENDENTES (ainda não executadas) — mesma seção "Posição" do Diário.
# Escritor único (o sync do dashboard principal, mesmo ciclo de
# `sincronizar_posicoes_abertas`) — sem lock dedicado, mesmo tratamento de
# `salvar_reconciliacao`/`salvar_aderencia_sr` (recomputada do zero a cada
# ciclo, nunca editada por múltiplos escritores). Escrita atômica mesmo
# assim, pra nunca expor um arquivo parcial a quem só lê (Diário).
# -----------------------------------------------------------------------------

def _caminho_ordens_pendentes() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "ordens_pendentes.parquet"


def salvar_ordens_pendentes(df: pd.DataFrame):
    """Salva a tabela de ordens pendentes (escrita atômica)."""
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    _salvar_parquet_atomico(_caminho_ordens_pendentes(), df)


def carregar_ordens_pendentes() -> pd.DataFrame | None:
    """Carrega a tabela de ordens pendentes. Retorna None se ainda não existir."""
    caminho = _caminho_ordens_pendentes()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


# -----------------------------------------------------------------------------
# CHECK-IN DIÁRIO / RODA DA VIDA (aba "Bem-Estar", Fase E — feedback
# diário.txt, 2026-07-11) — primeiras tabelas do projeto chaveadas por DATA
# em vez de `id_trade`/slug. Check-in é upsert (uma linha por dia, editável);
# Roda da Vida é append-only (cada registro é um retrato no tempo). As duas
# vivem na mesma tela, editadas por clique do usuário — uma trava só, mesmo
# padrão de `lock_estrategias` cobrindo várias tabelas da mesma aba.

def _caminho_checkin_diario() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_checkin.parquet"


def _caminho_roda_vida() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_roda_vida.parquet"


def _caminho_checkin_diario_lock() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / '.checkin_diario.lock'


@contextmanager
def lock_checkin_diario():
    """Mesmo mecanismo de `lock_diario()`/`lock_estrategias()` (criação
    atômica via O_EXCL) — cobre check-in e Roda da Vida, editados na mesma
    aba "Bem-Estar"."""
    caminho = _caminho_checkin_diario_lock()
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


def salvar_checkin_diario(df: pd.DataFrame):
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_checkin_diario(), index=False)


def carregar_checkin_diario() -> pd.DataFrame | None:
    caminho = _caminho_checkin_diario()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


def salvar_roda_vida(df: pd.DataFrame):
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_roda_vida(), index=False)


def carregar_roda_vida() -> pd.DataFrame | None:
    caminho = _caminho_roda_vida()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


# -----------------------------------------------------------------------------
# TRAÇADOS (níveis de S/R desenhados à mão no gráfico — aba/janela "Traçados",
# ver tracados.py) — dois escritores possíveis (o próprio dashboard, ação do
# usuário desenhando/arrastando/apagando; nunca simultâneo de fato, mas mesma
# cautela de `posicoes_abertas`) — trava própria + escrita atômica.
# -----------------------------------------------------------------------------

def _caminho_tracados() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "tracados.parquet"


def _caminho_tracados_lock() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / '.tracados.lock'


@contextmanager
def lock_tracados():
    """Mesmo mecanismo de `lock_posicoes_abertas()` (criação atômica via
    O_EXCL) — ação do usuário (criar/mover/apagar um traçado), deve
    mostrar erro/retry visível se ocupado, mas colisão real é rara (um
    usuário, uma aba por vez)."""
    caminho = _caminho_tracados_lock()
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


def salvar_tracados(df: pd.DataFrame):
    """Salva a tabela de traçados — escrita atômica (mesmo motivo de
    `salvar_posicoes_abertas`: nunca expor um arquivo parcial a um leitor
    concorrente)."""
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    _salvar_parquet_atomico(_caminho_tracados(), df)


def carregar_tracados() -> pd.DataFrame | None:
    """Carrega a tabela de traçados. Retorna None se ainda não existir
    (nenhum traçado foi criado ainda)."""
    caminho = _caminho_tracados()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)
