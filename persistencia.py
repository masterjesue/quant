# =============================================================================
# persistencia.py — Salvar e carregar a base processada em Parquet
# =============================================================================
# Responsabilidade: única fonte de leitura/escrita em disco. Tanto o merge
# pesado (setup inicial) quanto a atualização leve (dia a dia) passam por
# aqui — nunca se toca em arquivo Parquet fora deste módulo.
# =============================================================================

import os
import json
import re
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
    _limpar_temporarios_orfaos(caminho.parent)
    caminho_tmp = caminho.with_name(f"{caminho.stem}.tmp{os.getpid()}{caminho.suffix}")
    df.to_parquet(caminho_tmp, index=False)
    os.replace(caminho_tmp, caminho)


_TEMPORARIOS_JA_LIMPOS = False


def _limpar_temporarios_orfaos(pasta: Path):
    """Remove `*.tmp<pid>.parquet` deixados para trás por um processo que
    morreu ENTRE o `to_parquet` e o `os.replace` — nesse intervalo o
    temporário existe e nada o apaga.

    Achado real 2026-07-28: 7 órfãos somando 17 MB acumulados em
    `data/base_processada/`, a maioria de dashboards encerrados à força
    durante a sessão de correções. Não quebram nada, mas crescem para sempre.

    Só apaga o que comprovadamente NÃO tem dono: o PID embutido no nome não
    corresponde a nenhum processo vivo. Idade não serve de critério sozinha —
    uma gravação concorrente legítima e demorada (`run_backtest --features`
    sobre a base inteira) teria seu temporário apagado no meio do caminho,
    trocando um desperdício de disco por corrupção de dado.

    Roda uma vez por processo (a primeira gravação) — é varredura de
    diretório, não faz sentido repetir a cada save.
    """
    global _TEMPORARIOS_JA_LIMPOS
    if _TEMPORARIOS_JA_LIMPOS:
        return
    _TEMPORARIOS_JA_LIMPOS = True
    try:
        for tmp in pasta.glob("*.tmp*.parquet"):
            achado = re.search(r"\.tmp(\d+)\.parquet$", tmp.name)
            if not achado:
                continue
            pid = int(achado.group(1))
            if pid == os.getpid() or _processo_vivo(pid):
                continue
            try:
                tmp.unlink()
            except OSError:
                pass  # em uso por outro processo — deixa pro próximo ciclo
    except OSError:
        pass  # pasta inacessível: limpeza é oportunista, nunca impede a escrita


