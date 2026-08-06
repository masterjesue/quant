# =============================================================================
# backtesting_tick.py — Motor de análise do protótipo de backtesting tick a tick
# =============================================================================
# Ver spec_prototipo_backtesting_tick.md pro plano completo. Funções PURAS
# sobre tick+trade já carregados em memória — nenhuma chamada MT5 aqui
# dentro. Quem orquestra I/O:
#   - Backfill de tick: run_backtest.py --backfill-ticks (persistencia.py)
#   - Alinhamento trade->tick (deals reais): mt5_loader.buscar_execucoes_periodo
#     + mt5_loader.agrupar_posicoes, chamados só por `alinhar_trade_tick` abaixo
#     (é a única função deste módulo que faz I/O — busca os deals exatos do
#     trade, não tick em si).
#
# Limitação conhecida, sem solução retroativa: não há histórico de QUANDO um
# stop foi movido pra breakeven nos trades já fechados — só o estado final
# (ver spec). Não existe função de "timing de breakeven" aqui por isso; é
# melhoria prospectiva separada (estender notas_cronologicas), fora de escopo.
# =============================================================================

import numpy as np
import pandas as pd

import config_backtest as cfg
import persistencia
import mt5_loader
import diario_trade


# -----------------------------------------------------------------------------
# ALINHAMENTO TRADE -> TICK (fonte certa de timestamp/preço exato)
# -----------------------------------------------------------------------------

def alinhar_trade_tick(id_trade: int) -> dict | None:
    """
    Timestamp e preço de entrada/saída de um trade, priorizando SEMPRE o
    dado exato dos deals reais do MT5 sobre o campo aproximado do Diário —
    mas só onde o deal exato realmente existe (ver `exit_fonte` no retorno).

    Achado empírico 2026-08-03, não documentado antes em lugar nenhum do
    repo: nesta conta HEDGING, um stop/alvo disparando nunca grava um deal
    de saída (`entry` OUT/OUT_BY) na própria posição — ele abre uma posição
    OPOSTA nova (confirmado em `history_deals_get` real: tanto um trade de
    1 ticket quanto um par de hedge de 2 tickets só têm deals `entry=0`
    (IN), nenhum OUT, em nenhuma das pernas). Por isso:
      - Entrada: sempre exata — vem do deal IN da perna que abriu primeiro.
      - Saída, par de hedge (2 tickets em `tickets_finalizados`): exata —
        a perna aberta DEPOIS é o proxy de saída da conta hedging; o preço/
        hora do PRÓPRIO deal IN dela É o preço/hora de saída do trade
        líquido (validado contra o trade #233: bateu exato com
        preco_saida/datetime_saida do Diário).
      - Saída, ticket único: a posição oposta que efetivamente fechou o
        trade não fica registrada em `tickets_finalizados` (só pares
        explicitamente casados por `finalizar_par_hedge` ficam) — sem
        heurística confiável de achar o deal certo por preço/hora, cai
        pro valor já gravado no Diário (`exit_fonte='diario_aproximado'`;
        pode ser o `saida_estimada=True` mencionado na spec — o campo não
        sobrevive em `COLUNAS_DIARIO`, então não dá pra saber qual caso é).

    Retorna None se o trade não tiver nenhum ticket MT5 vinculado (trade
    antigo/importado, sem `tickets_finalizados` — ver "cobertura desigual
    da base" na spec) ou se os deals não forem mais encontráveis no MT5.
    """
    tickets_df = persistencia.carregar_tickets_finalizados()
    if tickets_df is None or tickets_df.empty:
        return None
    tickets = tickets_df.loc[tickets_df['id_trade'] == id_trade, 'id_posicao'].astype(int).tolist()
    if not tickets:
        return None

    trades = diario_trade.listar_trades()
    linha_df = trades.loc[trades['id_trade'] == id_trade]
    if linha_df.empty or pd.isna(linha_df.iloc[0]['data_op']):
        return None
    linha = linha_df.iloc[0]
    data_op = linha['data_op']

    # Janela generosa (dia anterior a 2 dias depois) — a perna que fecha um
    # par de hedge pode levar 1-2h pra abrir, e a saída pode escorregar pro
    # dia seguinte perto da meia-noite.
    inicio = (pd.Timestamp(data_op) - pd.Timedelta(days=1)).to_pydatetime()
    fim = (pd.Timestamp(data_op) + pd.Timedelta(days=2)).to_pydatetime()

    deals = mt5_loader.buscar_execucoes_periodo(inicio, fim)
    if deals is None or deals.empty:
        return None

    posicoes = mt5_loader.agrupar_posicoes(deals)
    posicoes = posicoes[posicoes['position_id'].isin(tickets)].sort_values('datetime_entrada').reset_index(drop=True)
    if posicoes.empty:
        return None

    perna_entrada = posicoes.iloc[0]
    preco_entrada = float(perna_entrada['preco_entrada'])
    datetime_entrada = pd.Timestamp(perna_entrada['datetime_entrada'])

    if len(posicoes) >= 2:
        perna_saida = posicoes.iloc[-1]
        preco_saida = float(perna_saida['preco_entrada'])
        datetime_saida = pd.Timestamp(perna_saida['datetime_entrada'])
        exit_fonte = 'mt5_exato'
    elif pd.notna(perna_entrada['datetime_saida']):
        # Caso raro: a própria perna tem mesmo um OUT real gravado.
        preco_saida = float(perna_entrada['preco_saida'])
        datetime_saida = pd.Timestamp(perna_entrada['datetime_saida'])
        exit_fonte = 'mt5_exato'
    else:
        preco_saida = float(linha['preco_saida']) if pd.notna(linha['preco_saida']) else None
        datetime_saida = pd.Timestamp(linha['datetime_saida']) if pd.notna(linha['datetime_saida']) else None
        exit_fonte = 'diario_aproximado'

    return {
        'id_trade': id_trade,
        'ativo': str(linha['ativo']),
        'contrato': str(perna_entrada['contrato']),
        'tipo_operacao': str(perna_entrada['tipo']),
        'tickets': tickets,
        'preco_entrada': preco_entrada,
        'datetime_entrada': datetime_entrada,
        'preco_saida': preco_saida,
        'datetime_saida': datetime_saida,
        'exit_fonte': exit_fonte,
    }


