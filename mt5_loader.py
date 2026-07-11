# =============================================================================
# mt5_loader.py — Carga de dados via MetaTrader5 (Camada 1 do backtest)
# =============================================================================
# Responsabilidade: complementar o histórico do CSV com (a) candles do
# período mais recente que o CSV não cobre, usando sempre o contrato mais
# recente disponível (regra operacional confirmada por Jesué), e (b) ticks
# com flag de agressor para cálculo de delta, usando copy_ticks_from() com
# retry — único método validado como funcional (copy_ticks_range() falhou
# de forma consistente em teste real, mesmo havendo dado disponível).
# =============================================================================

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
import time
import re
from datetime import datetime, timedelta

import config_backtest as cfg


# -----------------------------------------------------------------------------
# CONEXÃO
# -----------------------------------------------------------------------------

def conectar():
    if not mt5.initialize():
        raise ConnectionError(f"Falha ao conectar no MT5: {mt5.last_error()}")
    print(f"[mt5_loader] Conectado ao MT5 (build {mt5.terminal_info().build})")


def desconectar():
    mt5.shutdown()


# -----------------------------------------------------------------------------
# CHECAGEM DE CONECTIVIDADE
# -----------------------------------------------------------------------------
# Adicionado em 30/06/2026 — print de servidores MT5 Access mostrou vários
# com latência alta ou indisponível (n/a), explicando parte da instabilidade
# observada em testes de busca de tick. Esta função testa a saúde da conexão
# ANTES de iniciar operações pesadas (ex: carga inicial de 2 anos de dado),
# para alertar o usuário em vez de só tentar e falhar silenciosamente.

def checar_conectividade(ticker_teste: str = "WINQ26") -> dict:
    """
    Testa a saúde da conexão com o MT5: terminal conectado, latência de
    uma chamada simples (tick atual), e confirma que dados básicos
    respondem antes de iniciar operações pesadas.

    Retorna dict com status e métricas. Não lança exceção — quem chama
    decide se interrompe ou avisa e segue.
    """
    resultado = {
        'terminal_conectado': False,
        'latencia_ms': None,
        'symbol_responde': False,
        'status': 'desconhecido'
    }

    terminal = mt5.terminal_info()
    if terminal is None:
        resultado['status'] = 'terminal_nao_conectado'
        return resultado

    resultado['terminal_conectado'] = terminal.connected

    if not terminal.connected:
        resultado['status'] = 'sem_conexao_servidor'
        return resultado

    # Mede latência de uma chamada simples e real
    t0 = time.time()
    tick = mt5.symbol_info_tick(ticker_teste)
    t1 = time.time()

    resultado['latencia_ms'] = round((t1 - t0) * 1000, 1)
    resultado['symbol_responde'] = tick is not None

    if not resultado['symbol_responde']:
        resultado['status'] = 'symbol_sem_resposta'
    elif resultado['latencia_ms'] > 2000:
        resultado['status'] = 'latencia_alta'
    else:
        resultado['status'] = 'ok'

    return resultado


def exigir_conexao_saudavel(ticker_teste: str = "WINQ26", latencia_max_ms: float = 2000):
    """
    Checa conectividade e levanta erro claro se não estiver saudável.
    Usar antes de operações pesadas (carga inicial de 2 anos de histórico).
    """
    status = checar_conectividade(ticker_teste)

    if status['status'] == 'terminal_nao_conectado':
        raise ConnectionError("[mt5_loader] Terminal MT5 não está aberto/conectado.")
    if status['status'] == 'sem_conexao_servidor':
        raise ConnectionError("[mt5_loader] Terminal aberto mas sem conexão com o servidor da corretora.")
    if status['status'] == 'symbol_sem_resposta':
        raise ConnectionError(f"[mt5_loader] Símbolo {ticker_teste} não respondeu — verifique Market Watch.")
    if status['status'] == 'latencia_alta':
        print(f"[mt5_loader] ⚠️ Latência alta detectada ({status['latencia_ms']}ms). "
              f"Operações pesadas podem ser lentas ou instáveis. Considere trocar "
              f"de servidor MT5 Access antes de continuar (Ferramentas > Opções > Servidor).")
    else:
        print(f"[mt5_loader] Conexão saudável (latência: {status['latencia_ms']}ms)")

    return status


