# Passo único (não repete): troca o `code` da autorização OAuth2 (colado
# por Jesué depois de aprovar no navegador dele) pelos tokens de acesso, e
# descobre o ctidTraderAccountId da conta 6112712 pra gravar em
# credenciais_ctrader.py. Depois disso, ctrader_openapi_loader.py já
# funciona sozinho (o refresh_token não expira).
#
# Uso: python ctrader_openapi_autorizar.py <code>

import sys

from twisted.internet import reactor

from ctrader_open_api import Client, TcpProtocol, EndPoints
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq,
    ProtoOAGetAccountListByAccessTokenReq,
)

import credenciais_ctrader as cred
import ctrader_openapi_loader as openapi

if len(sys.argv) != 2:
    print("Uso: python ctrader_openapi_autorizar.py <code>")
    sys.exit(1)

code = sys.argv[1]

print("[1/2] Trocando code por access_token/refresh_token...")
tokens = openapi.trocar_code_por_token(code)
if "accessToken" not in tokens:
    print("Falha na troca de token:", tokens)
    sys.exit(1)

access_token = tokens["accessToken"]
refresh_token = tokens.get("refreshToken")
print(f"    access_token obtido (expira em {tokens.get('expiresIn')}s), refresh_token obtido.")

print("[2/2] Descobrindo ctidTraderAccountId das contas vinculadas a esse token...")

host = (EndPoints.PROTOBUF_LIVE_HOST if cred.CTRADER_OPENAPI["ambiente"] == "live"
        else EndPoints.PROTOBUF_DEMO_HOST)
client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
contas_encontradas = []
erro = {"msg": None}


def ao_conectar(client):
    req = ProtoOAApplicationAuthReq()
    req.clientId = cred.CTRADER_OPENAPI["client_id"]
    req.clientSecret = cred.CTRADER_OPENAPI["client_secret"]
    client.send(req).addCallbacks(apos_app_auth, falhou)


def apos_app_auth(msg):
    req = ProtoOAGetAccountListByAccessTokenReq()
    req.accessToken = access_token
    client.send(req).addCallbacks(apos_lista_contas, falhou)


def apos_lista_contas(msg):
    for conta in msg.ctidTraderAccount:
        contas_encontradas.append({
            "ctidTraderAccountId": conta.ctidTraderAccountId,
            "traderLogin": conta.traderLogin,
            "isLive": conta.isLive,
        })
    parar()


def falhou(failure):
    erro["msg"] = str(failure)
    parar()


def parar():
    if reactor.running:
        reactor.stop()


client.setConnectedCallback(ao_conectar)
client.startService()
reactor.run()

if erro["msg"]:
    print("Erro:", erro["msg"])
    sys.exit(1)

print(f"\n{len(contas_encontradas)} conta(s) encontrada(s) nesse token:")
for c in contas_encontradas:
    print(f"  ctidTraderAccountId={c['ctidTraderAccountId']}  "
          f"traderLogin={c['traderLogin']}  isLive={c['isLive']}")

print("\naccess_token:", access_token)
print("refresh_token:", refresh_token)
print("\nPróximo passo: gravar esses 3 valores em credenciais_ctrader.py "
      "(CTRADER_OPENAPI['access_token'/'refresh_token'/'ctid_trader_account_id']), "
      "usando a conta com traderLogin == 6112712.")
