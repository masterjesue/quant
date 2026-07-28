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
from pathlib import Path
from contextlib import contextmanager

import config_backtest as cfg

_LOCK_STALE_SEGUNDOS = 120

_PADRAO = {
    'modo_aprendizagem': False,
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
    return {**_PADRAO, **dados}


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