# -----------------------------------------------------------------------------
# DETECÇÃO DO CONTRATO MAIS RECENTE
# -----------------------------------------------------------------------------
# Regra confirmada por Jesué: SEMPRE o ticker de vencimento mais recente
# disponível na Market Watch — nunca por liquidez/volume. Mesmo no dia de
# rolagem, mesmo que o contrato anterior tenha mais liquidez.

_LETRA_MES = {
    'F': 1, 'G': 2, 'H': 3, 'J': 4, 'K': 5, 'M': 6,
    'N': 7, 'Q': 8, 'U': 9, 'V': 10, 'X': 11, 'Z': 12
}

_PADRAO_TICKER = re.compile(r'^([A-Z]+)([FGHJKMNQUVXZ])(\d{2})$')


def _chave_ordenacao_vencimento(ticker: str):
    """
    Extrai (ano, mes) de um ticker tipo WINQ26 para permitir ordenação
    cronológica por data de vencimento. Retorna None se não casar o padrão.
    """
    m = _PADRAO_TICKER.match(ticker)
    if not m:
        return None
    _, letra_mes, ano_2digitos = m.groups()
    ano = 2000 + int(ano_2digitos)
    mes = _LETRA_MES[letra_mes]
    return (ano, mes)


def detectar_contrato_mais_recente(prefixo: str) -> str | None:
    """
    Detecta o contrato VIGENTE (o que Jesué efetivamente opera).

    Lógica confirmada por Jesué (30/06/2026): não é sobre calcular a data
    exata de vencimento — é mais simples. Em qualquer momento, podem
    coexistir dois contratos com negociação ativa real (ex: WDON26 e
    WDOQ26 ao mesmo tempo, perto da virada). Jesué sempre opera o de
    letra/mês MAIS DISTANTE no futuro dentre os que têm negociação ativa
    de verdade — nunca o mais próximo de vencer, mesmo que este ainda
    tenha liquidez residual.

    Critério: dentre TODOS os candidatos com negociação real acontecendo
    agora (volume > 0 nos candles mais recentes), escolhe o de
    vencimento mais distante no tempo (ano, mês maior). Contratos sem
    volume são descartados antes de comparar — eles não competem.
    """
    symbols = mt5.symbols_get()
    if symbols is None:
        return None

    candidatos_brutos = []
    for s in symbols:
        if not s.name.startswith(prefixo) or not s.visible:
            continue
        if s.name in cfg.CONTRATOS_EXCLUIDOS:
            continue
        chave = _chave_ordenacao_vencimento(s.name)
        if chave is None:
            continue
        if prefixo == "WIN" and chave[1] % 2 != 0:
            continue  # WIN só vence em mês par
        candidatos_brutos.append((chave, s.name))

    if not candidatos_brutos:
        print(f"[mt5_loader] ⚠️ Nenhum contrato candidato para prefixo '{prefixo}'")
        return None

    candidatos_brutos.sort(key=lambda x: x[0])  # mais antigo primeiro

    # Filtra só os que têm negociação real acontecendo agora
    candidatos_com_negociacao = []
    for chave, ticker in candidatos_brutos:
        rates = mt5.copy_rates_from_pos(ticker, mt5.TIMEFRAME_M5, 0, 5)
        if rates is None or len(rates) == 0:
            continue
        volume = sum(r['tick_volume'] for r in rates)
        if volume > 0:
            candidatos_com_negociacao.append((chave, ticker, volume))
            print(f"[mt5_loader] {ticker}: negociação ativa confirmada "
                  f"(volume últimos 5 candles = {volume})")

    if not candidatos_com_negociacao:
        # Nenhum com negociação agora (ex: mercado fechado) — usa o de
        # vencimento mais próximo a partir de hoje, como melhor estimativa
        hoje = datetime.now()
        futuros = [(c, t) for c, t in candidatos_brutos if c >= (hoje.year, hoje.month)]
        if futuros:
            fallback = futuros[0][1]
            print(f"[mt5_loader] ⚠️ Nenhum candidato com negociação ativa agora "
                  f"(mercado pode estar fechado). Usando estimativa: {fallback}")
            return fallback
        return None

    # CRITÉRIO REAL (corrigido após teste em 30/06/2026): praticamente todo
    # contrato — mesmo vencido há anos, ex: WINZ22 de 2022 — mantém algum
    # volume residual nos candles mais recentes (ruído de negociação
    # esporádica). "Ter volume > 0" não distingue o contrato vigente.
    # O que distingue de forma clara é MAGNITUDE: o contrato realmente
    # operado concentra a esmagadora maioria do volume (testado: WINQ26
    # teve 386.626 contra outros na casa de centenas/milhares; WDOQ26 teve
    # 29.489 contra 2.152 do segundo colocado — mais de 10x de diferença).
    # Critério: maior volume absoluto dentre os candidatos.
    candidatos_com_negociacao.sort(key=lambda x: x[2], reverse=True)  # por volume, maior primeiro
    chave_final, ticker_final, volume_final = candidatos_com_negociacao[0]

    print(f"[mt5_loader] Contrato vigente para {prefixo}: {ticker_final} "
          f"(maior volume absoluto: {volume_final})")

    return ticker_final


