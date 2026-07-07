import MetaTrader5 as mt5
import pandas as pd

# 1. Conectar ao MetaTrader 5 aberto na máquina
if not mt5.initialize():
    print("Falha ao inicializar o MT5. Verifique se a plataforma está aberta e conectada na sua conta.")
    mt5.shutdown()
    quit()

print("Conexão com MT5 estabelecida com sucesso!")

# 1. Busca apenas os ativos que estão visíveis na sua "Observação de Mercado" do MT5
symbols = mt5.symbols_get(group="*")
visible_symbols = [s.name for s in symbols if s.visible]

print(f"Ativos visíveis na sua Observação de Mercado: {visible_symbols}")

# 2. Você pode escolher o ativo da lista pelo nome, garantindo que ele exista
target_asset = "WINQ26" # Substitua pelo ticker que você quer monitorar

if target_asset in visible_symbols:
    ativo = target_asset
    print(f"Ativo '{ativo}' encontrado e pronto para extração.")
else:
    print(f"Erro: O ativo '{target_asset}' não está na sua lista de Observação de Mercado.")
    mt5.shutdown()
    quit()

# Tenta selecionar o ativo na janela de "Observação do Mercado" (Market Watch)
selecionado = mt5.symbol_select(ativo, True)
if not selecionado:
    print(f"Falha ao selecionar o ativo {ativo}. O mercado pode estar fechado ou o nome está incorreto.")
    mt5.shutdown()
    quit()

# 3. Puxar os últimos 10 candles do gráfico de 5 minutos (M5)
print(f"\nExtraindo dados de {ativo}...")
rates = mt5.copy_rates_from_pos(ativo, mt5.TIMEFRAME_M5, 0, 10)

if rates is None:
    print("Erro ao obter dados.")
else:
    # 4. Transformar os dados brutos em uma tabela limpa e estruturada (Pandas)
    df = pd.DataFrame(rates)
    # Converter o tempo do MT5 (segundos) para data e hora real:
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    print(f"\n--- Últimos 10 candles de {ativo} (Gráfico M5) ---")
    print(df[['time', 'open', 'high', 'low', 'close', 'tick_volume']])

# 5. Encerrar a conexão com o terminal
mt5.shutdown()