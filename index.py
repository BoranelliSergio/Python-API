import os
from flask import Flask, jsonify, request
from flask_cors import CORS
import ccxt
import pandas as pd
import numpy as np
import pytz
import requests
from threading import Thread, Lock
import time
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


# Inicializando Flask e CORS (para evitar problemas de Cross-Origin Resource Sharing)
app = Flask(__name__)
CORS(app)

# Conectando com a Binance
exchange = ccxt.binance()

# Variáveis globais
is_monitoring_active = False
monitoring_lock = Lock()
monitoring_threads = []  # Lista para manter as threads
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

def calculate_zigzag(df, deviation=0.1, pivot_legs=300, max_points=10):
    highs = df['high']
    lows = df['low']
    length = len(df)

    tops = []
    bottoms = []
    
    last_pivot_price = None
    last_pivot_index = -pivot_legs
    trend = 0

    for i in range(pivot_legs, length):
        # Janelas para determinar topos e fundos
        if i < length - pivot_legs:
            window_high = max(highs[i - pivot_legs:i + pivot_legs + 1])
            window_low = min(lows[i - pivot_legs:i + pivot_legs + 1])
        else:
            window_high = max(highs[i - pivot_legs:])
            window_low = min(lows[i - pivot_legs:])

        # Identificação dos topos
        if trend != 1 and highs[i] == window_high:
            if last_pivot_price is None or highs[i] > last_pivot_price * (1 + deviation / 100):
                last_pivot_price = highs[i]
                last_pivot_index = i
                trend = 1
                tops.append((i, highs[i]))

        # Identificação dos fundos
        elif trend != -1 and lows[i] == window_low:
            if last_pivot_price is None or lows[i] < last_pivot_price * (1 - deviation / 100):
                last_pivot_price = lows[i]
                last_pivot_index = i
                trend = -1
                bottoms.append((i, lows[i]))

    # Limitar o número de pontos a serem retornados
    tops = tops[-max_points:]
    bottoms = bottoms[-max_points:]

    return tops, bottoms


def format_zigzag_for_chart(tops, bottoms, df):
    zigzag_points = []
    for index, value in tops:
        time_iso = df['timestamp'][index].isoformat()
        print("Exemplo de timestamp ISO 8601:", time_iso)  # Imprimindo para depuração
        zigzag_points.append({'time': time_iso, 'value': value, 'type': 'top'})
    for index, value in bottoms:
        time_iso = df['timestamp'][index].isoformat()
        zigzag_points.append({'time': time_iso, 'value': value, 'type': 'bottom'})
    zigzag_points.sort(key=lambda x: x['time'])
    return zigzag_points

def format_bottoms_for_table(bottoms, df):
    formatted_bottoms = []
    for index, value in bottoms:
        timestamp = df['timestamp'][index]
        formatted_time = timestamp.strftime('%d/%m/%Y %H:%M')
        formatted_bottoms.append({
            'index': index,
            'value': value,
            'formatted_time': formatted_time
        })
    return formatted_bottoms

def send_email(subject, body, to_address):
    sender_address = 'grupoboranellimonitoramento@zohomail.com'
    sender_pass = 'PnGRDNNvHXWB'  # Senha de aplicativo gerada

    # Configuração do MIMEMultipart
    message = MIMEMultipart()
    message['From'] = sender_address
    message['To'] = to_address
    message['Subject'] = subject
    message.attach(MIMEText(body, 'plain'))

    # Estabelecendo conexão com o servidor
    session = smtplib.SMTP('smtp.zoho.com', 587)  # Usar 465 para SSL
    session.starttls()  # Habilitar segurança
    session.login(sender_address, sender_pass)  # Login com credenciais

    # Enviar o e-mail e fechar a conexão
    text = message.as_string()
    session.sendmail(sender_address, to_address, text)
    session.quit()

    print(f"Email enviado para {to_address} com sucesso.")
def start_monitoring(pairs, timeframe):
    global is_monitoring_active
    print(f"Alertas ativados para {pairs} no {timeframe}")

    # Enviar mensagem inicial de monitoramento
    send_alert_activation_message(pairs, timeframe)

    while is_monitoring_active:
        for pair in pairs.split(';'):
            pair = pair.strip()
            try:
                ohlcv_data = fetch_ohlcv_data(pair, timeframe)
                tops, bottoms = calculate_zigzag(ohlcv_data, deviation=1, pivot_legs=200)
                print_monitoring_data(pair, tops, bottoms, ohlcv_data)

                if len(bottoms) > 0 and bottoms[-1][0] == len(ohlcv_data) - 1:
                    new_bottom = bottoms[-1]
                    timestamp = ohlcv_data['timestamp'][new_bottom[0]]
                    formatted_time = timestamp.strftime('%d/%m/%Y %H:%M')
                    value = new_bottom[1]
                    send_new_bottom_alert(pair, value, formatted_time)
            except Exception as e:
                print(f"Erro ao monitorar {pair}: {e}")

        # Dormir até o próximo fechamento de vela
        sleep_time = exchange.parse_timeframe(timeframe) * 1000 - (int(time.time() * 1000) % (exchange.parse_timeframe(timeframe) * 1000))
        time.sleep(sleep_time / 1000)