# -----------------------------------------------------------------------------
# RETRY GENÉRICO
# -----------------------------------------------------------------------------

def _com_retry(funcao, *args, tentativas=None, delay=None, **kwargs):
    """
    Executa uma chamada MT5 com retry. Validado como necessário em teste real
    (30/06/2026): chamadas a copy_ticks_from() falharam de forma transitória
    ("Terminal: Call failed") mesmo quando o dado existia e foi confirmado
    disponível em reteste imediato.
    """
    tentativas = tentativas or cfg.RETRY_TENTATIVAS
    delay = delay or cfg.RETRY_DELAY_SEGUNDOS

    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        resultado = funcao(*args, **kwargs)
        if resultado is not None and len(resultado) > 0:
            return resultado
        ultimo_erro = mt5.last_error()
        if tentativa < tentativas:
            time.sleep(delay)

    print(f"[mt5_loader] ⚠️ Falhou após {tentativas} tentativas. Último erro: {ultimo_erro}")
    return None


# -----------------------------------------------------------------------------
# CANDLES
# -----------------------------------------------------------------------------

def buscar_candles(ticker: str, n: int = 10000) -> pd.DataFrame | None:
    """Busca candles M5 do ticker, com retry."""
    mt5.symbol_select(ticker, True)

    rates = _com_retry(mt5.copy_rates_from_pos, ticker, mt5.TIMEFRAME_M5, 0, n)
    if rates is None:
        return None

    df = pd.DataFrame(rates)
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df = df.rename(columns={'tick_volume': 'volume_ticks'})
    df['fonte'] = f'mt5:{ticker}'

    return df[['datetime', 'open', 'high', 'low', 'close', 'volume_ticks', 'fonte']]


def buscar_candles_apos(ticker: str, data_referencia: datetime) -> pd.DataFrame | None:
    """
    Busca candles do ticker a partir da data de referência (inclusive).
    Inclusive de propósito: o candle em `data_referencia` pode ter sido
    salvo ainda em formação (MT5 sempre retorna o candle M5 corrente com
    valores parciais, mudando a cada tick) — re-buscá-lo permite que
    `merge_fontes.atualizar_incremental` (via `drop_duplicates(keep='last')`)
    substitua o valor parcial pelo definitivo assim que o candle fechar, em
    vez de congelá-lo para sempre no primeiro flagra incompleto.
    """
    todos = buscar_candles(ticker, n=10000)
    if todos is None:
        return None
    return todos[todos['datetime'] >= data_referencia].reset_index(drop=True)


