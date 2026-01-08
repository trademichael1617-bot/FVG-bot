import os, telebot, pandas as pd, pandas_ta as ta
import websocket, json, threading, time, requests, functools, logging
from flask import Flask

# ================== LOGGING ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
print = functools.partial(print, flush=True)

# ================== SYSTEM CONFIG ==================
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SSID = os.getenv("PO_SSID")
NEWS_URL = "https://script.google.com/macros/s/AKfycbzC0brtkaV6X4jWWRhAli14uCM7w_t-e_7Pom3A76CnCVn5afdUKUkMF3k7qbdZfIvFaw/exec"

bot = telebot.TeleBot(TOKEN)

market_history = {}
stats = {"total": 0, "wins": 0, "losses": 0}
active_news_events = []
last_signal_time = {}
early_alerts = {}  # 7-second alert tracking

# ================== VOLATILITY ==================
VOLATILITY_STATE = {}  # per-symbol
LOW_VOL_THRESHOLD = 0.6
HIGH_VOL_THRESHOLD = 0.9
VOL_ALERT_COOLDOWN = 300

def init_volatility_state(symbol):
    if symbol not in VOLATILITY_STATE:
        VOLATILITY_STATE[symbol] = {"enabled": True, "last_alert": 0}

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
            logging.info(f"News calendar updated: {active_news_events}")
        except Exception as e:
            logging.error(f"News Update Error: {e}")
        time.sleep(300)

# ================== VOLATILITY CHECK ==================
def check_market_volatility(symbol, df):
    init_volatility_state(symbol)
    if len(df) < 50:
        return

    atr = ta.atr(df["high"], df["low"], df["close"], 14)
    if atr is None or atr.isna().iloc[-1]:
        return

    curr_atr = atr.iloc[-1]
    avg_atr = atr.iloc[-30:].mean()
    ratio = curr_atr / avg_atr if avg_atr > 0 else 0
    now = time.time()

    # Low Volatility — Pause this currency
    if ratio < LOW_VOL_THRESHOLD and VOLATILITY_STATE[symbol]["enabled"]:
        if now - VOLATILITY_STATE[symbol]["last_alert"] > VOL_ALERT_COOLDOWN:
            VOLATILITY_STATE[symbol]["enabled"] = False
            VOLATILITY_STATE[symbol]["last_alert"] = now
            bot.send_message(
                CHAT_ID,
                f"🛑 **LOW VOLATILITY DETECTED**\n"
                f"📉 Trading paused for {symbol}\n"
                f"📊 ATR Ratio: {ratio:.2f}",
                parse_mode="Markdown"
            )
            logging.warning(f"{symbol} PAUSED — LOW VOLATILITY | ATR {ratio:.2f}")

    # High Volatility — Resume this currency
    elif ratio >= HIGH_VOL_THRESHOLD and not VOLATILITY_STATE[symbol]["enabled"]:
        if now - VOLATILITY_STATE[symbol]["last_alert"] > VOL_ALERT_COOLDOWN:
            VOLATILITY_STATE[symbol]["enabled"] = True
            VOLATILITY_STATE[symbol]["last_alert"] = now
            bot.send_message(
                CHAT_ID,
                f"✅ **VOLATILITY RESTORED**\n"
                f"📈 Trading resumed for {symbol}\n"
                f"📊 ATR Ratio: {ratio:.2f}",
                parse_mode="Markdown"
            )
            logging.info(f"{symbol} RESUMED — VOLATILITY NORMAL | ATR {ratio:.2f}")

