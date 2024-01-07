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
import psycopg2

# Inicializando Flask e CORS (para evitar problemas de Cross-Origin Resource Sharing)
app = Flask(__name__)
CORS(app)

# Conectando com a Binance
exchange = ccxt.binance()

# Variáveis globais
is_monitoring_active = False
monitoring_lock = Lock()
monitoring_threads = []  # Lista para manter as threads
latest_bottoms = []
def fetch_ohlcv_data(pair, timeframe, count=2000):
    limit = 1000  # Limite máximo por chamada
    all_ohlcv = []
    
    while count > 0:
        # A primeira chamada não terá parâmetro 'since'
        since = None if not all_ohlcv else all_ohlcv[0][0] - limit * exchange.parse_timeframe(timeframe) * 1000
        ohlcv = exchange.fetch_ohlcv(pair, timeframe, since=since, limit=min(limit, count))
        all_ohlcv = ohlcv + all_ohlcv
        count -= len(ohlcv)  # Decrementa pelo número de velas efetivamente retornadas

        # Verifica se a chamada retornou menos velas do que o limite, o que indica que chegamos ao começo dos dados
        if len(ohlcv) < limit:
            break
    
    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    
    # Configurando fuso horário para UTC e convertendo para UTC-3
    df['timestamp'] = df['timestamp'].dt.tz_localize(pytz.utc).dt.tz_convert('America/Sao_Paulo')
    
    return df.drop_duplicates(subset='timestamp').reset_index(drop=True)

def calculate_ema(df, period=200):
    ema = df['close'].ewm(span=period, adjust=False).mean()
    return ema
def calculate_zigzag(df, deviation=1, pivot_legs=5):
    highs = df['high']
    lows = df['low']
    length = len(df)
    
    tops = []
    bottoms = []
    
    last_pivot_price = None
    last_pivot_index = -pivot_legs
    trend = 0

    for i in range(pivot_legs, length - pivot_legs):
        window_high = max(highs[i - pivot_legs:i + pivot_legs])
        window_low = min(lows[i - pivot_legs:i + pivot_legs])

        if highs[i] == window_high and (last_pivot_price is None or highs[i] > last_pivot_price * (1 + deviation / 100)):
            if trend != 1 or last_pivot_index != i - pivot_legs:
                last_pivot_price = highs[i]
                last_pivot_index = i
                trend = 1
                tops.append((i, highs[i]))

        if lows[i] == window_low and (last_pivot_price is None or lows[i] < last_pivot_price * (1 - deviation / 100)):
            if trend != -1 or last_pivot_index != i - pivot_legs:
                last_pivot_price = lows[i]
                last_pivot_index = i
                trend = -1
                bottoms.append((i, lows[i]))
    
    # Processar o restante das velas após a última janela
    for i in range(length - pivot_legs, length):
        if trend == 1 and highs[i] > last_pivot_price * (1 + deviation / 100):
            last_pivot_price = highs[i]
            last_pivot_index = i
            tops.append((i, highs[i]))
        elif trend == -1 and lows[i] < last_pivot_price * (1 - deviation / 100):
            last_pivot_price = lows[i]
            last_pivot_index = i
            bottoms.append((i, lows[i]))
            
    return tops, bottoms