# -----------------------------------------------------------------------------
# TICKS (com paginação e retry — método validado: copy_ticks_from)
# -----------------------------------------------------------------------------

def _decodificar_agressor(flags: int) -> str:
    """
    Decodifica o flag de tick do MT5 para identificar o agressor.
    Validado empiricamente em 30/06/2026: flag 56 = LAST+VOLUME+BUY,
    flag 88 = LAST+VOLUME+SELL. Constantes oficiais:
      TICK_FLAG_BUY  = 32
      TICK_FLAG_SELL = 64
    """
    if flags & 32:
        return 'comprador'
    elif flags & 64:
        return 'vendedor'
    return 'indefinido'


def buscar_ticks_periodo(ticker: str, inicio: datetime, fim: datetime) -> pd.DataFrame | None:
    """
    Busca todos os ticks de negócio (LAST) de um ticker entre inicio e fim,
    paginando com copy_ticks_from() — copy_ticks_range() foi descartado do
    design por falhar consistentemente em teste real, apesar do dado existir.

    CORREÇÃO DE TIMEZONE (validada empiricamente em 30/06/2026): passar um
    datetime "ingênuo" para copy_ticks_from() faz o MT5 SOMAR 3h internamente
    ao horário pedido antes de buscar o tick — confirmado em 10 pontos de
    teste ao longo do dia (pedido 09h -> retornado 12h, etc, sempre exatos
    +3h). Correção: SUBTRAIR 3h do horário desejado antes de enviar ao MT5.

    CORREÇÃO DE TIPO (causa raiz real do bug de paginação travada, validada
    em 30/06/2026): copy_ticks_from() se comporta de forma DIFERENTE e
    incorreta quando recebe um pandas.Timestamp como argumento de data,
    mesmo representando exatamente o mesmo instante que um datetime.datetime
    nativo — testado lado a lado: datetime nativo avançou corretamente
    (09:04:15 -> lote até 09:07:40), pandas.Timestamp do MESMO instante
    retornou o lote ANTERIOR (09:02:58-09:04:14), como se ignorasse o ponto
    de partida. Isso explicava a paginação travada em testes anteriores —
    não era questão de segundo vs milissegundo, era o tipo do objeto.
    Correção: SEMPRE converter para datetime.datetime nativo (.to_pydatetime()
    se vier de pandas) imediatamente antes de cada chamada ao MT5.

    Pagina avançando o ponto de partida a cada lote de TICKS_POR_CHAMADA,
    até ultrapassar o horário de fim.
    """
    mt5.symbol_select(ticker, True)

    DESLOCAMENTO_TIMEZONE = timedelta(hours=3)

    todos_ticks = []
    ponto_atual = inicio  # mantido como datetime.datetime nativo o tempo todo

    while ponto_atual < fim:
        ponto_ajustado = ponto_atual - DESLOCAMENTO_TIMEZONE

        # Garantia explícita de tipo nativo — não confiar em cálculos
        # anteriores não terem introduzido um pandas.Timestamp por engano
        if isinstance(ponto_ajustado, pd.Timestamp):
            ponto_ajustado = ponto_ajustado.to_pydatetime()

        lote = _com_retry(
            mt5.copy_ticks_from, ticker, ponto_ajustado,
            cfg.TICKS_POR_CHAMADA, mt5.COPY_TICKS_TRADE
        )

        if lote is None or len(lote) == 0:
            break

        df_lote = pd.DataFrame(lote)
        df_lote['datetime'] = pd.to_datetime(df_lote['time'], unit='s')

        # Filtra só o que está dentro da janela pedida (em horário real,
        # já que os timestamps retornados pelo MT5 já vêm corretos em BRT)
        df_lote = df_lote[(df_lote['datetime'] >= inicio) & (df_lote['datetime'] <= fim)]
        todos_ticks.append(df_lote)

        # Avanço do ponteiro: converte explicitamente de volta para
        # datetime.datetime nativo (pd.to_datetime() acima gera Timestamp)
        ultimo_tempo_real = pd.to_datetime(lote[-1]['time'], unit='s').to_pydatetime()

        if len(lote) >= cfg.TICKS_POR_CHAMADA and ultimo_tempo_real == ponto_atual:
            print(f"[mt5_loader] ⚠️ Lote veio CHEIO ({len(lote)} ticks) sem avançar do "
                  f"ponto anterior ({ultimo_tempo_real}) — possível volume extremo "
                  f"num único segundo. Considere aumentar TICKS_POR_CHAMADA.")

        if ultimo_tempo_real <= ponto_atual:
            # Não avançou de fato — evita loop infinito
            break
        ponto_atual = ultimo_tempo_real

        if len(lote) < cfg.TICKS_POR_CHAMADA:
            # Lote incompleto = provavelmente não há mais dado à frente
            break

    if not todos_ticks:
        return None

    df_final = pd.concat(todos_ticks, ignore_index=True)
    df_final = df_final.drop_duplicates(subset='time_msc').sort_values('datetime').reset_index(drop=True)

    df_final['agressor'] = df_final['flags'].apply(_decodificar_agressor)
    df_final = df_final.rename(columns={'last': 'price', 'volume': 'volume_ticks'})

    return df_final[['datetime', 'price', 'volume_ticks', 'agressor', 'flags']]


