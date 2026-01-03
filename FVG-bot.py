import os, telebot, pandas as pd, pandas_ta as ta, websocket, json, threading, time, requests
from datetime import datetime, timedelta, timezone

# ================== SYSTEM CONFIG ==================
TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")
SSID = os.getenv("PO_SSID", "YOUR_SSID")

bot = telebot.TeleBot(TOKEN)
market_history, cooldowns = {}, {}
stats = {"total": 0, "wins": 0, "losses": 0}

# ================== NEWS FILTER LOGIC ==================
active_news_events = [] 

def update_news_calendar():
    global active_news_events
    while True:
        try:
            response = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
            events = response.json()
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            new_blocks = []
            for event in events:
                if event['impact'] in ["High", "Medium"]:
                    e_time = datetime.strptime(event['date'], "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
                    if (e_time - timedelta(minutes=5)) <= now <= (e_time + timedelta(minutes=15)):
                        new_blocks.append(event['country'])
            active_news_events = list(set(new_blocks))
        except Exception as e: print(f"News Filter Error: {e}")
        time.sleep(60)

def is_asset_blocked(symbol):
    for currency in active_news_events:
        if currency in symbol: return True
    return False

# ================== 1. ENGINE ==================
def analyze_all_strategies(symbol, df, payout):
    if is_asset_blocked(symbol): return 
    if len(df) < 100: return 
 
    curr_p = df["close"].iloc[-1]
    
    # --- INDICATOR CALCULATIONS ---
    df["rsi7"] = ta.rsi(df["close"], length=7)
    df["rsi10"] = ta.rsi(df["close"], length=10)
    macd = ta.macd(df["close"], 12, 26, 9)
    mh_s3 = macd["MACDh_12_26_9"].iloc[-1]
    
    # Sync Logic
    s3_bull_sync = (df["rsi7"].iloc[-1] > 50 and mh_s3 > 0)
    s3_bear_sync = (df["rsi7"].iloc[-1] < 50 and mh_s3 < 0)
# --- S1: FVG FLIP (Updated for Buy & Sell) ---
    # Bullish FVG: Low[0] > High[-2]
    bull_fvg = df["low"].iloc[-1] > df["high"].iloc[-3]
    
    # Bearish FVG: High[0] < Low[-2]
    bear_fvg = df["high"].iloc[-1] < df["low"].iloc[-3]

    # RSI 10 must be near the 50 level for the "Flip" confirmation
    rsi_align = (48 <= df["rsi10"].iloc[-1] <= 52)

    if bull_fvg and rsi_align:
        trigger_signal(symbol, "S1: FVG FLIP", payout, "UP 🟢", curr_p)
        
    elif bear_fvg and rsi_align:
        trigger_signal(symbol, "S1: FVG FLIP", payout, "DOWN 🔴", curr_p)
    # --- S2: TRIANGLE SYNC (RSI 10 + MACD) ---
    recent_h, recent_l = df["high"].iloc[-15:-1], df["low"].iloc[-15:-1]
    pk1, pk2 = recent_h.iloc[0:7].max(), recent_h.iloc[7:14].max()
    fl1, fl2 = recent_l.iloc[0:7].min(), recent_l.iloc[7:14].min()
    vol_ma = df["volume"].rolling(window=20).mean().iloc[-1]
    s2_bull_sync = (df["rsi10"].iloc[-1] > 50 and mh_s3 > 0)
    s2_bear_sync = (df["rsi10"].iloc[-1] < 50 and mh_s3 < 0)

    if (abs(pk1 - pk2) < (curr_p * 0.0002) and fl2 > fl1) and curr_p > pk2:
        if s2_bull_sync and df["volume"].iloc[-1] > vol_ma:
            trigger_signal(symbol, "S2: TRIANGLE SYNC", payout, "UP 🟢", curr_p)
    elif (abs(fl1 - fl2) < (curr_p * 0.0002) and pk2 < pk1) and curr_p < fl2:
        if s2_bear_sync and df["volume"].iloc[-1] > vol_ma:
            trigger_signal(symbol, "S2: TRIANGLE SYNC", payout, "DOWN 🔴", curr_p)

    # --- S3: SYNC SCALPER (RSI 7 + MACD) ---
    stoch = ta.stoch(df["high"], df["low"], df["close"], 5, 3, 3)
    sk, sd = stoch["STOCHk_5_3_3"].iloc[-1], stoch["STOCHd_5_3_3"].iloc[-1]
    if sk > sd and sk < 20 and s3_bull_sync:
        trigger_signal(symbol, "S3: SYNC SCALP", payout, "UP 🟢", curr_p)
    elif sk < sd and sk > 80 and s3_bear_sync:
        trigger_signal(symbol, "S3: SYNC SCALP", payout, "DOWN 🔴", curr_p)

    # --- S4: TREND RIDER (SMA 100 + ST 5,2) ---
    sma100 = ta.sma(df["close"], 100).iloc[-1]
    st = ta.supertrend(df["high"], df["low"], df["close"], 5, 2)
    st_dir = st["SUPERTd_5_2.0"].iloc[-1]
    st_line = st["SUPERT_5_2.0"].iloc[-1]
    curr_body, prev_body = abs(df["close"].iloc[-1]-df["open"].iloc[-1]), abs(df["close"].iloc[-2]-df["open"].iloc[-2])
    
    is_touching = abs(curr_p - sma100) / sma100 < 0.0001
    prev_st_line, prev_sma100 = st["SUPERT_5_2.0"].iloc[-2], ta.sma(df["close"], 100).iloc[-2]
    crossed = (prev_st_line < prev_sma100 and st_line > sma100) or (prev_st_line > prev_sma100 and st_line < sma100)

    if (is_touching or crossed) and curr_body > prev_body:
        direction = "UP 🟢" if st_dir == 1 else "DOWN 🔴"
        trigger_signal(symbol, "S4: TREND RIDER", payout, direction, curr_p)

# ================== 2. HELPERS ==================
def trigger_signal(symbol, strat, payout, direction, entry):
    msg = f"🎯 **{strat}**\n📈 {symbol} | {direction}\n⏱ 1 MIN | Entry: {entry}"
    bot.send_message(CHAT_ID, msg, parse_mode='Markdown')
    threading.Timer(60, check_result, [symbol, entry, direction]).start()

def check_result(symbol, entry, direction):
    global stats
    curr_p = market_history[symbol][-1]["close"]
    win = (curr_p > entry if "UP" in direction else curr_p < entry)
    stats["total"] += 1
    if win: stats["wins"] += 1
    else: stats["losses"] += 1
    bot.send_message(CHAT_ID, f"📊 {'WIN ✅' if win else 'LOSS ❌'} ({symbol})\nWR: {round((stats['wins']/stats['total'])*100, 1)}%")

# ================== 3. SOCKET ==================
def on_message(ws, message):
    if not message.startswith("42"): return
    data = json.loads(message[2:])
    if data[0] == "candle":
        p = data[1]
        s, c = p["asset"], {"close": p["close"], "open": p["open"], "high": p["high"], "low": p["low"], "volume": p["volume"], "time": p["time"]}
        if s not in market_history: market_history[s] = []
        market_history[s].append(c)
        if len(market_history[s]) > 110: market_history[s].pop(0)
        analyze_all_strategies(s, pd.DataFrame(market_history[s]), 92)

def connect():
    while True:
        try:
            ws = websocket.WebSocketApp("wss://api-c.po.market/socket.io/?EIO=4&transport=websocket", 
                                        on_message=on_message, header=[f"Cookie: SSID={SSID}"])
            ws.run_forever(ping_interval=20)
        except: time.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=update_news_calendar, daemon=True).start()
    connect()
