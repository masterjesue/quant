# =============================================================================
# config_operacional.py — configurações operacionais persistidas (2026-07-21)
# =============================================================================
# Diferente do switch de tema (dcc.Store(storage_type='local'), puramente
# client-side/cosmético): "Modo Aprendizagem" precisa ser lido por BACKEND de
# verdade (job de recalibração, snapshot diário) e por processos SEPARADOS
# (dashboard.py, dashboard_98.py, os 2 processos do Diário) — precisa viver
# no servidor, não no browser de uma aba específica.
#
# Arquivo pequeno (JSON, não parquet — é um dict de flags, não dado tabular),
# mesmo padrão de lock+escrita atômica de persistencia.py (criação atômica
# via O_EXCL pra trava, arquivo temporário + os.replace pra escrita) — ver
# persistencia.lock_tracados()/_salvar_parquet_atomico para o precedente
# exato que este módulo espelha.
# =============================================================================

import json
import os
import time
from datetime import date
from pathlib import Path
from contextlib import contextmanager

import config_backtest as cfg

_LOCK_STALE_SEGUNDOS = 120

# Toggles de notificação (item 1D do upgrade de 2026-08-04) — cada chave é
# um tipo de evento do sino no menu lateral, liga/desliga em Configurações.
_NOTIFICACOES_PADRAO = {
    'posicao_aberta': True,
    'pendencia_diario': True,
    'pendencia_coaching': True,
    'sinal_quantlab': True,
}

_PADRAO = {
    'modo_aprendizagem': False,
    'notificacoes': _NOTIFICACOES_PADRAO,
    # Carimbo gravado 1x (ver `garantir_primeiro_uso`) — só alimenta o
    # tier visual do rodapé do menu (`diario_trade.nivel_conta`), nunca
    # crítico pro resto do sistema.
    'primeiro_uso': None,
}


def _caminho() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / 'config_operacional.json'


def _caminho_lock() -> Path:
    return cfg.PASTA_BASE_PROCESSADA / '.config_operacional.lock'


@contextmanager
def _lock():
    """Mesmo mecanismo de `persistencia.lock_tracados()` — colisão real é
    rara (1 usuário, ação manual em Configurações, ou o job de
    recalibração rodando em segundo plano uma vez por dia)."""
    caminho = _caminho_lock()
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


def carregar() -> dict:
    """Config operacional atual, com defaults pras chaves ausentes (arquivo
    pode não existir ainda, ou vir de uma versão anterior sem alguma chave
    nova — nunca quebra por schema desatualizado)."""
    caminho = _caminho()
    if not caminho.exists():
        return dict(_PADRAO)
    try:
        with open(caminho, 'r', encoding='utf-8') as f:
            dados = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(_PADRAO)
    mesclado = {**_PADRAO, **dados}
    # Mescla rasa dedicada pra 'notificacoes' — sem isso, um arquivo salvo
    # antes de um tipo de evento novo ser adicionado perderia esse toggle
    # (sobrescrito pelo dict antigo inteiro, em vez de só as chaves que já
    # existiam nele).
    mesclado['notificacoes'] = {**_NOTIFICACOES_PADRAO, **dados.get('notificacoes', {})}
    return mesclado


def _salvar(dados: dict):
    caminho = _caminho()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho_tmp = caminho.with_name(f'{caminho.stem}.tmp{os.getpid()}{caminho.suffix}')
    with open(caminho_tmp, 'w', encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(caminho_tmp, caminho)


def modo_aprendizagem_ativo() -> bool:
    """Leitura rápida — usada em qualquer hot-path (snapshot diário, job de
    recalibração) sem precisar montar o dict inteiro toda vez no chamador."""
    return bool(carregar().get('modo_aprendizagem', False))


def definir_modo_aprendizagem(ativo: bool) -> bool:
    """Liga/desliga o Modo Aprendizagem. Retorna False se a trava estava
    ocupada (chamador decide o que fazer — no dashboard, só mantém o
    estado visual como estava e deixa o usuário clicar de novo)."""
    with _lock() as adquirida:
        if not adquirida:
            return False
        dados = carregar()
        dados['modo_aprendizagem'] = bool(ativo)
        _salvar(dados)
        return True


def notificacoes_config() -> dict:
    """Toggles atuais de notificação — leitura rápida pro sino do menu e
    pra tela de Configurações."""
    return dict(carregar()['notificacoes'])


def definir_notificacao(chave: str, ativo: bool) -> bool:
    """Liga/desliga 1 tipo de notificação (`chave` em `_NOTIFICACOES_
    PADRAO`). Mesmo contrato de retorno de `definir_modo_aprendizagem`."""
    with _lock() as adquirida:
        if not adquirida:
            return False
        dados = carregar()
        notificacoes = dict(dados['notificacoes'])
        notificacoes[chave] = bool(ativo)
        dados['notificacoes'] = notificacoes
        _salvar(dados)
        return True


def garantir_primeiro_uso() -> str:
    """Data (ISO) do primeiro uso registrado — grava a data de hoje na 1ª
    chamada e nunca mais sobrescreve. Só alimenta o tier visual do rodapé
    do menu (`diario_trade.nivel_conta`), nunca é dado crítico — se a
    trava estiver ocupada, devolve a data de hoje mesmo sem persistir
    (o próximo processo a chamar tenta gravar de novo)."""
    dados = carregar()
    if dados.get('primeiro_uso'):
        return dados['primeiro_uso']
    hoje = date.today().isoformat()
    with _lock() as adquirida:
        if not adquirida:
            return hoje
        dados = carregar()
        if not dados.get('primeiro_uso'):
            dados['primeiro_uso'] = hoje
            _salvar(dados)
        return dados['primeiro_uso']