# -----------------------------------------------------------------------------
# EXECUÇÕES REAIS — reconciliação do Diário de Trade (Fase D)
# -----------------------------------------------------------------------------
# A conexão MT5 já usada acima pra candles/ticks é a CONTA REAL do Jesué na
# BTG Pactual (confirmado em 08/07/2026: mt5.account_info() retorna o login,
# servidor BancoBTGPactual-PRD, nome do titular) — não é um terminal só de
# cotação. `mt5.history_deals_get()` expõe os fills reais dessa conta,
# ticket a ticket, servindo como fonte de verdade pra reconciliar contra o
# que foi lançado manualmente no diário.

def _extrair_raiz_contrato(simbolo: str) -> str:
    """WINJ26 -> WIN, WDOK26 -> WDO — reaproveita o mesmo padrão de ticker
    (contrato-específico) já usado pra detectar o contrato vigente."""
    m = _PADRAO_TICKER.match(simbolo)
    return m.group(1) if m else simbolo


def _corrigir_mojibake(texto: str | None) -> str | None:
    """
    O campo `comment` de `history_deals_get` vem com acentuação quebrada —
    bytes UTF-8 de verdade, mas Python já decodificou como latin1 antes de
    chegar aqui (comportamento da própria lib `MetaTrader5`). Reencoda como
    latin1 e decodifica como UTF-8 pra recuperar o texto original. Se não
    for esse o caso (string já correta, ou de fato não-UTF-8), devolve o
    texto original sem alteração — nunca lança exceção nem descarta caractere.
    """
    if not texto:
        return texto
    try:
        return texto.encode('latin1').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        return texto


