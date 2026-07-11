# =============================================================================
# gerenciador_processos.py — ciclo de vida dos processos filhos do Diário
# =============================================================================
# Os dois dashboards principais (dashboard.py/dashboard_98.py) embutem o
# Diário de Trade via <iframe> apontando pra um processo Dash próprio e
# separado (diario_app_classico.py/diario_app_98.py) — não por preferência
# de arquitetura, mas porque é a única forma de garantir isolamento real: um
# iframe é sempre um Window/Document/árvore de reconciliação separada, então
# nenhum componente do Diário (por mais numeroso que fique) pode competir
# pelo tempo de main-thread do dashboard principal. Ver
# `C:\Users\jesue\.claude\plans\t-tulo-super-quant-eager-knuth.md`.
#
# Esses processos filhos precisam nascer e morrer sozinhos, sem depender de
# o usuário lembrar de gerenciar um terceiro/quarto script: cada dashboard
# lança seu filho na subida (`garantir_processo_filho`, idempotente — não
# duplica se a porta já estiver ocupada por uma instância anterior), e cada
# filho se auto-encerra (`iniciar_monitor_processo_pai`) quando detecta que
# o pai que o originou não responde mais — não um sinal de desligamento
# gracioso (os processos deste projeto até agora só foram parados via
# taskkill/crash, nunca um shutdown limpo), então o mecanismo é polling,
# não um hook de saída do pai.
# =============================================================================

import os
import socket
import subprocess
import sys
import threading
import time


def porta_ocupada(porta: int, host: str = '127.0.0.1') -> bool:
    """True se já existe algo escutando na porta — nosso próprio processo
    filho de uma execução anterior, ou uma instância já lançada por outro
    dashboard que tenha subido primeiro."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, porta)) == 0


def garantir_processo_filho(porta: int, script: str, cwd: str) -> None:
    """Lança `script` (outro processo Python, mesmo interpretador) em
    background se a porta dele ainda não estiver ocupada. Idempotente: se
    já tem algo respondendo ali (nossa própria instância anterior ainda
    viva, ou o outro dashboard já lançou), não lança de novo — evita dois
    processos competindo pela mesma porta."""
    if porta_ocupada(porta):
        print(f"[gerenciador_processos] porta {porta} já ocupada — não lança {script} de novo.")
        return
    flags = 0
    if os.name == 'nt':
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        [sys.executable, script],
        cwd=cwd,
        creationflags=flags,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )
    print(f"[gerenciador_processos] lançado {script} (porta {porta}).")


def iniciar_monitor_processo_pai(porta_pai: int, intervalo: int = 20, tentativas_falha: int = 3) -> None:
    """Inicia (em thread daemon) um heartbeat que encerra ESTE processo
    (`os._exit`) quando `porta_pai` para de responder por `tentativas_falha`
    checagens seguidas (~intervalo*tentativas_falha segundos de graça, pra
    não se matar num restart rápido do pai). Chamar isso de dentro do
    processo FILHO (ex. diario_app_98.py), nunca do pai."""
    def _loop():
        falhas = 0
        while True:
            time.sleep(intervalo)
            if porta_ocupada(porta_pai):
                falhas = 0
            else:
                falhas += 1
                if falhas >= tentativas_falha:
                    print(f"[gerenciador_processos] processo pai (porta {porta_pai}) não "
                          f"responde há ~{intervalo * tentativas_falha}s — encerrando processo.")
                    os._exit(0)
    threading.Thread(target=_loop, daemon=True).start()