def carregar_tick_para_trade(alinhamento: dict) -> pd.DataFrame | None:
    """Carrega o(s) dia(s) de tick já persistidos (`--backfill-ticks`)
    cobrindo o alinhamento de um trade. None se ainda não houve backfill
    pra aquele dia."""
    ativo = alinhamento['ativo']
    dias = {alinhamento['datetime_entrada'].date()}
    if alinhamento['datetime_saida'] is not None:
        dias.add(alinhamento['datetime_saida'].date())

    partes = [persistencia.carregar_ticks_dia(ativo, dia) for dia in sorted(dias)]
    partes = [p for p in partes if p is not None and not p.empty]
    if not partes:
        return None
    return pd.concat(partes, ignore_index=True).sort_values('datetime').reset_index(drop=True)


def _janela_ticks(ticks: pd.DataFrame, centro: pd.Timestamp, segundos_antes: float, segundos_depois: float) -> pd.DataFrame:
    ini = centro - pd.Timedelta(seconds=segundos_antes)
    fim = centro + pd.Timedelta(seconds=segundos_depois)
    return ticks[(ticks['datetime'] >= ini) & (ticks['datetime'] <= fim)]


# -----------------------------------------------------------------------------
# QUALIDADE DE ENTRADA/SAÍDA
# -----------------------------------------------------------------------------

def _acao_de_fill(tipo_operacao: str, e_entrada: bool) -> str:
    """A ação de MERCADO nesse fill específico — entrada de venda é vender,
    saída de venda é comprar de volta (não confundir com `tipo_operacao`,
    que é sempre a direção do trade como um todo)."""
    if e_entrada:
        return tipo_operacao
    return 'venda' if tipo_operacao == 'compra' else 'compra'


def qualidade_fill(ticks: pd.DataFrame, timestamp: pd.Timestamp, preco_obtido: float,
                    tipo_operacao: str, e_entrada: bool, janela_segundos: float | None = None) -> dict | None:
    """
    Preço obtido vs. melhor preço disponível numa janela curta em torno do
    timestamp exato do fill — "quanto ficou na mesa" na execução em si.
    `folga_pontos` >= 0 esperado (obtido nunca deveria ser melhor que o
    melhor da própria janela); se vier negativo é sinal de janela estreita
    demais pra capturar o próprio tick do fill, não um erro a esconder.
    """
    janela_segundos = janela_segundos if janela_segundos is not None else cfg.TICK_JANELA_QUALIDADE_SEGUNDOS
    janela = _janela_ticks(ticks, timestamp, janela_segundos, janela_segundos)
    if janela.empty:
        return None

    acao = _acao_de_fill(tipo_operacao, e_entrada)
    quer_preco_baixo = acao == 'compra'
    melhor = float(janela['price'].min() if quer_preco_baixo else janela['price'].max())
    folga_pontos = (preco_obtido - melhor) if quer_preco_baixo else (melhor - preco_obtido)

    return {
        'acao': acao,
        'preco_obtido': float(preco_obtido),
        'melhor_preco_janela': melhor,
        'folga_pontos': float(folga_pontos),
        'n_ticks_janela': int(len(janela)),
        'janela_segundos': janela_segundos,
    }


