# Teste de conectividade FIX com o cTrader (IC Markets, conta 6112712).
# So handshake de sessao (Logon/Logout) nas duas sessoes, QUOTE e TRADE.
# Nunca envia mensagem de ordem.

import ctrader_loader as ct

resultado = ct.testar_conectividade()

for nome, r in resultado.items():
    print(f"--- Sessao {nome.upper()} ({r['host']}:{r['port']}) ---")
    print("Sucesso:", r["sucesso"])
    if r["erro"]:
        print("Erro:", r["erro"])
    print("Mensagens trocadas:")
    for m in r["mensagens"]:
        print(" ", m)
    print()