def _processo_vivo(pid: int) -> bool:
    """Existe um processo com este PID?

    Em caso de dúvida devolve True: o custo de manter um órfão é disco, o de
    apagar um temporário vivo é dado corrompido.

    `os.kill(pid, 0)` — o truque POSIX de sempre — NÃO serve no Windows: pra
    PID inexistente ele levanta `OSError [WinError 87] Parâmetro incorreto`, e
    não `ProcessLookupError`. Tratar isso como "não sei" faria a limpeza nunca
    apagar nada aqui (este projeto roda em Windows). Daí o caminho via
    `OpenProcess` da API do Windows.
    """
    if pid <= 0:
        return True
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERRO_ACESSO_NEGADO = 5      # existe, mas sem permissão de consultar
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            k32.CloseHandle(handle)
            return True
        return k32.GetLastError() == ERRO_ACESSO_NEGADO
    try:
        os.kill(pid, 0)          # sinal 0 = só testa a existência
    except ProcessLookupError:
        return False
    except PermissionError:
        return True              # existe, mas é de outro usuário
    except OSError:
        return True
    return True


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
def lock_diario(tentativas: int = 1, espera_segundos: float = 0.15):
    """
    Mesmo mecanismo de `lock_atualizacao()` (criação atômica via O_EXCL),
    trava separada. Uso: `with persistencia.lock_diario() as adquirida: if
    adquirida: ...` — se ocupada, quem chama decide como reagir (aqui, ao
    contrário do update de candle, a escrita foi disparada por um clique do
    usuário, então o chamador deve mostrar um erro/retry visível, não pular
    em silêncio).

    `tentativas`/`espera_segundos`: mesma adição que `lock_posicoes_abertas`
    ganhou em 2026-07-20 (ver lá o achado). Escritas disparadas por clique
    (`atualizar_trade`, pela edição no Histórico) pedem algumas tentativas
    com espera curta antes de desistir; o default segue `tentativas=1` pra
    não mudar o comportamento de nenhum chamador existente.
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


def _caminho_aderencia_tracados() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_aderencia_tracados.parquet"


def salvar_aderencia_tracados(df: pd.DataFrame):
    """
    Salva a tabela de aderência aos traçados (nível marcado à mão × traçados
    ativos no snapshot do motor) — tabela separada da principal, mesmo
    motivo de `salvar_reconciliacao`: recomputável a qualquer momento, não
    histórico imutável do próprio trade. Substitui `salvar_aderencia_sr`
    (2026-07-21, Fase 3.2 — motor automático de S/R desativado)."""
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_aderencia_tracados(), index=False)
    print(f"[persistencia] aderência aos traçados: {len(df)} linhas salvas em {_caminho_aderencia_tracados()}")


def carregar_aderencia_tracados() -> pd.DataFrame | None:
    """Carrega a tabela de aderência aos traçados salva. Retorna None se
    ainda não existir."""
    caminho = _caminho_aderencia_tracados()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


def _caminho_log_recalibracao() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "aprendizagem_log_recalibracao.parquet"


def salvar_log_recalibracao(entrada: dict):
    """Acrescenta 1 registro ao log de recalibração do Modo Aprendizagem
    (`aprendizagem.rodar_recalibracao`) — APPEND-ONLY, nunca sobrescreve:
    diferente de `salvar_aderencia_tracados`/`salvar_reconciliacao`
    (caches recomputáveis, sempre a versão mais recente vence), este é uma
    trilha de AUDITORIA — cada rodada precisa continuar visível depois,
    mesmo que a próxima rodada mude de opinião."""
    caminho = _caminho_log_recalibracao()
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    linha = pd.DataFrame([{
        'timestamp': entrada.get('timestamp'),
        'modo_aprendizagem_ativo': entrada.get('modo_aprendizagem_ativo'),
        'status': entrada.get('status'),
        'n_amostras': entrada.get('n_amostras'),
        'por_postura_json': json.dumps(entrada.get('por_postura', {}), ensure_ascii=False),
        'sugestoes_json': json.dumps(entrada.get('sugestoes', []), ensure_ascii=False),
    }])
    if caminho.exists():
        existente = pd.read_parquet(caminho)
        linha = pd.concat([existente, linha], ignore_index=True)
    _salvar_parquet_atomico(caminho, linha)
    print(f"[persistencia] log de recalibração: +1 linha ({len(linha)} total) em {caminho}")


def carregar_log_recalibracao() -> pd.DataFrame | None:
    """Carrega o log de recalibração (histórico completo, todas as
    rodadas). Retorna None se `rodar_recalibracao` nunca rodou ainda."""
    caminho = _caminho_log_recalibracao()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


# -----------------------------------------------------------------------------
# SUGESTÕES ESTRUTURAIS (Modo Aprendizagem, Fase 3.4, 2026-07-21) — qualquer
# achado que NÃO seja "mover um número dentro de um limite" (ver
# cfg.AJUSTE_FINO_ALLOWLIST) vira uma entrada aqui, pra eu (Claude Code)
# implementar manualmente numa sessão futura — nunca virou código sozinho.
# Mesma trava+escrita atômica de `tracados` (tabela pequena, CRUD real —
# criar/listar/mudar status —, não um cache recomputável nem um log
# append-only puro).
# -----------------------------------------------------------------------------

def _caminho_sugestoes_estruturais() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "aprendizagem_sugestoes_estruturais.parquet"


def _caminho_sugestoes_estruturais_lock() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / '.sugestoes_estruturais.lock'


@contextmanager
def lock_sugestoes_estruturais():
    """Mesmo mecanismo de `lock_tracados()` — colisão real é rara (uma
    sugestão nova por vez, criada por mim durante uma sessão, ou o status
    mudado manualmente por Jesué)."""
    caminho = _caminho_sugestoes_estruturais_lock()
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


def salvar_sugestoes_estruturais(df: pd.DataFrame):
    """Salva a tabela de sugestões estruturais inteira — escrita atômica
    (mesmo motivo de `salvar_tracados`: nunca expor um arquivo parcial a
    um leitor concorrente, ex. o painel de Configurações lendo enquanto
    uma sugestão nova é adicionada)."""
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    _salvar_parquet_atomico(_caminho_sugestoes_estruturais(), df)


def carregar_sugestoes_estruturais() -> pd.DataFrame | None:
    """Carrega a tabela de sugestões estruturais. Retorna None se nenhuma
    foi registrada ainda."""
    caminho = _caminho_sugestoes_estruturais()
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
# ESTRATÉGIAS / CHECKLIST PRÉ-TRADE / GERENCIAMENTO DE RISCO / TAGS
# SETUP-ERRO / TAGS EMOCIONAIS (cadastro editável, aba "Estratégias" —
# feedback diário.txt, 2026-07-11) — ao contrário das tabelas acima
# (recomputáveis a qualquer momento), estas são editadas diretamente pelo
# usuário (CRUD), então levam trava própria (mesmo padrão de `lock_diario`)
# — escrita disparada por clique, não por um ciclo automático, então quem
# chama deve mostrar erro/retry visível se ocupada. Uma trava só cobre as 5
# tabelas (mesma tela "Estratégias", nunca editadas concorrentemente por
# dois escritores). Os antigos "Presets R:R" (`diario_rr_presets.parquet`)
# foram substituídos pelo cadastro de Gerenciamento de Risco abaixo — o
# arquivo antigo fica intocado em disco, dado superado.

def _caminho_estrategias() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_estrategias.parquet"


def _caminho_checklist_pretrade() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_checklist_pretrade.parquet"


def _caminho_gerenciamento_risco() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_gerenciamento_risco.parquet"


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


def salvar_checklist_pretrade(df: pd.DataFrame):
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_checklist_pretrade(), index=False)


def carregar_checklist_pretrade() -> pd.DataFrame | None:
    caminho = _caminho_checklist_pretrade()
    if not caminho.exists():
        return None
    return pd.read_parquet(caminho)


def salvar_gerenciamento_risco(df: pd.DataFrame):
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_gerenciamento_risco(), index=False)


def carregar_gerenciamento_risco() -> pd.DataFrame | None:
    caminho = _caminho_gerenciamento_risco()
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
# STATUS DE CONEXÃO MT5 — cabeçalho de conexão em Posição (Playbook,
# 2026-08-03). Quem fala com o MT5 ao vivo é SÓ a thread de fundo do
# dashboard.py (processo PAI, ver `carregar_bases`) — o processo FILHO
# (`diario_app_classico.py`, onde Posição realmente vive) nunca conecta no
# MT5 diretamente, mesmo padrão já usado por `posicoes_abertas.parquet`
# (pai escreve, filho só lê). 1 linha só, sem trava dedicada: um único
# escritor (a mesma thread que já escreve `contrato_win`/`contrato_wdo` em
# memória), leituras são só `pd.read_parquet` tolerante a arquivo ausente.
def _caminho_status_conexao() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "status_conexao_mt5.parquet"


def salvar_status_conexao(dados: dict):
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([dados])
    _salvar_parquet_atomico(_caminho_status_conexao(), df)


def carregar_status_conexao() -> dict | None:
    caminho = _caminho_status_conexao()
    if not caminho.exists():
        return None
    try:
        df = pd.read_parquet(caminho)
    except Exception:
        return None
    return df.iloc[0].to_dict() if not df.empty else None


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
# TICKETS IGNORADOS — operações reais do MT5 que o usuário decidiu que NÃO
# entram no Diário (2026-07-27). Diferente de `tickets_finalizados`: lá o
# ticket virou um trade; aqui ele nunca vai virar, por decisão explícita.
#
# Por que precisa existir: a reconciliação lê o MT5 e reporta como pendente
# toda operação real sem trade correspondente. Se o usuário conscientemente
# não quer aquela operação no Diário (ex: o relatório de performance da
# corretora, filtrado por carteira, não a considera parte da estratégia), sem
# esta lista ela reaparece como PENDENTE a cada ciclo, pra sempre — e a única
# forma de calar era registrá-la, ou seja, aceitar o que se queria recusar.
# Ver `diario_trade.ignorar_pendencia`/`_tickets_ignorados`.
# -----------------------------------------------------------------------------

def _caminho_tickets_ignorados() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "tickets_ignorados.parquet"


def salvar_tickets_ignorados(df: pd.DataFrame):
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    _salvar_parquet_atomico(_caminho_tickets_ignorados(), df)


def carregar_tickets_ignorados() -> pd.DataFrame | None:
    caminho = _caminho_tickets_ignorados()
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


# -----------------------------------------------------------------------------
# CHECKLIST PRÉ-TRADE DIÁRIO (Playbook, 2026-08-02) — o "foi feito hoje?" do
# guarda-corpo de Posição. Mesmo formato de check-in: upsert por DATA, 1
# linha por dia. Trava própria (não reaproveita `lock_checkin_diario`) pra
# não gerar contenção entre saves de Bem-Estar e o guarda-corpo em Posição,
# que podem acontecer no mesmo instante em processos/abas diferentes.

def _caminho_checklist_pretrade_dia() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / "diario_checklist_pretrade_dia.parquet"


def _caminho_checklist_pretrade_dia_lock() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / '.checklist_pretrade_dia.lock'


@contextmanager
def lock_checklist_pretrade_dia():
    """Mesmo mecanismo de `lock_checkin_diario()` (criação atômica via O_EXCL)."""
    caminho = _caminho_checklist_pretrade_dia_lock()
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


def salvar_checklist_pretrade_dia(df: pd.DataFrame):
    cfg.PASTA_BASE_PROCESSADA.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_caminho_checklist_pretrade_dia(), index=False)


def carregar_checklist_pretrade_dia() -> pd.DataFrame | None:
    caminho = _caminho_checklist_pretrade_dia()
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
