import ccxt
import pandas as pd
import numpy as np
import pytz

# Conectando com a Binance
exchange = ccxt.binance()

def fetch_ohlcv_data(pair, timeframe):
    limit = 1000  # Limite máximo por chamada

    # Primeira chamada para a API
    first_ohlcv = exchange.fetch_ohlcv(pair, timeframe, limit=limit)
    
    # Segunda chamada para a API, pegando dados anteriores à primeira vela da primeira chamada
    last_timestamp = first_ohlcv[0][0]  # Timestamp da primeira vela da primeira chamada
    second_ohlcv = exchange.fetch_ohlcv(pair, timeframe, limit=limit, since=last_timestamp - limit * exchange.parse_timeframe(timeframe) * 1000)
    
    # Concatenando os dois conjuntos de dados
    ohlcv = second_ohlcv + first_ohlcv
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    
    # Configurando fuso horário para UTC e convertendo para UTC-3
    df['timestamp'] = df['timestamp'].dt.tz_localize(pytz.utc).dt.tz_convert('America/Sao_Paulo')
    
    return df.drop_duplicates(subset='timestamp').reset_index(drop=True)

def calculate_ema(df, period=200):
    ema = df['close'].ewm(span=period, adjust=False).mean()
    return ema

def calculate_zigzag(df, deviation=1, pivot_legs=200):
    highs = df['high']
    lows = df['low']
    length = len(df)
    
    # Inicializando as listas de topos e fundos
    tops = []
    bottoms = []
    
    last_pivot_price = None
    last_pivot_index = -pivot_legs
    trend = 0

    for i in range(pivot_legs, length):
        if i < length - pivot_legs:
            window_high = max(highs[i - pivot_legs:i + pivot_legs + 1])
            window_low = min(lows[i - pivot_legs:i + pivot_legs + 1])
        else:
            window_high = max(highs[i - pivot_legs:])
            window_low = min(lows[i - pivot_legs:])

        if trend != 1 and highs[i] == window_high:
            if last_pivot_price is None or highs[i] > last_pivot_price * (1 + deviation / 100):
                last_pivot_price = highs[i]
                last_pivot_index = i
                trend = 1
                tops.append((i, highs[i]))

        elif trend != -1 and lows[i] == window_low:
            if last_pivot_price is None or lows[i] < last_pivot_price * (1 - deviation / 100):
                last_pivot_price = lows[i]
                last_pivot_index = i
                trend = -1
                bottoms.append((i, lows[i]))

    return tops, bottoms

def main():
    while True:
        pair = input("Digite a paridade (ex: 'BTC/USDT') ou 'sair' para encerrar: ")
        if pair.lower() == 'sair':
            break

        timeframe = input("Digite o intervalo de tempo (ex: '1d', '1h', '5m'): ")

        try:
            ohlcv_data = fetch_ohlcv_data(pair, timeframe)
            ema_data = calculate_ema(ohlcv_data, 200)
            tops, bottoms = calculate_zigzag(ohlcv_data, deviation=1, pivot_legs=200)

            ohlcv_data['EMA_200'] = ema_data
            
            print(f"Dados das 2000 velas para {pair} no intervalo de tempo {timeframe}:")
            print(ohlcv_data[['timestamp', 'close', 'EMA_200']].tail())

            print(f"\nTopos identificados: {len(tops)}")
            for top in tops:
                print(f"Índice: {top[0]}, Valor: {top[1]}")

            print(f"\nFundos identificados: {len(bottoms)}")
            for bottom in bottoms:
                print(f"Índice: {bottom[0]}, Valor: {bottom[1]}")

        except Exception as e:
            print(f"Erro: {e}")

if __name__ == "__main__":
    main()