# ================== STRATEGY ENGINE ==================
def analyze_all_strategies(symbol, df):
    if len(df) < 100:
        logging.info(f"{symbol} skipped: not enough candles ({len(df)})")
        return
    curr_price = df["close"].iloc[-1]
    init_volatility_state(symbol)
    check_market_volatility(symbol, df)

    if not VOLATILITY_STATE[symbol]["enabled"]:
        logging.info(f"{symbol} skipped: low volatility")
        return
    if is_asset_blocked(symbol):
        return

    # ------------------- STRATEGIES -------------------
    # S1: FVG Flip
    df["rsi10"] = ta.rsi(df["close"], 10)
    rsi = df["rsi10"].iloc[-1]
    rsi_align = 45 <= rsi <= 55
    if df["low"].iloc[-2] > df["high"].iloc[-4] and rsi_align:
        schedule_early_alert(symbol, "S1: FVG FLIP", "UP 🟢", curr_price)
        return
    if df["high"].iloc[-2] < df["low"].iloc[-4] and rsi_align:
        schedule_early_alert(symbol, "S1: FVG FLIP", "DOWN 🔴", curr_price)
        return

    # S2: Triangle Breakout
    atr = ta.atr(df["high"], df["low"], df["close"], 14)
    if atr is None or atr.isna().iloc[-1]:
        return
    atr_val = atr.iloc[-1]
    range_ = df["high"].iloc[-15:-1].max() - df["low"].iloc[-15:-1].min()
    if range_ < atr_val * 1.2:
        recent_high = df["high"].iloc[-15:-1].max()
        recent_low = df["low"].iloc[-15:-1].min()
        macd = ta.macd(df["close"], 12, 26, 9)
        if macd is None or macd.empty:
            return
        mh = macd["MACDh_12_26_9"].iloc[-1]
        st = ta.supertrend(df["high"], df["low"], df["close"], length=5, multiplier=2)
        st_dir = st["SUPERTd_5_2"].iloc[-1]

        if curr_price > recent_high and rsi_align and mh > 0 and st_dir == 1:
            schedule_early_alert(symbol, "S2: TRIANGLE BREAKOUT", "UP 🟢", curr_price)
            return
        if curr_price < recent_low and rsi_align and mh < 0 and st_dir == -1:
            schedule_early_alert(symbol, "S2: TRIANGLE BREAKOUT", "DOWN 🔴", curr_price)
            return

    # S3: Sync Scalp
    stoch = ta.stoch(df["high"], df["low"], df["close"], 5, 3, 3)
    if stoch is None or stoch.empty:
        return
    k, d = stoch.iloc[-1, 0], stoch.iloc[-1, 1]
    macd_s3 = ta.macd(df["close"], fast=5, slow=13, signal=6)
    if macd_s3 is None or macd_s3.empty:
        return
    mh_s3 = macd_s3["MACDh_5_13_6"].iloc[-1]
    df["rsi7"] = ta.rsi(df["close"], 7)
    rsi7 = df["rsi7"].iloc[-1]

    if k < 20 and k > d and mh_s3 > 0 and rsi7 < 30:
        schedule_early_alert(symbol, "S3: SYNC SCALP", "UP 🟢", curr_price)
        return
    if k > 80 and k < d and mh_s3 < 0 and rsi7 > 70:
        schedule_early_alert(symbol, "S3: SYNC SCALP", "DOWN 🔴", curr_price)
        return

    # S4: Trend Rider + MOM
    sma100 = ta.sma(df["close"], 100)
    if sma100 is None or sma100.isna().iloc[-1]:
        return
    st = ta.supertrend(df["high"], df["low"], df["close"], length=5, multiplier=2)
    st_dir = st["SUPERTd_5_2"].iloc[-1]
    mom = ta.mom(df["close"], 10)
    if mom is None or mom.isna().iloc[-1] or len(mom) < 2:
        return
    curr_mom, prev_mom = mom.iloc[-1], mom.iloc[-2]
    touching = abs(curr_price - sma100.iloc[-1]) / sma100.iloc[-1] < 0.001
    mom_up = curr_mom > prev_mom and curr_mom > 0
    mom_down = curr_mom < prev_mom and curr_mom < 0

    if touching and st_dir == 1 and mom_up:
        schedule_early_alert(symbol, "S4: TREND RIDER + MOM", "UP 🟢", curr_price)
        return
    if touching and st_dir == -1 and mom_down:
        schedule_early_alert(symbol, "S4: TREND RIDER + MOM", "DOWN 🔴", curr_price)
        return

