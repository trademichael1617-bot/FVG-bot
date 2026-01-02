import os
import telebot
import pandas as pd
import pandas_ta as ta
import websocket
import json
import threading
import time
from datetime import datetime
from flask import Flask

# ================== RENDER STAY-ALIVE SERVER ==================
app = Flask(__name__)

@app.route('/')
def home():
    return f"Bot is running! Last Check: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ================== SETTINGS & AUTH ==================
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SSID = os.getenv("PO_SSID")

if not all([TOKEN, CHAT_ID, SSID]):
    print("❌ ERROR: Missing Environment Variables!")
    exit()

bot = telebot.TeleBot(TOKEN)
market_history = {}

# ================== STRATEGY ENGINE ==================

def analyze_all_strategies(symbol, df, payout):
    if len(df) < 30: return

    # 1. UNIVERSAL VOLUME FILTER
    df["body_size"] = abs(df["close"] - df["open"])
    df["vol_avg"] = df["body_size"].rolling(5).mean()
    if df["body_size"].iloc[-1] < df["vol_avg"].iloc[-1] * 1.2:
        return

    # --- STRATEGY 1: TRIANGULAR BREAKOUT ---
    df['rsi_10'] = ta.rsi(df['close'], length=10)
    if df['close'].iloc[-1] > df['high'].iloc[-5:-1].max() and df['rsi_10'].iloc[-1] > 55:
        send_master_signal(symbol, "Breakout Strategy", payout, "UP 🟢")

    # --- STRATEGY 2: SMC (RSI 50 ALIGNMENT) ---
    df["rsi"] = ta.rsi(df["close"], length=10)
    rsi_now = df["rsi"].iloc[-1]
    
    # Bullish FVG + RSI Support at 50
    bullish_fvg = df["low"].iloc[-1] > df["high"].iloc[-3]
    if (49 <= rsi_now <= 53) and bullish_fvg:
        send_master_signal(symbol, "SMC CALL (RSI 50 Support)", payout, "UP 🟢")

    # Bearish FVG + RSI Resistance at 50
    bearish_fvg = df["high"].iloc[-1] < df["low"].iloc[-3]
    if (47 <= rsi_now <= 51) and bearish_fvg:
        send_master_signal(symbol, "SMC PUT (RSI 50 Resistance)", payout, "DOWN 🔴")

    # --- STRATEGY 3: INDICATOR ANALYSIS ONE ---
    stoch = ta.stoch(df['high'], df['low'], df['close'], k=5, d=3)
    macd = ta.macd(df['close'])
    if stoch['STOCHk_5_3_3'].iloc[-1] > 80 and macd['MACDh_12_26_9'].iloc[-1] > 0:
        send_master_signal(symbol, "Indicator Analysis One", payout, "UP 🟢")

    # --- STRATEGY 4: INDICATOR ANALYSIS TWO ---
    df['sma100'] = ta.sma(df['close'], length=100)
    df['mom'] = ta.mom(df['close'], length=10)
    if df['close'].iloc[-1] > df['sma100'].iloc[-1] and df['mom'].iloc[-1] > df['mom'].iloc[-2]:
        send_master_signal(symbol, "Indicator Analysis Two", payout, "UP 🟢")

def send_master_signal(symbol, strategy, payout, direction):
    msg = (f"🎯 **SIGNAL: {strategy}**\n"
           f"━━━━━━━━━━━━━━━\n"
           f"📈 Asset: {symbol}\n"
           f"🧭 Direction: {direction}\n"
           f"💰 Payout: {payout}%\n"
           f"⏰ Expiry: 1 MIN\n"
           f"✅ Volume: Confirmed")
    try:
        bot.send_message(CHAT_ID, msg, parse_mode='Markdown')
    except Exception as e:
        print(f"Telegram Error: {e}")

# ================== SYSTEM UTILITIES ==================

def on_message(ws, message):
    if not message.startswith("42"): return
    try:
        data = json.loads(message[2:])
        event, payload = data[0], data[1]

        if event == "success_auth":
            for asset in payload.get("assets", []):
                if asset.get("profit") == 92:
                    ws.send(f'42["subscribeCandles", {{"asset": "{asset["name"]}", "period": 60}}]')

        if event == "candle":
            symbol = payload["asset"]
            candle = {"open": payload["open"], "high": payload["high"], "low": payload["low"], "close": payload["close"], "time": payload["time"]}
            if symbol not in market_history: market_history[symbol] = []
            
            if not market_history[symbol] or market_history[symbol][-1]["time"] != candle["time"]:
                market_history[symbol].append(candle)
                if len(market_history[symbol]) > 100: market_history[symbol].pop(0)
            else:
                market_history[symbol][-1] = candle

            if len(market_history[symbol]) >= 30:
                analyze_all_strategies(symbol, pd.DataFrame(market_history[symbol]), 92)
    except: pass

def connect():
    while True:
        try:
            ws = websocket.WebSocketApp("wss://api.po.market/socket.io/?EIO=4&transport=websocket",
                                      on_message=on_message, header=[f"Cookie: SSID={SSID}"])
            ws.run_forever(ping_interval=25)
        except: time.sleep(5)

# ================== MAIN EXECUTION ==================

if __name__ == "__main__":
    # Start Flask
    threading.Thread(target=run_web_server, daemon=True).start()
    
    # Start Pocket Option Connection
    threading.Thread(target=connect, daemon=True).start()
    
    print("🚀 Bot is running...")
    # Use infinity_polling ONCE with skip_pending
    bot.infinity_polling(skip_pending=True)
