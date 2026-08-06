# =============================================================================
# ctrader_openapi_loader.py — Histórico via cTrader Open API (Camada 1)
# =============================================================================
# Responsabilidade: candle histórico M5 (ProtoOAGetTrendbarsReq) pra
# ATIVOS_CTRADER (XAUUSD, EURUSD) — a sessão FIX (`ctrader_loader.py`) não
# tem chamada de histórico, só cotação/execução ao vivo, por isso essa é
# uma integração separada, com protocolo (protobuf sobre Twisted) e
# autenticação (OAuth2) diferentes da FIX.
#
# ATENÇÃO Twisted: o reactor global só roda uma vez por processo — depois
# de `reactor.stop()` uma nova chamada a `reactor.run()` no MESMO processo
# levanta `ReactorNotRestartable`. Por isso `buscar_historico()` abaixo
# processa TODOS os pares (ativo, symbol_name) pedidos numa única sessão/
# `reactor.run()`, nunca uma por ativo. Isso é seguro pro uso via
# `run_backtest.py` (processo novo a cada invocação); NÃO reusar essa
# função dentro do processo de dashboard.py de vida longa sem revisar esse
# ponto primeiro (precisaria rodar em processo/thread separada com seu
# próprio reactor, ex: via `crochet` ou subprocess).

import datetime as dt

import pandas as pd
from twisted.internet import reactor

from ctrader_open_api import Client, TcpProtocol, EndPoints, Auth
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAAccountAuthReq,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOASymbolsListReq,
    ProtoOAGetTrendbarsReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbarPeriod

import credenciais_ctrader as cred

PERIODO_M5 = ProtoOATrendbarPeriod.M5

# Limite documentado da Open API pra período M1-M5: 302400000 ms (5 semanas)
# entre fromTimestamp/toTimestamp — requisições maiores precisam ser
# fatiadas em múltiplas chamadas.
JANELA_MAX_M5 = dt.timedelta(milliseconds=302_400_000)


def _montar_auth() -> Auth:
    cfg = cred.CTRADER_OPENAPI
    return Auth(cfg["client_id"], cfg["client_secret"], cfg["redirect_uri"])


def gerar_url_autorizacao(scope: str = "trading") -> str:
    """URL que Jesué precisa abrir no navegador DELE (login/consentimento
    manual — nunca automatizável por aqui) pra conceder acesso ao app."""
    return _montar_auth().getAuthUri(scope=scope)


def trocar_code_por_token(code: str) -> dict:
    """Troca o `code` (colado por Jesué depois do consentimento) pelos
    tokens. Não persiste sozinho — ver `ctrader_openapi_autorizar.py`."""
    return _montar_auth().getToken(code)


def renovar_token(refresh_token: str) -> dict:
    return _montar_auth().refreshToken(refresh_token)


def _host() -> str:
    return (EndPoints.PROTOBUF_LIVE_HOST if cred.CTRADER_OPENAPI["ambiente"] == "live"
            else EndPoints.PROTOBUF_DEMO_HOST)


