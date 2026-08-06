# =============================================================================
# ctrader_loader.py — Conexão FIX com o cTrader (protótipo, camada 1)
# =============================================================================
# Responsabilidade hoje: só o handshake de sessão FIX 4.4 (Logon/Logout)
# contra as duas sessões da corretora (QUOTE = dados de mercado, TRADE =
# execução), pra validar conectividade antes de desenhar o resto da
# integração (equivalente ao `checar_conectividade` do mt5_loader.py).
#
# Implementado com `simplefix` (engine FIX puro-Python, sem dependência de
# C++/QuickFIX) sobre um socket SSL cru — sem lib de sessão com reconexão/
# sequence recovery ainda. Isso é suficiente para teste de conectividade,
# mas NÃO é a base para o motor de execução real: uma integração completa
# (auto-reconnect, sequence number persistente entre reinícios, resposta a
# heartbeat/test request, parsing de ExecutionReport) é trabalho futuro,
# ver CORRELAÇÕES/roadmap.
#
# ATENÇÃO: este módulo nunca deve compor nem enviar NewOrderSingle (35=D)
# ou qualquer mensagem de ordem — a sessão TRADE aqui só é usada para
# validar o handshake (Logon/Logout), nunca para operar.

import socket
import ssl
import time

import pandas as pd
import simplefix

import credenciais_ctrader as cfg


def _montar_logon(sessao: dict, seqnum: int) -> simplefix.FixMessage:
    msg = simplefix.FixMessage()
    msg.append_pair(8, "FIX.4.4")
    msg.append_pair(35, "A")
    msg.append_pair(49, sessao["sender_comp_id"])
    msg.append_pair(56, sessao["target_comp_id"])
    msg.append_pair(34, seqnum)
    msg.append_utc_timestamp(52)
    msg.append_pair(50, sessao["sub_id"])
    msg.append_pair(57, sessao["sub_id"])
    msg.append_pair(98, 0)          # EncryptMethod = none
    msg.append_pair(108, 30)        # HeartBtInt (segundos)
    msg.append_pair(141, "Y")       # ResetSeqNumFlag
    msg.append_pair(553, sessao["username"])
    msg.append_pair(554, sessao["password"])
    return msg


def _montar_logout(sessao: dict, seqnum: int) -> simplefix.FixMessage:
    msg = simplefix.FixMessage()
    msg.append_pair(8, "FIX.4.4")
    msg.append_pair(35, "5")
    msg.append_pair(49, sessao["sender_comp_id"])
    msg.append_pair(56, sessao["target_comp_id"])
    msg.append_pair(34, seqnum)
    msg.append_utc_timestamp(52)
    msg.append_pair(50, sessao["sub_id"])
    msg.append_pair(57, sessao["sub_id"])
    return msg


def testar_logon(sessao: dict, timeout: float = 10.0) -> dict:
    """
    Faz o handshake FIX (Logon + Logout) contra uma sessão cTrader e
    retorna o resultado. Não lança exceção — quem chama decide o que
    fazer com `sucesso`/`erro`. Nunca envia mensagem de ordem.
    """
    resultado = {
        "sub_id": sessao["sub_id"],
        "host": sessao["host"],
        "port": sessao["port_ssl"],
        "sucesso": False,
        "mensagens": [],
        "erro": None,
    }

    sock = None
    try:
        ctx = ssl.create_default_context()
        raw_sock = socket.create_connection(
            (sessao["host"], sessao["port_ssl"]), timeout=timeout
        )
        sock = ctx.wrap_socket(raw_sock, server_hostname=sessao["host"])

        sock.sendall(_montar_logon(sessao, 1).encode())

        parser = simplefix.FixParser()
        logon_ack = None
        deadline = time.time() + timeout

        while time.time() < deadline and logon_ack is None:
            sock.settimeout(max(0.1, deadline - time.time()))
            data = sock.recv(4096)
            if not data:
                resultado["erro"] = "Conexao fechada pelo servidor antes do Logon ack"
                break

            parser.append_buffer(data)
            msg = parser.get_message()
            while msg is not None:
                resultado["mensagens"].append(msg.to_string(separator="|"))
                tipo = msg.get(35)
                if tipo == b"A":
                    logon_ack = msg
                elif tipo in (b"5", b"3"):
                    resultado["erro"] = f"Sessao recusou: {msg.to_string(separator='|')}"
                    logon_ack = "rejeitado"
                msg = parser.get_message()

        if logon_ack not in (None, "rejeitado"):
            resultado["sucesso"] = True
            sock.sendall(_montar_logout(sessao, 2).encode())
            time.sleep(0.5)
        elif resultado["erro"] is None:
            resultado["erro"] = "Timeout esperando Logon ack (35=A)"

    except Exception as e:
        resultado["erro"] = f"{type(e).__name__}: {e}"
    finally:
        if sock is not None:
            sock.close()

    return resultado