def format_zigzag_for_chart(tops, bottoms, df):
    zigzag_points = []
    for index, value in tops + bottoms:
        # Converter para Unix timestamp considerando o fuso horário UTC-3 São Paulo
        timestamp = int(df['timestamp'][index].tz_localize(None).timestamp())
        point_type = 'top' if (index, value) in tops else 'bottom'
        zigzag_points.append({'time': timestamp, 'value': value, 'type': point_type})
    
    # Certifique-se de que os pontos estejam em ordem cronológica
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
                tops, bottoms = calculate_zigzag(ohlcv_data, deviation=1, pivot_legs=5)
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
        log_message += "Topos:\n"
        for top in tops[-3:]:  # Considerando os últimos 3 topos
            index, value = top
            time = ohlcv_data['timestamp'][index].strftime('%d/%m/%Y %H:%M:%S')
            log_message += f"Índice {index}, Valor {value}, Horário {time} UTC-3\n"
    else:
        log_message += "Topos: Nenhum\n"

    if bottoms:
        log_message += "\nFundos:\n"
        for bottom in bottoms[-3:]:  # Considerando os últimos 3 fundos
            index, value = bottom
            time = ohlcv_data['timestamp'][index].strftime('%d/%m/%Y %H:%M:%S')
            log_message += f"Índice {index}, Valor {value}, Horário {time} UTC-3\n"
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
    global latest_bottoms
    chat_id = 5045523503
    message = f"*Grupo Boranelli - Monitoramento de Criptomoedas - COMPRA*\n\n{pair} Compra: {value} Horario: {time}"
    image_path = 'images/AlertaCompra.png'  # Caminho da imagem
    # Enviar e-mail
    subject = "Novo Fundo Encontrado"
    body = f"Novo fundo para {pair} encontrado: {value} no horário: {time}"
    send_email(subject, body, "grupo.boranelli@gmail.com")
    send_telegram_photo(chat_id, message, image_path)
    # Adicione o novo fundo à lista
    latest_bottoms.append({
        'pair': pair,
        'value': value,
        'time': time
    })
     # Opcionalmente, limite o tamanho da lista para evitar crescimento infinito
    latest_bottoms = latest_bottoms[-10:]  # Mantenha apenas os 10 fundos mais recentes

     # Conectar ao banco de dados e inserir o alerta
    conn = psycopg2.connect("postgresql://BoranelliSergio:VjU6WIsS4HJD@ep-fancy-fire-69370269.eu-central-1.aws.neon.tech/boranellidb?sslmode=require")
    cur = conn.cursor()
    cur.execute("INSERT INTO fund_alerts (pair, value) VALUES (%s, %s)", (pair, value))
    conn.commit()
    cur.close()
    conn.close()
    
@app.route('/api/v1/fetch_alerts', methods=['GET'])
def fetch_alerts():
    conn = psycopg2.connect("postgresql://BoranelliSergio:VjU6WIsS4HJD@ep-fancy-fire-69370269.eu-central-1.aws.neon.tech/boranellidb?sslmode=require")
    cur = conn.cursor()
    cur.execute("SELECT * FROM fund_alerts WHERE is_shown = FALSE")
    alerts = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify({'alerts': alerts})
   
@app.route('/api/v1/latest_bottoms', methods=['GET'])
def get_latest_bottoms():
    global latest_bottoms
    return jsonify({'latest_bottoms': latest_bottoms})

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
        _, bottoms = calculate_zigzag(ohlcv_data, deviation=1, pivot_legs=5)
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
            tops, bottoms = calculate_zigzag(ohlcv_data, deviation=1, pivot_legs=5)

            ohlcv_data['EMA_200'] = ema_data
            
            print(f"Dados das 10000 velas para {pair} no intervalo de tempo {timeframe}:")
            print(ohlcv_data[['timestamp', 'close', 'EMA_200']].tail())

            print(f"\nTopos identificados: {len(tops)}")
            for top in tops:
                timestamp = ohlcv_data.iloc[top[0]]['timestamp']
                print(f"Índice: {top[0]}, Valor: {top[1]}, Horário: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

            print(f"\nFundos identificados: {len(bottoms)}")
            for bottom in bottoms:
                timestamp = ohlcv_data.iloc[bottom[0]]['timestamp']
                print(f"Índice: {bottom[0]}, Valor: {bottom[1]}, Horário: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

        except Exception as e:
            print(f"Erro: {e}")

@app.route('/api/v1/data', methods=['GET'])
def get_data():
    pair = request.args.get('pair', default='BTC/USDT', type=str)
    timeframe = request.args.get('timeframe', default='1d', type=str)

    try:
        ohlcv_data = fetch_ohlcv_data(pair, timeframe)
        ema_data = calculate_ema(ohlcv_data, 200)
        tops, bottoms = calculate_zigzag(ohlcv_data, deviation=1, pivot_legs=5)

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
    stablecoins = ['USDC', 'BUSD', 'DAI', 'TUSD', 'PAX']
    pairs = [market for market in markets if 'USDT' in market and not any(stablecoin in market for stablecoin in stablecoins) and markets[market]['active']]
    return jsonify({'pairs': pairs})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))  # Use a porta do ambiente, caso exista, senão use 5000
    app.run(host='0.0.0.0', port=port, debug=False)