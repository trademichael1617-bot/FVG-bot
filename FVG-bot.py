import os
import logging
import threading
import time
import json
import pandas as pd
import requests
import functools
from flask import Flask
import telebot

from pocketoptionapi.stable_api import PocketOption  # SSID connection

# ================== LOGGING ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
print = functools.partial(print, flush=True)

# ================== CONFIG ==================
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SSID = os.getenv("PO_SSID")  # Your Pocket Option SSID
NEWS_URL = "https://script.google.com/macros/s/AKfycbzC0brtkaV6X4jWWRhAli14uCM7w_t-e_7Pom3A76CnCVn5afdUKUkMF3k7qbdZfIvFaw/exec"

bot = telebot.TeleBot(TOKEN)
account = PocketOption(SSID)
check_connect, msg = account.connect()
if not check_connect:
    logging.error(f"SSID connection failed: {msg}")
else:
    logging.info("Connected to Pocket Option via SSID.")

market_history = {}
stats = {"total": 0, "wins": 0, "losses": 0}
active_news_events = []
early_alerts = {}  # 7-second alerts
VOLATILITY_STATE = {}
LOW_VOL_THRESHOLD = 0.6
HIGH_VOL_THRESHOLD = 0.9
VOL_ALERT_COOLDOWN = 300

# ================== VOLATILITY ==================
def init_volatility_state(symbol):
    if symbol not in VOLATILITY_STATE:
        VOLATILITY_STATE[symbol] = {"enabled": True, "last_alert": 0}

def check_market_volatility(symbol, df):
    init_volatility_state(symbol)
    if len(df) < 50:
        return
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    atr = true_range.rolling(14).mean()
    if atr.isna().iloc[-1]:
        return
    curr_atr = atr.iloc[-1]
    avg_atr = atr.iloc[-30:].mean()
    ratio = curr_atr / avg_atr if avg_atr > 0 else 0
    now = time.time()

    if ratio < LOW_VOL_THRESHOLD and VOLATILITY_STATE[symbol]["enabled"]:
        if now - VOLATILITY_STATE[symbol]["last_alert"] > VOL_ALERT_COOLDOWN:
            VOLATILITY_STATE[symbol]["enabled"] = False
            VOLATILITY_STATE[symbol]["last_alert"] = now
            bot.send_message(
                CHAT_ID,
                f"🛑 **LOW VOLATILITY**\nTrading paused for {symbol}\nATR Ratio: {ratio:.2f}",
                parse_mode="Markdown"
            )
    elif ratio >= HIGH_VOL_THRESHOLD and not VOLATILITY_STATE[symbol]["enabled"]:
        if now - VOLATILITY_STATE[symbol]["last_alert"] > VOL_ALERT_COOLDOWN:
            VOLATILITY_STATE[symbol]["enabled"] = True
            VOLATILITY_STATE[symbol]["last_alert"] = now
            bot.send_message(
                CHAT_ID,
                f"✅ **VOLATILITY RESTORED**\nTrading resumed for {symbol}\nATR Ratio: {ratio:.2f}",
                parse_mode="Markdown"
            )

# ================== NEWS FILTER ==================
def is_asset_blocked(symbol):
    if len(symbol) < 6:
        return False
    clean = symbol.replace("_otc", "")
    base, quote = clean[:3], clean[3:6]
    blocked = base in active_news_events or quote in active_news_events
    if blocked:
        logging.info(f"{symbol} blocked due to news: {active_news_events}")
    return blocked

def update_news_calendar():
    global active_news_events
    while True:
        try:
            r = requests.get(NEWS_URL, timeout=15)
            active_news_events = r.json()
            logging.info(f"News updated: {active_news_events}")
        except Exception as e:
            logging.error(f"News fetch error: {e}")
        time.sleep(300)

# ================== INDICATORS ==================
def compute_macd(series: pd.Series, fast=12, slow=26, signal=9):
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"MACD": macd_line, "Signal": signal_line, "MACDh": hist})

def compute_stochastic(df, k_period=5, d_period=3):
    lowest_low = df['low'].rolling(k_period).min()
    highest_high = df['high'].rolling(k_period).max()
    k = 100 * (df['close'] - lowest_low) / (highest_high - lowest_low)
    d = k.rolling(d_period).mean()
    return pd.DataFrame({"K": k, "D": d})

