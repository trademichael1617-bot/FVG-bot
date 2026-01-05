import os, telebot, pandas as pd, pandas_ta as ta
import websocket, json, threading, time, requests, functools

print = functools.partial(print, flush=True)

# ================== SYSTEM CONFIG ==================
TOKEN = os.getenv("TELEGRAM_TOKEN", "8390314643:AAEU7UBAjTbM42J58klEIkBi83nt-uH5aB4")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003505344399")
SSID = os.getenv("PO_SSID", "abf1c651aa72419a6b77e0f5360f1f54")
NEWS_URL = "https://script.google.com/macros/s/AKfycbzC0brtkaV6X4jWWRhAli14uCM7w_t-e_7Pom3A76CnCVn5afdUKUkMF3k7qbdZfIvFaw/exec"

bot = telebot.TeleBot(TOKEN)

market_history = {}
stats = {"total": 0, "wins": 0, "losses": 0}
active_news_events = []
last_signal_time = {}

# ================== NEWS FILTER ==================
def is_asset_blocked(symbol):
    if len(symbol) < 6:
        return False
    base, quote = symbol[:3], symbol[3:]
    return base in active_news_events or quote in active_news_events

def update_news_calendar():
    global active_news_events
    while True:
        try:
            r = requests.get(NEWS_URL, timeout=15)
            active_news_events = r.json()
        except Exception as e:
            print(f"News Update Error: {e}")
        time.sleep(300)

# ================== STRATEGY ENGINE ==================
def analyze_all_strategies(symbol, df):
    if len(df) < 125 or is_asset_blocked(symbol):
        return

    now = time.time()
    if symbol in last_signal_time and now - last_signal_time[symbol] < 60:
        return

    curr_price = df["close"].iloc[-1]

    # ===== INDICATORS =====
    df["rsi7"] = ta.rsi(df["close"], 7)
    df["rsi10"] = ta.rsi(df["close"], 10)

    macd = ta.macd(df["close"], 12, 26, 9)
    if macd is None or macd.empty:
        return

    mh = macd.iloc[:, -1].iloc[-1]  # ✅ Correct histogram

    sync_bull = df["rsi7"].iloc[-1] > 50 and mh > 0
    sync_bear = df["rsi7"].iloc[-1] < 50 and mh < 0

    # ===== S1: FVG FLIP =====
    bull_fvg = df["low"].iloc[-2] > df["high"].iloc[-4]
    bear_fvg = df["high"].iloc[-2] < df["low"].iloc[-4]
    rsi_align = 48 <= df["rsi10"].iloc[-1] <= 52

    if bull_fvg and rsi_align and sync_bull:
        trigger_signal(symbol, "S1: FVG FLIP", "UP 🟢", curr_price)
        return
    if bear_fvg and rsi_align and sync_bear:
        trigger_signal(symbol, "S1: FVG FLIP", "DOWN 🔴", curr_price)
        return

    # ===== S2: TRIANGLE SYNC =====
    recent_h = df["high"].iloc[-15:-1]
    recent_l = df["low"].iloc[-15:-1]

    pk1, pk2 = recent_h[:7].max(), recent_h[7:].max()
    fl1, fl2 = recent_l[:7].min(), recent_l[7:].min()

    vol_ma = df["volume"].rolling(20).mean().iloc[-1]

    if abs(pk1 - pk2) < curr_price * 0.0002 and fl2 > fl1:
        if curr_price > pk2 and sync_bull and df["volume"].iloc[-1] > vol_ma:
            trigger_signal(symbol, "S2: TRIANGLE SYNC", "UP 🟢", curr_price)
            return

    if abs(fl1 - fl2) < curr_price * 0.0002 and pk2 < pk1:
        if curr_price < fl2 and sync_bear and df["volume"].iloc[-1] > vol_ma:
            trigger_signal(symbol, "S2: TRIANGLE SYNC", "DOWN 🔴", curr_price)
            return

    # ===== S3: STOCH SCALPER =====
    stoch = ta.stoch(df["high"], df["low"], df["close"], 5, 3, 3)
    if stoch is None or stoch.empty:
        return

    k, d = stoch.iloc[-1, 0], stoch.iloc[-1, 1]

    if k < 20 and k > d and sync_bull:
        trigger_signal(symbol, "S3: SYNC SCALP", "UP 🟢", curr_price)
        return
    if k > 80 and k < d and sync_bear:
        trigger_signal(symbol, "S3: SYNC SCALP", "DOWN 🔴", curr_price)
        return

    # ===== S4: TREND RIDER =====
    sma100 = ta.sma(df["close"], 100).iloc[-1]
    st = ta.supertrend(df["high"], df["low"], df["close"], 5, 2)
    st_dir = st.iloc[:, -1].iloc[-1]  # ✅ Safe direction

    body_now = abs(df["close"].iloc[-1] - df["open"].iloc[-1])
    body_prev = abs(df["close"].iloc[-2] - df["open"].iloc[-2])

    touching = abs(curr_price - sma100) / sma100 < 0.0001

    if touching and body_now > body_prev:
        if st_dir == 1 and sync_bull:
            trigger_signal(symbol, "S4: TREND RIDER", "UP 🟢", curr_price)
        elif st_dir == -1 and sync_bear:
            trigger_signal(symbol, "S4: TREND RIDER", "DOWN 🔴", curr_price)