# -----------------------------------------------------------------------------
# DETECÇÃO DE VIOLINO (whipsaw)
# -----------------------------------------------------------------------------

def detectar_pivos_zigzag(ticks: pd.DataFrame, limiar_pontos: float) -> pd.DataFrame:
    """
    Reduz a série de tick a pontos de inflexão (zigzag clássico): só marca
    um novo pivô quando o preço reverte >= `limiar_pontos` a partir do
    extremo corrente. Base pra `detectar_violinos` — um violino é 2 pernas
    de zigzag opostas, rápidas, ambas >= limiar.
    """
    if ticks.empty:
        return pd.DataFrame(columns=['datetime', 'price', 'tipo'])

    precos = ticks['price'].to_numpy()
    tempos = ticks['datetime'].to_numpy()

    pivos = []
    direcao = 0          # 0=indefinida, 1=subindo desde o último pivô, -1=descendo
    extremo_idx = 0

    for i in range(1, len(precos)):
        delta = precos[i] - precos[extremo_idx]
        if direcao <= 0 and delta >= limiar_pontos:
            pivos.append((tempos[extremo_idx], precos[extremo_idx], 'vale'))
            direcao = 1
            extremo_idx = i
        elif direcao >= 0 and delta <= -limiar_pontos:
            pivos.append((tempos[extremo_idx], precos[extremo_idx], 'topo'))
            direcao = -1
            extremo_idx = i
        elif (direcao == 1 and precos[i] > precos[extremo_idx]) or \
             (direcao == -1 and precos[i] < precos[extremo_idx]) or direcao == 0:
            extremo_idx = i  # extremo corrente melhora sem confirmar reversão ainda

    pivos.append((tempos[extremo_idx], precos[extremo_idx], 'topo' if direcao == 1 else 'vale'))
    return pd.DataFrame(pivos, columns=['datetime', 'price', 'tipo'])


def detectar_violinos(ticks: pd.DataFrame, ativo: str,
                       limiar_pontos: float | None = None,
                       janela_max_segundos: float | None = None) -> list[dict]:
    """Ida-e-volta rápida: 2 pernas de zigzag opostas, cada uma >= limiar,
    completando o ciclo em <= janela_max_segundos — acima do ruído normal
    de tick, sugere ausência de sinal real (ver PROVISÓRIO em
    config_backtest.py)."""
    limiar_pontos = limiar_pontos if limiar_pontos is not None else cfg.TICK_VIOLINO_LIMIAR_PONTOS.get(ativo, cfg.TICK_VIOLINO_LIMIAR_PONTOS['WIN'])
    janela_max_segundos = janela_max_segundos if janela_max_segundos is not None else cfg.TICK_VIOLINO_JANELA_MAX_SEGUNDOS

    pivos = detectar_pivos_zigzag(ticks, limiar_pontos)
    if len(pivos) < 3:
        return []

    violinos = []
    for i in range(len(pivos) - 2):
        p0, p1, p2 = pivos.iloc[i], pivos.iloc[i + 1], pivos.iloc[i + 2]
        duracao = (pd.Timestamp(p2['datetime']) - pd.Timestamp(p0['datetime'])).total_seconds()
        if duracao > janela_max_segundos:
            continue
        amplitude = min(abs(p1['price'] - p0['price']), abs(p2['price'] - p1['price']))
        violinos.append({
            'inicio': pd.Timestamp(p0['datetime']),
            'fim': pd.Timestamp(p2['datetime']),
            'preco_ida': float(p1['price']),
            'amplitude_pontos': float(amplitude),
            'duracao_segundos': float(duracao),
        })
    return violinos


# -----------------------------------------------------------------------------
# CORRETO MAS ESTOPADO
# -----------------------------------------------------------------------------