def testar_conectividade() -> dict:
    """Testa as duas sessões (QUOTE e TRADE). Só handshake, nunca ordens."""
    return {
        "quote": testar_logon(cfg.CTRADER_QUOTE),
        "trade": testar_logon(cfg.CTRADER_TRADE),
    }


# =============================================================================
# SESSÃO PERSISTENTE + PARSING DE EXECUTIONREPORT (2026-08-04)
# =============================================================================
# Ainda NÃO testado contra a sessão TRADE ao vivo (Fase 1/3 seguem
# bloqueadas — sem histórico nem execução real ainda pra validar contra
# ExecutionReport de verdade). O parsing/agrupamento abaixo é testado com
# mensagens FIX sintéticas (mesmo formato que a cTrader documenta), não
# com dado real observado. Revisar assim que a primeira ExecutionReport
# real chegar — ver `testes/` pro padrão de teste deste projeto.
#
# Decisão deliberada: `ResetSeqNumFlag=Y` em TODO logon/reconexão (já era
# assim desde a Fase 0) em vez de persistir sequence number entre
# reconexões — a cTrader aceita isso sem reclamar (confirmado no teste de
# conectividade da Fase 0), e evita ter que implementar Resend
# Request/gap-fill (complexidade real, só vale a pena se o broker EXIGISSE
# sequência contínua). Simplicidade deliberada, não corte de canto.

# cTrader não expõe LastPx (tag 31) no ExecutionReport — confirmado na
# especificação oficial (help.ctrader.com/fix/specification). AvgPx (tag 6)
# é o preço usado aqui.
_TAG_EXEC_TYPE = 150
_TAG_ORD_STATUS = 39
_TAG_SIDE = 54
_TAG_SYMBOL = 55
_TAG_LAST_QTY = 32
_TAG_AVG_PX = 6
_TAG_TRANSACT_TIME = 60
_TAG_EXEC_ID = 17
_EXEC_TYPE_TRADE = b"F"  # unico ExecType que representa um fill de verdade


def _montar_heartbeat(sessao: dict, seqnum: int, test_req_id: str | None = None) -> simplefix.FixMessage:
    msg = simplefix.FixMessage()
    msg.append_pair(8, "FIX.4.4")
    msg.append_pair(35, "0")
    msg.append_pair(49, sessao["sender_comp_id"])
    msg.append_pair(56, sessao["target_comp_id"])
    msg.append_pair(34, seqnum)
    msg.append_utc_timestamp(52)
    msg.append_pair(50, sessao["sub_id"])
    msg.append_pair(57, sessao["sub_id"])
    if test_req_id is not None:
        msg.append_pair(112, test_req_id)  # TestReqID — obrigatorio quando é resposta a um TestRequest
    return msg


def _parse_transact_time(valor: bytes | None):
    """`TransactTime` (tag 60) vem como `AAAAMMDD-HH:MM:SS[.sss]` (UTC).
    `None` se o campo não veio (nunca inventa um horário)."""
    if not valor:
        return None
    import datetime as _dt
    texto = valor.decode()
    formato = "%Y%m%d-%H:%M:%S.%f" if "." in texto else "%Y%m%d-%H:%M:%S"
    return _dt.datetime.strptime(texto, formato)