def buscar_execucoes_periodo(inicio: datetime, fim: datetime) -> pd.DataFrame | None:
    """
    Busca os fills reais da conta MT5 conectada no período — equivalente ao
    que a aba `profitreal` da planilha trazia via export manual do Profit,
    mas ao vivo e via API. Retorna None se a chamada ao MT5 falhar (conexão
    fora do ar); DataFrame vazio (não None) se a chamada funcionou mas não
    houve fills no período — situação normal, não é erro.
    """
    deals = mt5.history_deals_get(inicio, fim)
    if deals is None:
        return None

    linhas = []
    for d in deals:
        if not d.symbol:
            continue  # sem symbol = operação de saldo/ajuste da conta, não é fill de contrato
        linhas.append({
            'ticket': d.ticket,
            'time': datetime.fromtimestamp(d.time),
            'symbol': d.symbol,
            'raiz_contrato': _extrair_raiz_contrato(d.symbol),
            'tipo': 'compra' if d.type == mt5.DEAL_TYPE_BUY else ('venda' if d.type == mt5.DEAL_TYPE_SELL else 'outro'),
            'entry': d.entry,  # 0=IN (abertura), 1=OUT (fechamento), 2=INOUT (reversão)
            'volume': d.volume,
            'preco': d.price,
            'profit': d.profit,
            'position_id': d.position_id,
            'comment': _corrigir_mojibake(d.comment),
        })
    return pd.DataFrame(linhas)


def agrupar_posicoes(df_deals: pd.DataFrame) -> pd.DataFrame:
    """
    Uma linha por round-trip (`position_id`) a partir dos fills individuais
    de `buscar_execucoes_periodo`: direção e preço de entrada vêm da(s)
    perna(s) IN, preço de saída e profit líquido vêm da(s) perna(s) OUT.
    Posições sem perna IN visível na janela pesquisada (aberta antes do
    período) são descartadas — não dá pra reconciliar sem o preço de entrada.
    """
    colunas = ['position_id', 'raiz_contrato', 'tipo', 'preco_entrada', 'preco_saida',
               'datetime_entrada', 'datetime_saida', 'volume', 'profit_total',
               'tickets_entrada', 'tickets_saida']
    if df_deals.empty:
        return pd.DataFrame(columns=colunas)

    linhas = []
    for position_id, grupo in df_deals.groupby('position_id'):
        pernas_in = grupo[grupo['entry'] == 0]
        pernas_out = grupo[grupo['entry'].isin([1, 2])]
        if pernas_in.empty:
            continue

        primeira_in = pernas_in.iloc[0]
        linhas.append({
            'position_id': position_id,
            'raiz_contrato': primeira_in['raiz_contrato'],
            'tipo': primeira_in['tipo'],
            'preco_entrada': primeira_in['preco'],
            'preco_saida': pernas_out.iloc[-1]['preco'] if not pernas_out.empty else None,
            'datetime_entrada': primeira_in['time'],
            'datetime_saida': pernas_out.iloc[-1]['time'] if not pernas_out.empty else None,
            'volume': primeira_in['volume'],
            'profit_total': float(pernas_out['profit'].sum()) if not pernas_out.empty else 0.0,
            'tickets_entrada': list(pernas_in['ticket']),
            'tickets_saida': list(pernas_out['ticket']),
        })
    return pd.DataFrame(linhas, columns=colunas)


# -----------------------------------------------------------------------------
# POSIÇÕES ABERTAS AO VIVO — seção "Posição" dos dashboards (ver plano em
# C:\Users\jesue\.claude\plans\analise-o-bloco-de-glimmering-marble.md)
# -----------------------------------------------------------------------------

