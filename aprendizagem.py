# =============================================================================
# aprendizagem.py — Modo Aprendizagem: ajuste fino automático (Fase 3.3, 2026-07-21)
# =============================================================================
# Decisão de arquitetura (ver plano completo em memory/project_modo_
# aprendizagem.md): "Modo Aprendizagem" NÃO é uma IA que reescreve sua
# própria lógica sozinha — é um sistema de log + comparação sempre ativo,
# com um passo de ajuste fino BOUNDED (mover um número dentro de uma
# constante já nomeada, dentro de um limite seguro) que só tem permissão
# de ESCREVER quando `config_operacional.modo_aprendizagem_ativo()` é True.
#
# **Este módulo, hoje, NUNCA escreve em config_backtest.py.** Só calcula o
# que ajustaria e grava um log auditável (`persistencia.salvar_log_
# recalibracao`) — a escrita de verdade é o próximo incremento, depois que
# houver volume real de trades (avaliar_calibracao_postura já retorna
# 'dados_insuficientes' com 0 trades hoje, já que o snapshot com traçados
# só existe pra trades registrados a partir da Fase 3.2) e Jesué revisar as
# primeiras sugestões reais. Ver cfg.AJUSTE_FINO_ALLOWLIST pra que
# constantes são candidatas e com que limite de variação.
# =============================================================================

import uuid
from datetime import datetime

import pandas as pd

import config_backtest as cfg
import config_operacional
import diario_trade
import persistencia

# Fase 3.4 — sugestões ESTRUTURAIS (qualquer achado que não seja "mover um
# número dentro de um limite" — precisa de código novo/redesenho, nunca
# aplicado sozinho). Schema simples: quem registrou (`origem`), o que é,
# e um `status` de ciclo de vida (só leitura pelo painel de Configurações
# por enquanto — mudar o status é manual, direto no parquet ou numa
# sessão futura de Claude Code, não tem UI de edição ainda).
COLUNAS_SUGESTOES_ESTRUTURAIS = [
    "id_sugestao", "criado_em", "titulo", "detalhe", "origem", "status",
]
_STATUS_SUGESTAO_PADRAO = "pendente"

# Ordem de hierarquia esperada das posturas — FAVORECE deveria ter o
# melhor resultado real médio, EVITAR o pior. Usada só pra detectar
# INVERSÕES (evidência de limiar mal calibrado), não pra medir a força
# absoluta de nenhuma postura isolada.
_ORDEM_POSTURA = ['favorece_entrada', 'aguardar_confirmacao', 'sinal_fraco', 'evitar']


def avaliar_calibracao_postura(dias_atras: int | None = None) -> dict:
    """
    Cruza a POSTURA que o motor via no instante do trade (via
    `diario_trade.avaliar_aderencia_tracados`, que já casa
    `nivel_sr_referencia` com o traçado mais próximo do tipo certo) contra
    o resultado REAL do trade (`saldo_operacao_reais`) — a pergunta central
    do ajuste fino: "os limiares de postura (FAVORECE/AGUARDAR/FRACO/
    EVITAR — `cfg.LIMIAR_POSTURA_*`) realmente separam trade bom de trade
    ruim, ou estão fora de calibração?"

    Retorna sempre o mesmo formato:
    `{'status': 'dados_insuficientes'|'ok', 'n_amostras': int,
    'por_postura': {postura: {'n', 'taxa_acerto', 'saldo_medio'}}}`.
    `dados_insuficientes` se, somados, houver menos que o menor
    `amostra_minima` do allowlist relevante (`LIMIAR_POSTURA_*`).
    """
    aderencia = diario_trade.avaliar_aderencia_tracados(dias_atras=dias_atras)
    elegiveis = aderencia[aderencia['postura'].notna()] if not aderencia.empty else aderencia

    amostra_min = min(
        (v['amostra_minima'] for k, v in cfg.AJUSTE_FINO_ALLOWLIST.items() if k.startswith('LIMIAR_POSTURA_')),
        default=50,
    )
    if len(elegiveis) < amostra_min:
        return {'status': 'dados_insuficientes', 'n_amostras': int(len(elegiveis)), 'por_postura': {}}

    trades = diario_trade.listar_trades(is_simulated=False)
    saldo_por_trade = dict(zip(trades['id_trade'], trades['saldo_operacao_reais']))

    linhas = []
    for _, row in elegiveis.iterrows():
        saldo = saldo_por_trade.get(row['id_trade'])
        if saldo is None or pd.isna(saldo):
            continue
        linhas.append({'postura': row['postura'], 'saldo': float(saldo)})

    if not linhas:
        return {'status': 'dados_insuficientes', 'n_amostras': 0, 'por_postura': {}}

    df = pd.DataFrame(linhas)
    por_postura = {}
    for postura, grupo in df.groupby('postura'):
        por_postura[postura] = {
            'n': int(len(grupo)),
            'taxa_acerto': float((grupo['saldo'] > 0).mean()),
            'saldo_medio': float(grupo['saldo'].mean()),
        }
    return {'status': 'ok', 'n_amostras': int(len(df)), 'por_postura': por_postura}