def parsear_execution_report(msg: simplefix.FixMessage) -> dict | None:
    """
    Extrai um fill (deal) de uma ExecutionReport (35=8) da cTrader. Só
    retorna dado pra `ExecType='F'` (Trade — fill de verdade); qualquer
    outro ExecType (New/Canceled/Rejected/PendingNew/...) retorna `None` —
    não é um fill, não tem o que extrair.

    Formato de saída pensado pra alimentar `_agrupar_fills_netting`
    abaixo, não o `agrupar_posicoes` do MT5 diretamente (contas netting
    não têm o conceito de `entry` IN/OUT do MT5 — ver comentário da
    função de agrupamento).
    """
    if msg.get(35) != b"8":
        return None
    if msg.get(_TAG_EXEC_TYPE) != _EXEC_TYPE_TRADE:
        return None

    symbol = msg.get(_TAG_SYMBOL)
    side = msg.get(_TAG_SIDE)
    qty = msg.get(_TAG_LAST_QTY)
    preco = msg.get(_TAG_AVG_PX)
    exec_id = msg.get(_TAG_EXEC_ID)
    if not (symbol and side and qty and preco):
        return None  # ExecutionReport de fill sem os campos essenciais — não é um dado confiável

    return {
        "ticket": exec_id.decode() if exec_id else None,
        "symbol": symbol.decode(),
        "tipo": "compra" if side == b"1" else "venda",
        "volume": float(qty),
        "preco": float(preco),
        "time": _parse_transact_time(msg.get(_TAG_TRANSACT_TIME)),
    }


def _agrupar_fills_netting(fills: list[dict]) -> pd.DataFrame:
    """
    Equivalente a `mt5_loader.agrupar_posicoes`, mas pra conta NETTING
    (cTrader) em vez de hedging (MT5): agrupa por `symbol` (não por
    `PosMaintRptID` — a especificação da cTrader diz explicitamente que
    esse campo "só pode ser especificado pra contas hedging", não é
    confiável aqui) e reconstrói round-trips com uma máquina de estado de
    posição líquida (soma de compra − soma de venda). Um fill que afasta o
    líquido de zero é entrada/aumento; um fill que aproxima ou cruza zero
    é saída/redução — nunca usa o branch `DEAL_ENTRY_OUT_BY` do MT5 (não
    existe artefato de hedge numa conta netting).

    Mesmo contrato de saída de `agrupar_posicoes`: uma linha por
    round-trip fechado, mesmas colunas (`position_id` aqui é sintético:
    `f"{symbol}#{n}"`, o n-ésimo round-trip daquele símbolo).

    LACUNA CONHECIDA (não testada — sem ExecutionReport real pra validar
    ainda): um fill que INVERTE o líquido (ex: net +2 e um fill de venda
    de 3, virando net -1) fecha o round-trip antigo corretamente, mas não
    abre um round-trip novo na direção oposta no mesmo fill — o próximo
    fill nessa direção vira uma entrada nova do zero, não do -1 real. Caso
    raro (fill único maior que a posição toda) — revisar quando a Fase 3
    tiver dado real de ExecutionReport pra testar contra.
    """
    colunas = ["position_id", "raiz_contrato", "contrato", "tipo", "preco_entrada", "preco_saida",
               "datetime_entrada", "datetime_saida", "volume", "profit_total",
               "tickets_entrada", "tickets_saida"]
    if not fills:
        return pd.DataFrame(columns=colunas)

    por_symbol: dict[str, list[dict]] = {}
    for f in fills:
        por_symbol.setdefault(f["symbol"], []).append(f)

    linhas = []
    for symbol, lista in por_symbol.items():
        lista = sorted(lista, key=lambda f: f["time"] or 0)
        liquido = 0.0
        round_trip_n = 0
        entrada = None  # dict do round-trip em construção: tickets/preco/volume/time de entrada

        for f in lista:
            sinal = 1.0 if f["tipo"] == "compra" else -1.0
            liquido_antes = liquido
            liquido_depois = liquido + sinal * f["volume"]

            abre_do_zero = liquido_antes == 0.0
            aumenta = (liquido_antes >= 0) == (liquido_depois >= 0) and abs(liquido_depois) > abs(liquido_antes)

            if abre_do_zero or aumenta:
                if entrada is None:
                    round_trip_n += 1
                    entrada = {
                        "position_id": f"{symbol}#{round_trip_n}",
                        "tipo": f["tipo"],
                        "preco_entrada": f["preco"],
                        "datetime_entrada": f["time"],
                        "volume_entrada": 0.0,
                        "tickets_entrada": [],
                    }
                entrada["volume_entrada"] += f["volume"]
                entrada["tickets_entrada"].append(f["ticket"])
            else:
                # Reduz ou cruza o líquido — saída (parcial ou total) do
                # round-trip em construção. Sem posição em construção
                # (reinício no meio de um round-trip que já existia antes
                # de esta sessão começar a escutar) — descarta o fill, sem
                # preço de entrada não dá pra reconciliar (mesmo raciocínio
                # de `agrupar_posicoes` pra posição sem perna IN visível).
                if entrada is not None:
                    bruto = f["preco"] - entrada["preco_entrada"]
                    pnl_pontos = bruto if entrada["tipo"] == "compra" else -bruto
                    profit = pnl_pontos * f["volume"]
                    linhas.append({
                        "position_id": entrada["position_id"],
                        "raiz_contrato": symbol,
                        "contrato": symbol,
                        "tipo": entrada["tipo"],
                        "preco_entrada": entrada["preco_entrada"],
                        "preco_saida": f["preco"],
                        "datetime_entrada": entrada["datetime_entrada"],
                        "datetime_saida": f["time"],
                        "volume": min(entrada["volume_entrada"], f["volume"]),
                        "profit_total": profit,
                        "tickets_entrada": list(entrada["tickets_entrada"]),
                        "tickets_saida": [f["ticket"]],
                    })
                    entrada = None if abs(liquido_depois) < 1e-9 else entrada

            liquido = liquido_depois

    if not linhas:
        return pd.DataFrame(columns=colunas)
    return pd.DataFrame(linhas, columns=colunas)