def correto_mas_estopado(ticks: pd.DataFrame, ativo: str, datetime_saida: pd.Timestamp,
                          preco_saida: float, tipo_operacao: str,
                          janela_minutos: float | None = None,
                          limiar_pontos: float | None = None) -> dict:
    """
    Depois da saída (stop batido), o preço reverteu na direção da tese
    original o suficiente, dentro da janela, pra sugerir "a tese estava
    certa, só o stop foi curto pra volatilidade do momento"?
    """
    janela_minutos = janela_minutos if janela_minutos is not None else cfg.TICK_ESTOPADO_JANELA_MINUTOS
    limiar_pontos = limiar_pontos if limiar_pontos is not None else cfg.TICK_ESTOPADO_LIMIAR_PONTOS.get(ativo, cfg.TICK_ESTOPADO_LIMIAR_PONTOS['WIN'])

    limite = datetime_saida + pd.Timedelta(minutes=janela_minutos)
    janela = ticks[(ticks['datetime'] > datetime_saida) & (ticks['datetime'] <= limite)]
    if janela.empty:
        return {'correto_mas_estopado': False, 'motivo': 'sem tick apos a saida na janela',
                'excursao_favoravel_pontos': None, 'janela_minutos': janela_minutos}

    # tese de compra: reversão favorável = preço SOBE depois do stop.
    # tese de venda: reversão favorável = preço CAI depois do stop.
    if tipo_operacao == 'compra':
        excursao = float(janela['price'].max() - preco_saida)
    else:
        excursao = float(preco_saida - janela['price'].min())

    return {
        'correto_mas_estopado': bool(excursao >= limiar_pontos),
        'excursao_favoravel_pontos': excursao,
        'limiar_pontos': limiar_pontos,
        'janela_minutos': janela_minutos,
    }


# -----------------------------------------------------------------------------
# SIMULAÇÃO "E SE" — stop/alvo alternativos sobre a série de tick real
# -----------------------------------------------------------------------------

def simular_stop_alternativo(ticks: pd.DataFrame, datetime_entrada: pd.Timestamp, preco_entrada: float,
                              tipo_operacao: str, novo_stop_pontos: float,
                              novo_alvo_pontos: float | None = None) -> dict:
    """
    Recalcula o resultado do trade com stop (e opcionalmente alvo)
    alternativos, andando tick a tick a partir da entrada real — não uma
    aproximação por candle M5. Sem alvo informado, só encerra no stop ou no
    fim da série de tick fornecida.
    """
    janela = ticks[ticks['datetime'] >= datetime_entrada]
    if janela.empty:
        return {'saida': None, 'motivo': 'sem tick apos a entrada', 'resultado_pontos': None}

    precos = janela['price'].to_numpy()
    tempos = janela['datetime'].to_numpy()
    sinal = 1 if tipo_operacao == 'compra' else -1

    preco_stop = preco_entrada - sinal * novo_stop_pontos
    preco_alvo = (preco_entrada + sinal * novo_alvo_pontos) if novo_alvo_pontos else None

    bateu_stop = (precos <= preco_stop) if sinal == 1 else (precos >= preco_stop)
    idx_stop = int(np.argmax(bateu_stop)) if bateu_stop.any() else None

    idx_alvo = None
    if preco_alvo is not None:
        bateu_alvo = (precos >= preco_alvo) if sinal == 1 else (precos <= preco_alvo)
        idx_alvo = int(np.argmax(bateu_alvo)) if bateu_alvo.any() else None

    candidatos = [(idx, motivo, preco) for idx, motivo, preco in
                  [(idx_stop, 'stop', preco_stop), (idx_alvo, 'alvo', preco_alvo)]
                  if idx is not None]

    if not candidatos:
        ultimo = len(precos) - 1
        resultado = float((precos[ultimo] - preco_entrada) * sinal)
        return {
            'saida': 'fim_da_serie', 'preco_saida': float(precos[ultimo]),
            'datetime_saida': pd.Timestamp(tempos[ultimo]), 'resultado_pontos': resultado,
        }

    idx_saida, motivo, preco_saida = min(candidatos, key=lambda c: c[0])
    resultado = float((preco_saida - preco_entrada) * sinal)
    return {
        'saida': motivo, 'preco_saida': float(preco_saida),
        'datetime_saida': pd.Timestamp(tempos[idx_saida]), 'resultado_pontos': resultado,
    }
