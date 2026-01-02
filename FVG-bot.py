import os
import telebot
import pandas as pd
import pandas_ta as ta
import websocket
import json
import threading
import time
import requests
from datetime import datetime, timedelta
from flask import Flask

# ================== RENDER STAY-ALIVE SERVER ==================
app = Flask(__name__)
@app.route('/')
def home():
    return f"Bot Active | WR: {calculate_win_rate()}% | Tracking: {len(active_news_events)} News Blocks"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ================== SETTINGS & GLOBAL STATS ==================
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SSID = os.getenv("PO_SSID")

bot = telebot.TeleBot(TOKEN)
market_history = {}
stats = {"total": 0, "wins": 0, "losses": 0}
pending_trades = []
active_news_events = [] # Stores current currency blocks

# ================== FOREX FACTORY & CURRENCY FILTER ==================

def update_news_calendar():
    """Fetches news and sets specific currency-based block windows."""
    global active_news_events
    while True:
        try:
            response = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
            events = response.json()
            now = datetime.utcnow()
            
            new_blocks = []
            for event in events:
                if event['impact'] in ["High", "Medium"]:
                    # Parse event time
                    e_time = datetime.strptime(event['date'], "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
                    
                    # BLOCK WINDOW: 5 mins before to 15 mins after
                    start_block = e_time - timedelta(minutes=5)
                    end_block = e_time + timedelta(minutes=15)
                    
                    if start_block <= now <= end_block:
                        new_blocks.append(event['country']) # e.g., 'USD', 'EUR'
            
            active_news_events = list(set(new_blocks)) # Unique list of blocked currencies
        except: pass
        time.sleep(60) # Refresh news list every minute

def is_asset_blocked(symbol):
    """Checks if the asset's currencies are currently in a news block."""
    for currency in active_news_events:
        if currency in symbol: # e.g., if 'USD' is in 'EURUSD'
            return True
    return False

# ================== STRATEGY ENGINE ==================

def analyze_all_strategies(symbol, df, payout):
    # NEWS FILTER: Blocks only relevant assets
    if is_asset_blocked(symbol): 
        return

    # TREND FILTER (EMA 50)
    df["ema50"] = ta.ema(df["close"], length=50)
    current_price = df["close"].iloc[-1]
    trend = "UP" if current_price > df["ema50"].iloc[-1] else "DOWN"

    df["rsi"] = ta.rsi(df["close"], length=10)
    rsi_now = df["rsi"].iloc[-1]
    
    # 1. SMC BULLISH (Buy)
    bullish_fvg = df["low"].iloc[-1] > df["high"].iloc[-3]
    if (49 <= rsi_now <= 53) and bullish_fvg and trend == "UP":
        send_master_signal(symbol, "SMC CALL", payout, "UP 🟢")

    # 2. SMC BEARISH (Sell)
    bearish_fvg = df["high"].iloc[-1] < df["low"].iloc[-3]
    if (47 <= rsi_now <= 51) and bearish_fvg and trend == "DOWN":
        send_master_signal(symbol, "SMC PUT", payout, "DOWN 🔴")

    # --- Add other balanced strategies here ---

# ================== RESULTS & TRACKING ==================

def send_master_signal(symbol, strategy, payout, direction):
    global stats
    stats["total"] += 1
    msg = (f"🎯 **SIGNAL: {strategy}**\n"
           f"━━━━━━━━━━━━━━━\n"
           f"📈 Asset: {symbol} | {direction}\n"
           f"💰 WR: {calculate_win_rate()}% | Payout: {payout}%\n"
           f"⏰ Expiry: 1 MIN")
    bot.send_message(CHAT_ID, msg, parse_mode='Markdown')
    
    pending_trades.append({
        "symbol": symbol, "entry": market_history[symbol][-1]["close"],
        "dir": direction, "expiry": datetime.now() + timedelta(minutes=1)
    })

def check_results():
    global stats
    while True:
        time.sleep(5)
        now = datetime.now()
        for t in pending_trades[:]:
            if now >= t["expiry"]:
                curr = market_history[t["symbol"]][-1]["close"]
                win = (curr > t["entry"] if "UP" in t["dir"] else curr < t["entry"])
                if win: stats["wins"] += 1
                else: stats["losses"] += 1
                pending_trades.remove(t)

def calculate_win_rate():
    return round((stats["wins"] / stats["total"] * 100), 1) if stats["total"] > 0 else 0

# ================== EXECUTION ==================

def on_message(ws, message):
    if not message.startswith("42"): return
    try:
        data = json.loads(message[2:])
        event, payload = data[0], data[1]
        if event == "candle":
            s = payload["asset"]
            c = {"close": payload["close"], "high": payload["high"], "low": payload["low"], "time": payload["time"]}
            if s not in market_history: market_history[s] = []
            if not market_history[s] or market_history[s][-1]["time"] != c["time"]:
                market_history[s].append(c)
                if len(market_history[s]) > 100: market_history[s].pop(0)
                analyze_all_strategies(s, pd.DataFrame(market_history[s]), 92)
            else: market_history[s][-1] = c
    except: pass

def connect():
    while True:
        try:
            ws = websocket.WebSocketApp("wss://api.po.market/socket.io/?EIO=4&transport=websocket",
                                      on_message=on_message, header=[f"Cookie: SSID={SSID}"])
            ws.run_forever(ping_interval=25)
        except: time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    threading.Thread(target=update_news_calendar, daemon=True).start()
    threading.Thread(target=check_results, daemon=True).start()
    threading.Thread(target=connect, daemon=True).start()
    bot.infinity_polling(skip_pending=True)
