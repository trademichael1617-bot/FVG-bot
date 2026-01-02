import os
import telebot
import pandas as pd
import pandas_ta as ta
import websocket
import json
import threading
import time
from datetime import datetime

# ================== SETTINGS ==================
# Load all configurations from Environment Variables
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SSID = os.getenv("PO_SSID")

# Validation check
if not all([TOKEN, CHAT_ID, SSID]):
    print("❌ ERROR: Missing one or more Environment Variables!")
else:
    bot = telebot.TeleBot(TOKEN)
    print("✅ Bot configurations loaded successfully.")
    
# ================== GLOBALS ==================
market_history = {}
MAX_HISTORY = 100

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
    st = ta.supertrend(df['high'], df['low'], df['close'], length=5, multiplier=2)
    # logic: Price breaks recent high + RSI > 50
    if df['close'].iloc[-1] > df['high'].iloc[-5:-1].max() and df['rsi_10'].iloc[-1] > 55:
        send_master_signal(symbol, "Breakout Strategy", payout)

    # --- STRATEGY 2: SMC (RSI 50 CONFIRMED) ---
    df["rsi"] = ta.rsi(df["close"], length=10)
    rsi_now = df["rsi"].iloc[-1]
    bullish_fvg = df["low"].iloc[-1] > df["high"].iloc[-3]
    if (48 <= rsi_now <= 52) and bullish_fvg:
        send_master_signal(symbol, "SMC Strategy", payout)

    # --- STRATEGY 3: INDICATOR ANALYSIS ONE ---
    stoch = ta.stoch(df['high'], df['low'], df['close'], k=5, d=3)
    macd = ta.macd(df['close'])
    if stoch['STOCHk_5_3_3'].iloc[-1] > 80 and macd['MACDh_12_26_9'].iloc[-1] > 0:
        send_master_signal(symbol, "Indicator Analysis One", payout)

    # --- STRATEGY 4: INDICATOR ANALYSIS TWO ---
    df['sma100'] = ta.sma(df['close'], length=100)
    df['mom'] = ta.mom(df['close'], length=10)
    if df['close'].iloc[-1] > df['sma100'].iloc[-1] and df['mom'].iloc[-1] > df['mom'].iloc[-2]:
        send_master_signal(symbol, "Indicator Analysis Two", payout)

def send_master_signal(symbol, strategy, payout):
    msg = (f"🎯 **SIGNAL: {strategy}**\n"
           f"━━━━━━━━━━━━━━━\n"
           f"📈 Asset: {symbol}\n"
           f"💰 Payout: {payout}%\n"
           f"⏰ Expiry: 1 MIN\n"
           f"✅ Volume: Confirmed")
    bot.send_message(CHAT_ID, msg, parse_mode='Markdown')

# ================== SYSTEM UTILITIES ==================

def hourly_heartbeat():
    while True:
        time.sleep(3600)
        bot.send_message(CHAT_ID, "🟢 **BOT STATUS:** Active and Scanning.")

def on_message(ws, message):
    if not message.startswith("42"): return
    try:
        data = json.loads(message[2:])
        event, payload = data[0], data[1]

        if event == "success_auth":
            for asset in payload.get("assets", []):
                if asset.get("profit") == 92:
                    ws.send(f'42["subscribeCandles", {{"asset": "{asset["name"]}", "period": 60}}]')

        if event == "error" and "auth" in str(payload).lower():
            bot.send_message(CHAT_ID, "⚠️ **SSID EXPIRED!** Please update your session key.")

        if event == "candle":
            symbol = payload["asset"]
            candle = {"open": payload["open"], "high": payload["high"], "low": payload["low"], "close": payload["close"], "time": payload["time"]}
            if symbol not in market_history: market_history[symbol] = []
            
            if not market_history[symbol] or market_history[symbol][-1]["time"] != candle["time"]:
                market_history[symbol].append(candle)
            else:
                market_history[symbol][-1] = candle

            if len(market_history[symbol]) >= 30:
                analyze_all_strategies(symbol, pd.DataFrame(market_history[symbol]), 92)

    except Exception as e: print(f"Error: {e}")

def connect():
    while True:
        try:
            ws = websocket.WebSocketApp("wss://api.po.market/socket.io/?EIO=4&transport=websocket",
                                        on_message=on_message, header=[f"Cookie: SSID={SSID}"])
            ws.run_forever(ping_interval=25)
        except: time.sleep(5)

if __name__ == "__main__":
    bot.send_message(CHAT_ID, "🚀 **BOT STARTED:** Master Script is live.")
    threading.Thread(target=hourly_heartbeat, daemon=True).start()
    threading.Thread(target=connect, daemon=True).start()
    bot.infinity_polling()
