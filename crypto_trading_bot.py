# ESTE ES TU BOT DE TRADING AUTOMÁTICO

import os
import time
import logging
import pandas as pd
from datetime import datetime
import yfinance as yf
from telegram import Bot
import requests
import hmac
import hashlib
import base64
import json
import schedule
from dotenv import load_dotenv

# Carga las claves secretas
load_dotenv()

# Esto te dice qué está haciendo el bot
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# === CONFIGURACIÓN (PUEDES CAMBIAR ESTO) ===
CONFIG = {
    'cryptos': ['BTC-USD', 'ETH-USD'],
    'pionex_api_key': os.getenv('PIONEX_API_KEY'),
    'pionex_api_secret': os.getenv('PIONEX_API_SECRET'),
    'initial_balance_usdt': float(os.getenv('INITIAL_BALANCE_USDT', 1000.0)),
    'trade_quantity_btc': float(os.getenv('TRADE_QUANTITY_BTC', 0.001)),
    'trade_quantity_eth': float(os.getenv('TRADE_QUANTITY_ETH', 0.01)),
    'telegram_token': os.getenv('TELEGRAM_TOKEN'),
    'telegram_chat_id': os.getenv('TELEGRAM_CHAT_ID'),
    'rsi_buy_threshold': 30,
    'rsi_sell_threshold': 70,
    'sma_period': 20,
    'price_change_threshold': 0.02,
    'data_period': '60d',
    'data_interval': '1h',
    'poll_interval_minutes': 15,
    'report_interval_hours': 2,
}

# Verifica que no falten claves
required = ['PIONEX_API_KEY', 'PIONEX_API_SECRET', 'TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID']
for key in required:
    if not os.getenv(key):
        raise ValueError(f"Te falta: {key} en las variables de entorno")

# === FUNCIONES DE ANÁLISIS ===
def calculate_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_sma(prices, period):
    return prices.rolling(window=period).mean()

# === CONEXIÓN CON PIONEX ===
class PionexClient:
    def __init__(self):
        self.url = "https://api.pionex.com"
        self.key = CONFIG['pionex_api_key']
        self.secret = CONFIG['pionex_api_secret']

    def _sign(self, method, path, body=''):
        timestamp = int(time.time() * 1000)
        msg = f"{timestamp}{method}{path}{body}"
        signature = base64.b64encode(
            hmac.new(self.secret.encode(), msg.encode(), hashlib.sha256).digest()
        ).decode()
        return timestamp, signature

    def place_order(self, symbol, side, qty):
        body = json.dumps({
            'symbol': f"{symbol}USDT",
            'side': side,
            'type': 'MARKET',
            'quantity': str(qty)
        })
        try:
            timestamp, sig = self._sign('POST', '/api/v1/spot/order', body)
            headers = {
                'X-BB-APIKEY': self.key,
                'X-BB-SIGN': sig,
                'X-BB-TIMESTAMP': str(timestamp),
                'Content-Type': 'application/json'
            }
            r = requests.post(f"{self.url}/api/v1/spot/order", headers=headers, data=body)
            r.raise_for_status()
            data = r.json()
            logger.info(f"Trade: {side} {qty} {symbol}")
            return data
        except Exception as e:
            logger.error(f"Error trade: {e}")
            return None

    def get_balance(self):
        try:
            timestamp, sig = self._sign('GET', '/api/v1/spot/balance')
            headers = {
                'X-BB-APIKEY': self.key,
                'X-BB-SIGN': sig,
                'X-BB-TIMESTAMP': str(timestamp)
            }
            r = requests.get(f"{self.url}/api/v1/spot/balance", headers=headers)
            r.raise_for_status()
            data = r.json()
            usdt = next((x['free'] for x in data['data'] if x['asset'] == 'USDT'), 0)
            return float(usdt)
        except Exception as e:
            logger.error(f"Error balance: {e}")
            return 0.0

# === DATOS DEL MERCADO ===
class DataCollector:
    def get_data(self, symbol):
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=CONFIG['data_period'], interval=CONFIG['data_interval'])
            if df.empty:
                return None
            close = df['Close']
            current = close.iloc[-1]
            prev = close.iloc[-2] if len(close) > 1 else current
            change = (current - prev) / prev
            return {'data': df, 'current': current, 'change': change}
        except Exception as e:
            logger.error(f"Error datos {symbol}: {e}")
            return None

# === ANÁLISIS Y TRADING ===
class Analyzer:
    def __init__(self):
        self.collector = DataCollector()
        self.pionex = PionexClient()

    def analyze(self, symbol):
        data = self.collector.get_data(symbol)
        if not data:
            return None
        close = data['data']['Close']
        rsi = calculate_rsi(close).iloc[-1]
        sma = calculate_sma(close, CONFIG['sma_period']).iloc[-1]
        price = data['current']
        change = data['change']
        crypto = symbol.split('-')[0]
        qty = CONFIG[f'trade_quantity_{crypto.lower()}']

        alert = None
        if rsi < CONFIG['rsi_buy_threshold'] and price > sma and change > CONFIG['price_change_threshold']:
            alert = f"COMPRA {symbol}\nRSI: {rsi:.1f} | Precio: ${price:,.2f} | +{change:.2%}"
            self.pionex.place_order(crypto, 'BUY', qty)
        elif (rsi > CONFIG['rsi_sell_threshold'] or price < sma) and change < -CONFIG['price_change_threshold']:
            alert = f"VENTA {symbol}\nRSI: {rsi:.1f} | Precio: ${price:,.2f} | {change:.2%}"
            self.pionex.place_order(crypto, 'SELL', qty)

        logger.info(f"{symbol} | RSI: {rsi:.1f} | Precio: ${price:,.2f} | Cambio: {change:.2%}")
        return alert

# === NOTIFICACIONES ===
class Notifier:
    def __init__(self):
        self.bot = Bot(token=CONFIG['telegram_token'])

    def send(self, msg):
            if msg:
                try:
                    import asyncio
                    asyncio.run(self.bot.send_message(chat_id=CONFIG['telegram_chat_id'], text=msg))
                    logger.info(f"Enviado a Telegram: {msg}")
                except Exception as e:
                    logger.error(f"Error Telegram: {e}")

# === REPORTE DE GANANCIAS ===
def send_profit_report():
    pionex = PionexClient()
    balance = pionex.get_balance()
    initial = CONFIG['initial_balance_usdt']
    pnl = ((balance - initial) / initial) * 100
    msg = f"REPORTE DE GANANCIAS\nHora: {datetime.now().strftime('%H:%M')}\nBalance: ${balance:,.2f}\nGanancia: {pnl:+.2f}%"
    Notifier().send(msg)

# === TAREA PRINCIPAL ===
def run_analysis():
    logger.info("Iniciando análisis...")
    analyzer = Analyzer()
    notifier = Notifier()
    for crypto in CONFIG['cryptos']:
        alert = analyzer.analyze(crypto)
        notifier.send(alert)

# === INICIO DEL BOT ===
if __name__ == "__main__":
    logger.info("BOT INICIADO - TRADING 24/7")
    schedule.every(CONFIG['poll_interval_minutes']).minutes.do(run_analysis)
    schedule.every(2).hours.do(send_profit_report)  # Cada 2 horas
    run_analysis()  # Primera ejecución
    while True:
        schedule.run_pending()
        time.sleep(60)