# ================== EARLY ALERT SYSTEM ==================
def schedule_early_alert(symbol, strategy, direction, entry_price):
    early_alerts[symbol] = {"strategy": strategy, "direction": direction, "entry": entry_price, "timer": time.time()}
    bot.send_message(CHAT_ID, f"⏱ 7s ALERT: {symbol} | {strategy}")
    # Start 7-second timer
    threading.Timer(7, resolve_early_alert, args=[symbol]).start()

def resolve_early_alert(symbol):
    if symbol not in market_history:
        return
    df = market_history[symbol]
    curr_price = df["close"].iloc[-1]
    alert = early_alerts.get(symbol)
    if not alert:
        return

    # Check if conditions still valid (simple price check for demonstration)
    if abs(curr_price - alert["entry"]) / alert["entry"] > 0.05:  # >5% deviation loses signal
        bot.send_message(CHAT_ID, f"❌ Signal LOST: {symbol} | {alert['strategy']}")
        logging.info(f"{symbol} | Early alert lost")
        early_alerts.pop(symbol)
        return

    # Send actual signal
    trigger_signal(symbol, alert["strategy"], alert["direction"], alert["entry"])
    early_alerts.pop(symbol)

# ================== SIGNAL & RESULTS ==================
def trigger_signal(symbol, strategy, direction, entry):
    last_signal_time[symbol] = time.time()
    bot.send_message(
        CHAT_ID,
        f"🎯 **{strategy}**\n📊 {symbol} | {direction}\n⏱ 1 MIN | Entry: {entry}",
        parse_mode="Markdown"
    )
    logging.info(f"Triggered: {strategy} | {symbol} | {direction} at {entry}")
    threading.Timer(60, check_result, args=[symbol, entry, direction]).start()

def check_result(symbol, entry_price, direction):
    df = market_history.get(symbol)
    if df is None or len(df) < 2:
        return
    exit_price = df["close"].iloc[-1]
    win = exit_price > entry_price if "UP" in direction else exit_price < entry_price
    stats["total"] += 1
    stats["wins"] += int(win)
    stats["losses"] += int(not win)
    wr = round(stats["wins"] / stats["total"] * 100, 1)
    bot.send_message(CHAT_ID, f"{'✅ WIN' if win else '❌ LOSS'} | {symbol}\nWR: {wr}%")
    logging.info(f"{symbol} | Trade result: {'WIN' if win else 'LOSS'} | Exit: {exit_price} | WR: {wr}%")

# ================== WEBSOCKET ==================
def on_message(ws, message):
    if not message.startswith('42["candles"'):
        return
    payload = json.loads(message[2:])[1]
    asset = payload["asset"]
    df = pd.DataFrame(payload["candles"])
    df.rename(columns={"o":"open","c":"close","h":"high","l":"low","v":"volume"}, inplace=True)
    market_history[asset] = df.tail(100)
    analyze_all_strategies(asset, market_history[asset])
    logging.info(f"{asset} updated with {len(df)} candles")

def connect():
    ws = websocket.WebSocketApp(
        "wss://api-c.po.market/socket.io/?EIO=4&transport=websocket",
        on_message=on_message,
        header=[f"Cookie: SSID={SSID}"]
    )
    logging.info("Connecting to WebSocket...")
    ws.run_forever(ping_interval=25, ping_timeout=10)

# ================== FLASK KEEP-ALIVE ==================
app = Flask("keep_alive")
@app.route("/")
def home():
    return "Bot is running!"

# ================== START ==================
if __name__ == "__main__":
    threading.Thread(target=update_news_calendar, daemon=True).start()
    bot.send_message(CHAT_ID, "✅ BOT ONLINE — ALL SYSTEMS STABLE")
    logging.info("Bot started, all systems stable.")
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000)).start()
    connect()
