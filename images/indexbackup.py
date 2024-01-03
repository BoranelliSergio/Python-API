import ccxt
import pandas as pd
import numpy as np

# Conectando com a Binance
exchange = ccxt.binance()

def fetch_ohlcv_data(pair, timeframe):
    limit = 1000
    ohlcv = exchange.fetch_ohlcv(pair, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def calculate_ema(df, period=200):
    ema = df['close'].ewm(span=period, adjust=False).mean()
    return ema

def is_reversal(current_price, last_pivot, deviation):
    return abs(current_price - last_pivot) / last_pivot >= deviation / 100

def calculate_zigzag(df, deviation=1, pivot_legs=200):
    highs = df['high']
    lows = df['low']
    pivot_indexes = [-pivot_legs]  # Initialize with a dummy index for first pivot_legs number of data points
    pivot_values = [0]
    pivot_is_high = False

    for i in range(pivot_legs, len(df) - pivot_legs + 1):
        window_highs = highs[i - pivot_legs:i + 1]
        window_lows = lows[i - pivot_legs:i + 1]
        max_high = window_highs.max()
        min_low = window_lows.min()

        if pivot_is_high:
            if lows[i] <= min_low and is_reversal(min_low, pivot_values[-1], deviation):
                pivot_indexes.append(i)
                pivot_values.append(min_low)
                pivot_is_high = False
        else:
            if highs[i] >= max_high and is_reversal(max_high, pivot_values[-1], deviation):
                pivot_indexes.append(i)
                pivot_values.append(max_high)
                pivot_is_high = True

    # Adding last pivot
    if pivot_is_high:
        pivot_values.append(highs.iloc[-1])
    else:
        pivot_values.append(lows.iloc[-1])
    pivot_indexes.append(len(df) - 1)

    return pivot_indexes, pivot_values

def main():
    while True:
        pair = input("Digite a paridade (ex: 'BTC/USDT') ou 'sair' para encerrar: ")
        if pair.lower() == 'sair':
            break

        timeframe = input("Digite o intervalo de tempo (ex: '1d', '1h', '5m'): ")

        try:
            ohlcv_data = fetch_ohlcv_data(pair, timeframe)
            ema_data = calculate_ema(ohlcv_data, 200)
            pivot_indexes, pivot_values = calculate_zigzag(ohlcv_data, deviation=1, pivot_legs=200)

            ohlcv_data['EMA_200'] = ema_data
            
            print(f"Dados das 1000 velas para {pair} no intervalo de tempo {timeframe}:")
            print(ohlcv_data)

            print(f"\nPontos de pivô do Zig Zag:")
            for index, value in zip(pivot_indexes, pivot_values):
                print(f"Índice: {index}, Valor: {value}")

        except Exception as e:
            print(f"Erro: {e}")

if __name__ == "__main__":
    main()