# ================== SIGNAL & RESULTS ==================
def trigger_signal(symbol, strategy, direction, entry):
    last_signal_time[symbol] = time.time()
    candle_index = len(market_history[symbol]) - 1

    bot.send_message(
        CHAT_ID,
        f"🎯 **{strategy}**\n📊 {symbol} | {direction}\n⏱ 1 MIN | Entry: {entry}",
        parse_mode="Markdown"
    )

    threading.Timer(60, check_result, args=[symbol, candle_index, direction]).start()

def check_result(symbol, entry_index, direction):
    global stats
    df = market_history.get(symbol)
    if df is None or len(df) <= entry_index + 1:
        return

    entry = df["close"].iloc[entry_index]
    exit_p = df["close"].iloc[entry_index + 1]

    win = exit_p > entry if "UP" in direction else exit_p < entry

    stats["total"] += 1
    stats["wins"] += int(win)
    stats["losses"] += int(not win)

    wr = round(stats["wins"] / stats["total"] * 100, 1)
    bot.send_message(CHAT_ID, f"{'✅ WIN' if win else '❌ LOSS'} | {symbol}\nWR: {wr}%")

# ================== WEBSOCKET ==================
def on_error(ws, error):
    if "401" in str(error):
        bot.send_message(CHAT_ID, "⚠️ **SSID EXPIRED — UPDATE REQUIRED**")

def on_message(ws, message):
    if not message.startswith('42["candles"'):
        return
    try:
        payload = json.loads(message[2:])[1]
        asset = payload["asset"]

        df = pd.DataFrame(payload["candles"])
        df.rename(columns={"o":"open","c":"close","h":"high","l":"low","v":"volume"}, inplace=True)

        market_history[asset] = df
        analyze_all_strategies(asset, df)

    except Exception as e:
        print(f"Processing Error: {e}")

def connect():
    while True:
        try:
            ws = websocket.WebSocketApp(
                "wss://api-c.po.market/socket.io/?EIO=4&transport=websocket",
                on_message=on_message,
                on_error=on_error,
                header=[f"Cookie: SSID={SSID}"]
            )
            ws.run_forever(ping_interval=20)
        except:
            time.sleep(10)

# ================== START ==================
if __name__ == "__main__":
    threading.Thread(target=update_news_calendar, daemon=True).start()
    bot.send_message(CHAT_ID, "✅ **BOT ONLINE — ALL SYSTEMS STABLE**")
    connect()