def escutar_sessao_trade(sessao: dict, ao_receber_fill, deve_parar, timeout_recv: float = 1.0):
    """
    Sessão TRADE persistente de verdade (não o handshake de teste da Fase
    0): faz Logon, e então fica escutando indefinidamente, respondendo
    Heartbeat (35=0) a cada `HeartBtInt` segundos ociosos e TestRequest
    (35=1) com Heartbeat ecoando o TestReqID — comportamento padrão do
    protocolo FIX, obrigatório pra sessão não cair por timeout do lado da
    corretora. Chama `ao_receber_fill(dict)` (o dict de
    `parsear_execution_report`) pra cada fill real recebido; ignora
    qualquer outra ExecutionReport (acks de New/Cancel/Reject).

    `deve_parar`: callable sem argumento, checado a cada iteração — quem
    chama controla quando encerrar (ex: `lambda: threading_event.is_set()`).
    Fecha com Logout (35=5) ao sair, sempre.

    NUNCA envia NewOrderSingle (35=D) — só Logon/Heartbeat/TestRequest-
    resposta/Logout. Ainda não testado contra a sessão TRADE ao vivo (ver
    comentário no topo desta seção) — construído a partir do protocolo FIX
    padrão + do handshake já validado na Fase 0, não a partir de
    observação real de heartbeat da cTrader.
    """
    heartbeat_seg = 30
    seqnum = 1
    sock = None
    try:
        ctx = ssl.create_default_context()
        raw_sock = socket.create_connection((sessao["host"], sessao["port_ssl"]), timeout=10)
        sock = ctx.wrap_socket(raw_sock, server_hostname=sessao["host"])
        sock.sendall(_montar_logon(sessao, seqnum).encode())
        seqnum += 1

        parser = simplefix.FixParser()
        ultimo_envio = time.time()

        while not deve_parar():
            sock.settimeout(timeout_recv)
            try:
                data = sock.recv(4096)
            except socket.timeout:
                data = None

            if data == b"":
                break  # conexao fechada pelo servidor
            if data:
                parser.append_buffer(data)
                msg = parser.get_message()
                while msg is not None:
                    tipo = msg.get(35)
                    if tipo == b"1":  # TestRequest -> responde com Heartbeat ecoando TestReqID
                        test_req_id = msg.get(112)
                        sock.sendall(_montar_heartbeat(
                            sessao, seqnum, test_req_id.decode() if test_req_id else None
                        ).encode())
                        seqnum += 1
                        ultimo_envio = time.time()
                    elif tipo == b"8":
                        fill = parsear_execution_report(msg)
                        if fill is not None:
                            ao_receber_fill(fill)
                    msg = parser.get_message()

            if time.time() - ultimo_envio >= heartbeat_seg:
                sock.sendall(_montar_heartbeat(sessao, seqnum).encode())
                seqnum += 1
                ultimo_envio = time.time()
    finally:
        if sock is not None:
            try:
                sock.sendall(_montar_logout(sessao, seqnum).encode())
            except Exception:
                pass
            sock.close()