monitoring_logs = []
def print_monitoring_data(pair, tops, bottoms, ohlcv_data):
    log_message = f"Monitorando {pair}:\nÚltimos Topos e Fundos:\n"

    if tops:
        log_message += "Topos:\n" + "\n".join([f"Índice {top[0]}, Valor {top[1]}" for top in tops[-3:]])
    else:
        log_message += "Topos: Nenhum\n"

    if bottoms:
        log_message += "\nFundos:\n" + "\n".join([f"Índice {bottom[0]}, Valor {bottom[1]}" for bottom in bottoms[-3:]])
    else:
        log_message += "\nFundos: Nenhum\n"

    last_close = ohlcv_data.iloc[-1]['close']
    log_message += f"\nValor do fechamento da última vela: {last_close}"

    print(log_message)  # Imprimir no console
    monitoring_logs.append(log_message)  # Adicionar aos logs

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'ok'}), 200
@app.route('/api/v1/monitoring_logs', methods=['GET'])
def get_monitoring_logs():
    return jsonify({'logs': monitoring_logs})

@app.route('/api/v1/deactivate_alert', methods=['POST'])
def deactivate_alert():
    global is_monitoring_active, monitoring_threads

    is_monitoring_active = False
    for thread in monitoring_threads:
        if thread.is_alive():
            thread.join()
    monitoring_threads.clear()

    print('Monitoramento Encerrado')
    return jsonify({'status': 'success', 'message': 'Alertas desativados'})

@app.route('/api/v1/monitoring_status', methods=['GET'])
def get_monitoring_status():
    global is_monitoring_active  # Declare a variável como global
    return jsonify({'is_active': is_monitoring_active})

def send_new_bottom_alert(pair, value, time):
    chat_id = 5045523503
    message = f"*Grupo Boranelli - Monitoramento de Criptomoedas - COMPRA*\n\n{pair} Compra: {value} Horario: {time}"
    image_path = 'images/AlertaCompra.png'  # Caminho da imagem
    # Enviar e-mail
    subject = "Novo Fundo Encontrado"
    body = f"Novo fundo para {pair} encontrado: {value} no horário: {time}"
    send_email(subject, body, "grupo.boranelli@gmail.com")
    send_telegram_photo(chat_id, message, image_path)

def send_telegram_photo(chat_id, message, image_path):
    token = '6827875802:AAEZvm9RA4sRuPP30ef5X9o2B-mga9pYNx0'
    url = f'https://api.telegram.org/bot{token}/sendPhoto'
    files = {'photo': open(image_path, 'rb')}
    data = {
        'chat_id': chat_id,
        'caption': message,
        'parse_mode': 'Markdown'
    }
    response = requests.post(url, files=files, data=data)
    return response.json()

def send_alert_activation_message(pairs, timeframe):
    chat_id = 5045523503
    message = f"*Grupo Boranelli - Monitoramento de Criptomoedas - ALERTA ATIVADO*\n\nParidades selecionadas: {pairs}, Tempo Gráfico: {timeframe}. Alerta ATIVADO"
    image_path = 'images/AlertaAtivado.png'  # Caminho da imagem
    send_telegram_photo(chat_id, message, image_path)

@app.route('/api/v1/activate_alert', methods=['POST'])
def activate_alert():
    global is_monitoring_active, monitoring_threads

    if is_monitoring_active:
        return jsonify({'status': 'error', 'message': 'Monitoramento já está ativo'}), 400

    data = request.json
    pairs = data['pairs']
    timeframe = data['timeframe']
    is_monitoring_active = True

    pairs_list = pairs.split(';')
    chunk_size = 25
    for i in range(0, len(pairs_list), chunk_size):
        chunk = pairs_list[i:i + chunk_size]
        thread = Thread(target=start_monitoring, args=(';'.join(chunk), timeframe))
        thread.start()
        monitoring_threads.append(thread)

    return jsonify({'status': 'success', 'message': 'Alertas ativados'})

# Rota para obter os dados formatados dos fundos
@app.route('/api/v1/bottoms', methods=['GET'])
def get_formatted_bottoms():
    pair = request.args.get('pair', default='BTC/USDT', type=str)
    timeframe = request.args.get('timeframe', default='1d', type=str)

    try:
        ohlcv_data = fetch_ohlcv_data(pair, timeframe)
        _, bottoms = calculate_zigzag(ohlcv_data, deviation=1, pivot_legs=200)
        formatted_bottoms = format_bottoms_for_table(bottoms, ohlcv_data)

        return jsonify({'bottoms': formatted_bottoms})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
@app.route('/api/v1/data', methods=['GET'])
def get_data():
    pair = request.args.get('pair', default='BTC/USDT', type=str)
    timeframe = request.args.get('timeframe', default='1d', type=str)

    try:
        ohlcv_data = fetch_ohlcv_data(pair, timeframe)
        ema_data = calculate_ema(ohlcv_data, 200)
        tops, bottoms = calculate_zigzag(ohlcv_data, deviation=1, pivot_legs=200)

        zigzag_points = format_zigzag_for_chart(tops, bottoms, ohlcv_data)
       
        ohlcv_data['EMA_200'] = ema_data
        data = ohlcv_data[['timestamp', 'open', 'high', 'low', 'close', 'volume', 'EMA_200']].to_dict('records')

        result = {
            'data': data,
            'ema_info': ema_data.iloc[-1],
            'zigzag_points': zigzag_points,
            'zigzag_info': {
                'tops_count': len(tops),
                'bottoms_count': len(bottoms),
                'tops': tops,
                'bottoms': bottoms
            }
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
@app.route('/api/v1/pairs', methods=['GET'])
def get_pairs():
    markets = exchange.load_markets()
    pairs = [market for market in markets if 'USDT' in market and markets[market]['active']]
    return jsonify({'pairs': pairs[:100]})  # Retorna apenas as primeiras 100 paridades


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))  # Use a porta do ambiente, caso exista, senão use 5000
    app.run(host='0.0.0.0', port=port, debug=False)