class _SessaoHistorico:
    """Uma sessão = uma conexão Open API = um `reactor.run()`. Processa uma
    lista de (ativo, symbol_name) em sequência, sempre M5, sempre o mesmo
    período `desde`/`ate` pra todos."""

    def __init__(self, pares: list[tuple[str, str]], desde: dt.datetime, ate: dt.datetime):
        self.pares = list(pares)
        self.desde = desde
        self.ate = ate
        self.resultado: dict[str, pd.DataFrame] = {}
        self.erro: str | None = None
        self.client = None
        self._symbol_ids: dict[str, int] = {}
        self._fila: list[tuple[str, str]] = []
        self._ativo_atual: str | None = None
        self._candles_atual: list = []
        self._janelas: list[tuple[dt.datetime, dt.datetime]] = []

    def executar(self) -> dict[str, pd.DataFrame]:
        cfg = cred.CTRADER_OPENAPI
        if not cfg.get("access_token") or not cfg.get("ctid_trader_account_id"):
            raise RuntimeError(
                "credenciais_ctrader.py: CTRADER_OPENAPI ainda sem access_token/"
                "ctid_trader_account_id — rode ctrader_openapi_autorizar.py primeiro."
            )
        self.client = Client(_host(), EndPoints.PROTOBUF_PORT, TcpProtocol)
        self.client.setConnectedCallback(self._ao_conectar)
        self.client.startService()
        reactor.run()
        if self.erro:
            raise RuntimeError(f"ctrader_openapi_loader: {self.erro}")
        return self.resultado

    # -- cadeia de callbacks (Twisted Deferred) --------------------------

    def _ao_conectar(self, client):
        req = ProtoOAApplicationAuthReq()
        req.clientId = cred.CTRADER_OPENAPI["client_id"]
        req.clientSecret = cred.CTRADER_OPENAPI["client_secret"]
        client.send(req).addCallbacks(self._apos_app_auth, self._falhou)

    def _apos_app_auth(self, msg):
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = cred.CTRADER_OPENAPI["ctid_trader_account_id"]
        req.accessToken = cred.CTRADER_OPENAPI["access_token"]
        self.client.send(req).addCallbacks(self._apos_account_auth, self._falhou)

    def _apos_account_auth(self, msg):
        req = ProtoOASymbolsListReq()
        req.ctidTraderAccountId = cred.CTRADER_OPENAPI["ctid_trader_account_id"]
        self.client.send(req).addCallbacks(self._apos_symbols, self._falhou)

    def _apos_symbols(self, msg):
        nomes_procurados = {nome for _, nome in self.pares}
        for s in msg.symbol:
            if s.symbolName in nomes_procurados:
                self._symbol_ids[s.symbolName] = s.symbolId
        faltando = nomes_procurados - set(self._symbol_ids)
        if faltando:
            self.erro = f"Simbolo(s) nao encontrado(s) na conta cTrader: {faltando}"
            self._finalizar()
            return
        self._fila = list(self.pares)
        self._proximo_par()

    def _proximo_par(self):
        if not self._fila:
            self._finalizar()
            return
        self._ativo_atual, symbol_name = self._fila.pop(0)
        self._candles_atual = []
        self._janelas = self._gerar_janelas(self.desde, self.ate)
        self._proxima_janela(symbol_name)

    @staticmethod
    def _gerar_janelas(desde, ate):
        janelas = []
        cursor = desde
        while cursor < ate:
            fim = min(cursor + JANELA_MAX_M5, ate)
            janelas.append((cursor, fim))
            cursor = fim
        return janelas

    def _proxima_janela(self, symbol_name):
        if not self._janelas:
            self.resultado[self._ativo_atual] = self._montar_dataframe(self._candles_atual)
            self._proximo_par()
            return
        ini, fim = self._janelas.pop(0)
        req = ProtoOAGetTrendbarsReq()
        req.ctidTraderAccountId = cred.CTRADER_OPENAPI["ctid_trader_account_id"]
        req.period = PERIODO_M5
        req.fromTimestamp = int(ini.timestamp() * 1000)
        req.toTimestamp = int(fim.timestamp() * 1000)
        req.symbolId = self._symbol_ids[symbol_name]
        (self.client.send(req, responseTimeoutInSeconds=30)
         .addCallbacks(lambda msg: self._apos_trendbars(msg, symbol_name), self._falhou))

    def _apos_trendbars(self, msg, symbol_name):
        self._candles_atual.extend(msg.trendbar)
        self._proxima_janela(symbol_name)

    @staticmethod
    def _montar_dataframe(trendbars) -> pd.DataFrame:
        linhas = []
        for tb in trendbars:
            low = tb.low / 100_000
            linhas.append({
                "datetime": pd.Timestamp(tb.utcTimestampInMinutes * 60, unit="s", tz="UTC").tz_localize(None),
                "open": low + tb.deltaOpen / 100_000,
                "high": low + tb.deltaHigh / 100_000,
                "low": low,
                "close": low + tb.deltaClose / 100_000,
                "fonte": "ctrader_openapi",
            })
        if not linhas:
            return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "fonte"])
        return (pd.DataFrame(linhas)
                .drop_duplicates(subset="datetime")
                .sort_values("datetime")
                .reset_index(drop=True))

    def _falhou(self, failure):
        self.erro = str(failure)
        self._finalizar()

    def _finalizar(self):
        if reactor.running:
            reactor.stop()


def buscar_historico(pares: list[tuple[str, str]], desde: dt.datetime, ate: dt.datetime) -> dict[str, pd.DataFrame]:
    """
    `pares`: lista de (ativo, symbol_name) — normalmente iguais
    (`[("XAUUSD","XAUUSD"), ("EURUSD","EURUSD")]`), separados só porque o
    nome do símbolo na cTrader pode um dia divergir do nome interno do SQ.
    Candle M5, mesmo formato usado por `merge_fontes.py`
    (`datetime, open, high, low, close, fonte`).
    """
    return _SessaoHistorico(pares, desde, ate).executar()
