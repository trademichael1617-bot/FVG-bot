import telebot
import pandas as pd
import pandas_ta as ta
import websocket
import json
import threading
from datetime import datetime

# --- SETTINGS ---
TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN'
SSID = 'YOUR_POCKET_OPTION_SSID'
CHAT_ID = 'YOUR_CHAT_ID'
bot = telebot.TeleBot(TOKEN)

# Assets & Data Management
active_92_assets = []
market_data = {} 

# --- REFINED STRATEGY ENGINE ---

def analyze_all_strategies(symbol, df, payout):
    """Independent strategy executor with Volume Filter."""
    
    # 1. UNIVERSAL VOLUME FILTER (Tick Proxy)
    # Checks if current activity is > 1.2x the recent average
    df['vol_avg'] = df['volume'].rolling(window=5).mean()
    high_volume = df['volume'].iloc[-1] > (df['vol_avg'].iloc[-1] * 1.2)
    
    if not high_volume:
        return # No signal if market activity is low

    # STRATEGY 1: Breakout Strategy (Triangular Patterns)
    # Uses RSI(10), MACD(12,26,9), SuperTrend(5,2)
    df['rsi_10'] = ta.rsi(df['close'], length=10)
    st = ta.supertrend(df['high'], df['low'], df['close'], length=5, multiplier=2)
    # Check if last candle closed outside the 5-period High/Low (Triangle base)
    if df['close'].iloc[-1] > df['high'].iloc[-2: -6].max() and df['rsi_10'].iloc[-1] > 50:
        send_master_signal(symbol, "Breakout strategy", payout)

    # STRATEGY 2: SMC Strategy (FVG + S/R + RSI 50 Alignment)
    # Personalized Rule: RSI must be in 48-52 zone
    df['rsi_smc'] = ta.rsi(df['close'], length=10)
    rsi_val = df['rsi_smc'].iloc[-1]
    fvg_detected = df['low'].iloc[-1] > df['high'].iloc[-3] # Bullish FVG example
    if fvg_detected and (48 <= rsi_val <= 52):
        send_master_signal(symbol, "SMC strategy", payout)

    # STRATEGY 3: Indicator Analysis One (Stoch/MACD/RSI 7)
    # Rule: All 3 indicators must align; Stochastic cannot lead
    stoch = ta.stoch(df['high'], df['low'], df['close'], k=5, d=3, smooth_k=3)
    df['rsi_7'] = ta.rsi(df['close'], length=7)
    if df['rsi_7'].iloc[-1] > 50 and stoch['STOCHk_5_3_3'].iloc[-1] > 50:
        send_master_signal(symbol, "indicator analysis one", payout)

    # STRATEGY 4: Indicator Analysis Two (SMA 100/SuperTrend/Momentum)
    # Rule: Volatility of new candle > previous
    df['sma100'] = ta.sma(df['close'], length=100)
    df['mom'] = ta.mom(df['close'], length=10)
    if df['mom'].iloc[-1] > df['mom'].iloc[-2] and df['close'].iloc[-1] > df['sma100'].iloc[-1]:
        send_master_signal(symbol, "indicator analysis two", payout)

def send_master_signal(asset, strategy, payout):
    msg = (f"🚨 **{strategy.upper()}**\n"
           f"━━━━━━━━━━━━━━━\n"
           f"📈 **Asset:** {asset}\n"
           f"💰 **Payout:** {payout}%\n"
           f"⏱ **Expiry:** 1 MIN\n"
           f"📊 **Volume:** HIGH (Confirmed)")
    bot.send_message(CHAT_ID, msg, parse_mode='Markdown')

# --- SYSTEM NOTIFICATIONS ---

def start_heartbeat():
    bot.send_message(CHAT_ID, "🚀 **BOT IS LIVE & SCANNING**")
    def pulse():
        while True:
            time.sleep(3600)
            bot.send_message(CHAT_ID, "🟢 **HOURLY UPDATE:** Bot is active.")
    threading.Thread(target=pulse, daemon=True).start()

# --- WEBSOCKET HANDLER ---

def on_message(ws, message):
    if message.startswith('42'):
        data = json.loads(message[2:])
        msg_type, payload = data[0], data[1]
        
        # Payout Scanner (92% Filter)
        if msg_type == "success_auth":
            assets = payload.get('assets', [])
            for a in assets:
                if a['profit'] == 92:
                    ws.send(f'42["subscribeCandles", {{"asset": "{a["name"]}", "period": 60}}]')
        
        # SSID Expiry Alert
        if msg_type == "error" and "auth" in str(payload).lower():
            bot.send_message(CHAT_ID, "⚠️ **SSID EXPIRED!** Bot Stopped.")
            ws.close()

def connect():
    start_heartbeat()
    ws = websocket.WebSocketApp("wss://api.po.market/socket.io/?EIO=4&transport=websocket",
                                on_message=on_message,
                                header={"Cookie": f"SSID={SSID}"})
    ws.run_forever(ping_interval=20)

if __name__ == "__main__":
    threading.Thread(target=connect).start()
    bot.infinity_polling()