def _detectar_inversoes(por_postura: dict) -> list:
    """Compara pares ADJACENTES na hierarquia esperada de postura — se a
    postura 'pior' (mais restritiva) teve saldo médio melhor que a
    'melhor' (menos restritiva), com amostra suficiente nas DUAS, é
    evidência real de limiar mal calibrado. Só compara pares onde AMBOS
    os lados batem a amostra mínima do allowlist — nunca conclui nada de
    um lado só."""
    amostra_min = min(
        (v['amostra_minima'] for k, v in cfg.AJUSTE_FINO_ALLOWLIST.items() if k.startswith('LIMIAR_POSTURA_')),
        default=50,
    )
    sugestoes = []
    for melhor, pior in zip(_ORDEM_POSTURA, _ORDEM_POSTURA[1:]):
        info_melhor, info_pior = por_postura.get(melhor), por_postura.get(pior)
        if not info_melhor or not info_pior:
            continue
        if info_melhor['n'] < amostra_min or info_pior['n'] < amostra_min:
            continue
        if info_pior['saldo_medio'] > info_melhor['saldo_medio']:
            sugestoes.append({
                'tipo': 'postura_invertida',
                'detalhe': (
                    f"'{pior}' teve saldo médio melhor que '{melhor}' "
                    f"(R$ {info_pior['saldo_medio']:.2f} em {info_pior['n']} trades vs. "
                    f"R$ {info_melhor['saldo_medio']:.2f} em {info_melhor['n']} trades) — "
                    f"considerar revisar LIMIAR_POSTURA_* (dentro do limite de "
                    f"±{cfg.AJUSTE_FINO_ALLOWLIST.get('LIMIAR_POSTURA_FAVORECE', {}).get('limite_variacao', 0.2)*100:.0f}%)."
                ),
            })
    return sugestoes


def rodar_recalibracao() -> dict:
    """
    Ponto de entrada do job de ajuste fino (Fase 3.3 do Modo Aprendizagem).
    Roda a comparação (`avaliar_calibracao_postura`) INDEPENDENTE do modo
    (log/comparação sempre ativo, mesma doutrina do resto do Modo
    Aprendizagem) — só registra no resultado se o Modo Aprendizagem está
    ligado, informativo pra quem for decidir se algum dia vale automatizar
    a ESCRITA condicionada a isso.

    **Nunca escreve em config_backtest.py nesta versão** — só grava um log
    auditável (`persistencia.salvar_log_recalibracao`, append-only) com o
    que sugeriria. Chamado manualmente (botão "Rodar recalibração" em
    Configurações, nas duas telas) — ainda não há agenda automática (não
    faz sentido rodar em intervalo curto com zero dado hoje; uma vez que
    trades reais acumularem histórico de traçados, um Interval diário é o
    próximo incremento natural, não construído agora).
    """
    calibracao = avaliar_calibracao_postura()
    sugestoes = _detectar_inversoes(calibracao['por_postura']) if calibracao['status'] == 'ok' else []
    resultado = {
        'timestamp': datetime.now().isoformat(),
        'modo_aprendizagem_ativo': config_operacional.modo_aprendizagem_ativo(),
        'status': calibracao['status'],
        'n_amostras': calibracao['n_amostras'],
        'por_postura': calibracao['por_postura'],
        'sugestoes': sugestoes,
    }
    persistencia.salvar_log_recalibracao(resultado)
    return resultado


def registrar_sugestao_estrutural(titulo: str, detalhe: str, origem: str = "manual") -> str | None:
    """
    Adiciona uma sugestão de ajuste ESTRUTURAL (Fase 3.4) — qualquer achado
    que NÃO seja "mover um número dentro de um limite já existente" (isso
    é `rodar_recalibracao`) — precisa de código novo/redesenho, sempre
    implementado manualmente numa sessão futura, nunca vira código
    sozinho. `origem` distingue quem registrou (`'manual'` = eu, Claude
    Code, notei durante uma investigação; outros valores reservados pra
    quando algum job automático puder propor uma estrutural também, não
    construído ainda). Retorna o `id_sugestao` gerado, ou `None` se a
    trava estava ocupada.
    """
    with persistencia.lock_sugestoes_estruturais() as adquirida:
        if not adquirida:
            return None
        df = persistencia.carregar_sugestoes_estruturais()
        df = df if df is not None else pd.DataFrame(columns=COLUNAS_SUGESTOES_ESTRUTURAIS)
        id_sugestao = uuid.uuid4().hex
        nova = pd.DataFrame([{
            "id_sugestao": id_sugestao, "criado_em": datetime.now(),
            "titulo": titulo, "detalhe": detalhe, "origem": origem,
            "status": _STATUS_SUGESTAO_PADRAO,
        }])
        df = pd.concat([df, nova], ignore_index=True)
        persistencia.salvar_sugestoes_estruturais(df)
        return id_sugestao


def listar_sugestoes_estruturais(status: str | None = "pendente") -> pd.DataFrame:
    """Lista sugestões estruturais — por padrão só as `pendente` (painel
    de Configurações não quer ver o que já foi implementado/descartado).
    `status=None` devolve todas."""
    df = persistencia.carregar_sugestoes_estruturais()
    if df is None:
        return pd.DataFrame(columns=COLUNAS_SUGESTOES_ESTRUTURAIS)
    if status is not None:
        df = df[df["status"] == status]
    return df.sort_values("criado_em", ascending=False).reset_index(drop=True)