def buscar_posicoes_abertas(ticker: str | None = None) -> pd.DataFrame | None:
    """
    Wrap de mt5.positions_get() — posições REALMENTE abertas agora na conta
    conectada (mesma conta real da reconciliação, ver comentário acima de
    _extrair_raiz_contrato). Chamada direta, sem _com_retry: roda num tick
    de alguns segundos (não o de 1s), mesmo espírito de
    atualizar_candle_atual/symbol_info_tick em dashboard.py — se falhar
    numa rodada, o próximo ciclo tenta de novo, nunca trava a UI.

    Retorna None se a chamada ao MT5 falhar; DataFrame vazio (colunas
    garantidas) se a chamada funcionou mas não há posição aberta agora.
    """
    colunas = ['ticket', 'ativo', 'contrato', 'tipo_operacao', 'volume',
               'preco_entrada', 'preco_atual', 'sl', 'tp', 'profit',
               'datetime_entrada', 'comment']
    posicoes = mt5.positions_get(symbol=ticker) if ticker else mt5.positions_get()
    if posicoes is None:
        return None
    if len(posicoes) == 0:
        return pd.DataFrame(columns=colunas)

    linhas = []
    for p in posicoes:
        linhas.append({
            'ticket': p.ticket,
            'ativo': _extrair_raiz_contrato(p.symbol),
            'contrato': p.symbol,
            'tipo_operacao': 'long' if p.type == mt5.POSITION_TYPE_BUY else 'short',
            'volume': p.volume,
            'preco_entrada': p.price_open,
            'preco_atual': p.price_current,
            'sl': p.sl or None,
            'tp': p.tp or None,
            'profit': p.profit,
            'datetime_entrada': datetime.fromtimestamp(p.time),
            'comment': _corrigir_mojibake(p.comment),
        })
    return pd.DataFrame(linhas, columns=colunas)


_TIPO_ORDEM_PENDENTE = {
    mt5.ORDER_TYPE_BUY_LIMIT: 'compra_limitada',
    mt5.ORDER_TYPE_SELL_LIMIT: 'venda_limitada',
    mt5.ORDER_TYPE_BUY_STOP: 'compra_stop',
    mt5.ORDER_TYPE_SELL_STOP: 'venda_stop',
    mt5.ORDER_TYPE_BUY_STOP_LIMIT: 'compra_stop_limitada',
    mt5.ORDER_TYPE_SELL_STOP_LIMIT: 'venda_stop_limitada',
}


def buscar_ordens_pendentes(ticker: str | None = None) -> pd.DataFrame | None:
    """
    Wrap de mt5.orders_get() — ordens PENDENTES (ainda não executadas: buy/
    sell limit/stop) na conta conectada. Diferente de `buscar_posicoes_
    abertas`: aqui o preço-alvo é o que o usuário definiu (`price_open`),
    não um preço de entrada já preenchido. Mesmo espírito de chamada direta
    (sem _com_retry) que `buscar_posicoes_abertas`.

    Retorna None se a chamada ao MT5 falhar; DataFrame vazio (colunas
    garantidas) se a chamada funcionou mas não há ordem pendente agora.
    """
    colunas = ['ticket', 'ativo', 'contrato', 'tipo_ordem', 'volume',
               'preco_pedido', 'sl', 'tp', 'datetime_criacao', 'comment']
    ordens = mt5.orders_get(symbol=ticker) if ticker else mt5.orders_get()
    if ordens is None:
        return None
    if len(ordens) == 0:
        return pd.DataFrame(columns=colunas)

    linhas = []
    for o in ordens:
        linhas.append({
            'ticket': o.ticket,
            'ativo': _extrair_raiz_contrato(o.symbol),
            'contrato': o.symbol,
            'tipo_ordem': _TIPO_ORDEM_PENDENTE.get(o.type, 'outro'),
            'volume': o.volume_current,
            'preco_pedido': o.price_open,
            'sl': o.sl or None,
            'tp': o.tp or None,
            'datetime_criacao': datetime.fromtimestamp(o.time_setup),
            'comment': _corrigir_mojibake(o.comment),
        })
    return pd.DataFrame(linhas, columns=colunas)


# -----------------------------------------------------------------------------
# TESTE STANDALONE
# -----------------------------------------------------------------------------

if __name__ == '__main__':
    print("=" * 90)
    print("  TESTE STANDALONE — mt5_loader.py")
    print("=" * 90)

    conectar()

    for prefixo in cfg.ATIVOS:
        ticker = detectar_contrato_mais_recente(prefixo)
        if ticker is None:
            continue

        candles = buscar_candles(ticker, n=50)
        if candles is not None:
            print(f"\n{prefixo} ({ticker}) — últimos 3 candles:")
            print(candles.tail(3).to_string(index=False))

    desconectar()
    print("\nTeste concluído.")