def compute_rsi(series, period=10):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# ================== STRATEGIES ==================
def analyze_all_strategies(symbol, df):
    if len(df) < 100: return
    curr_price = df["close"].iloc[-1]
    init_volatility_state(symbol)
    check_market_volatility(symbol, df)
    if not VOLATILITY_STATE[symbol]["enabled"]: return
    if is_asset_blocked(symbol): return

    # --- S1: FVG Flip ---
    df["rsi10"] = compute_rsi(df["close"], 10)
    rsi = df["rsi10"].iloc[-1]
    rsi_align = 45 <= rsi <= 55
    if df["low"].iloc[-2] > df["high"].iloc[-4] and rsi_align:
        schedule_early_alert(symbol, "S1: FVG FLIP", "UP 🟢", curr_price)
        return
    if df["high"].iloc[-2] < df["low"].iloc[-4] and rsi_align:
        schedule_early_alert(symbol, "S1: FVG FLIP", "DOWN 🔴", curr_price)
        return

    # --- S2: Triangle Breakout ---
    atr = (df['high'] - df['low']).rolling(14).mean()
    range_ = df["high"].iloc[-15:-1].max() - df["low"].iloc[-15:-1].min()
    if range_ < atr.iloc[-1] * 1.2:
        recent_high = df["high"].iloc[-15:-1].max()
        recent_low = df["low"].iloc[-15:-1].min()
        macd = compute_macd(df["close"])
        mh = macd["MACDh"].iloc[-1]
        st_dir = 1 if curr_price > df["close"].iloc[-2] else -1
        if curr_price > recent_high and rsi_align and mh > 0 and st_dir == 1:
            schedule_early_alert(symbol, "S2: TRIANGLE BREAKOUT", "UP 🟢", curr_price)
            return
        if curr_price < recent_low and rsi_align and mh < 0 and st_dir == -1:
            schedule_early_alert(symbol, "S2: TRIANGLE BREAKOUT", "DOWN 🔴", curr_price)
            return

    # --- S3: Sync Scalp ---
    stoch = compute_stochastic(df)
    k = stoch["K"].iloc[-1]
    d = stoch["D"].iloc[-1]
    macd_s3 = compute_macd(df["close"], fast=5, slow=13, signal=6)
    mh_s3 = macd_s3["MACDh"].iloc[-1]
    df["rsi7"] = compute_rsi(df["close"], 7)
    rsi7 = df["rsi7"].iloc[-1]
    if k < 20 and k > d and mh_s3 > 0 and rsi7 < 30:
        schedule_early_alert(symbol, "S3: SYNC SCALP", "UP 🟢", curr_price)
        return
    if k > 80 and k < d and mh_s3 < 0 and rsi7 > 70:
        schedule_early_alert(symbol, "S3: SYNC SCALP", "DOWN 🔴", curr_price)
        return

# --- S4: Trend Rider + MOM ---
def analyze_trend_rider(symbol, df):
    curr_price = df["close"].iloc[-1]
    sma100 = df["close"].rolling(100).mean()
    st_dir = 1 if curr_price > df["close"].iloc[-2] else -1
    mom = df["close"].diff(10)
    curr_mom, prev_mom = mom.iloc[-1], mom.iloc[-2]
    touching = abs(curr_price - sma100.iloc[-1]) / sma100.iloc[-1] < 0.001
    if touching and st_dir == 1 and curr_mom > prev_mom and curr_mom > 0:
        schedule_early_alert(symbol, "S4: TREND RIDER + MOM", "UP 🟢", curr_price)
    if touching and st_dir == -1 and curr_mom < prev_mom and curr_mom < 0:
        schedule_early_alert(symbol, "S4: TREND RIDER + MOM", "DOWN 🔴", curr_price)

# ================== EARLY ALERTS ==================
def schedule_early_alert(symbol, strategy, direction, entry_price):
    early_alerts[symbol] = {"strategy": strategy, "direction": direction, "entry": entry_price, "timer": time.time()}
    bot.send_message(CHAT_ID, f"⏱ 7s ALERT: {symbol} | {strategy}")
    threading.Timer(7, resolve_early_alert, args=[symbol]).start()

def resolve_early_alert(symbol):
    if symbol not in market_history:
        return
    df = market_history[symbol]
    curr_price = df["close"].iloc[-1]
    alert = early_alerts.get(symbol)
    if not alert:
        return
    if abs(curr_price - alert["entry"]) / alert["entry"] > 0.05:
        bot.send_message(CHAT_ID, f"❌ Signal LOST: {symbol} | {alert['strategy']}")
        early_alerts.pop(symbol)
        return
    bot.send_message(CHAT_ID, f"✅ Signal READY: {symbol} | {alert['strategy']} | {alert['direction']}")
    early_alerts.pop(symbol)

# ================== WEBSOCKET ==================
def on_message(ws, message):
    try:
        if not message.startswith('42["candles"'):
            return
        payload = json.loads(message[2:])[1]
        asset = payload["asset"]
        df = pd.DataFrame(payload["candles"])
        df.rename(columns={"o":"open","c":"close","h":"high","l":"low","v":"volume"}, inplace=True)
        market_history[asset] = df.tail(100)
        analyze_all_strategies(asset, df)
        analyze_trend_rider(asset, df)
    except Exception as e:
        logging.error(f"WS message error: {e}")

def connect_ws():
    while True:
        try:
            import websocket
            ws = websocket.WebSocketApp(
                "wss://api-c.po.market/socket.io/?EIO=4&transport=websocket",
                on_message=on_message,
                header=[f"Cookie: SSID={SSID}"]
            )
            ws.run_forever(ping_interval=25, ping_timeout=10)
        except Exception as e:
            logging.error(f"WebSocket error: {e}, reconnecting in 5s...")
            time.sleep(5)

# ================== FLASK KEEP ALIVE ==================
app = Flask("keep_alive")
@app.route("/")
def home():
    return "Bot is running!"

# ================== START BOT ==================
if __name__ == "__main__":
    threading.Thread(target=update_news_calendar, daemon=True).start()
    bot.send_message(CHAT_ID, "✅ BOT ONLINE — ALL SYSTEMS STABLE")
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=5000)).start()
    connect_ws()
