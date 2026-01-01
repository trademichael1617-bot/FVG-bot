import asyncio
import datetime
import pytz
import os
import pandas as pd
from flask import Flask
from threading import Thread
from telegram import Bot
from pocketoptionapi_async import AsyncPocketOptionClient

# --- KEEP-ALIVE SERVER ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Running"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

Thread(target=run, daemon=True).start()

# --- CONFIGURATION ---
SSID = os.environ.get("POCKET_SSID")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

PAIRS = [
    "EURUSD_otc", "AUDCAD_otc", "AUDCHF_otc", "AUDJPY_otc", "AUDNZD_otc", "AUDUSD_otc",
    "CADCHF_otc", "CADJPY_otc", "CHFJPY_otc", "EURCHF_otc", "EURGBP_otc", "EURJPY_otc",
    "EURNZD_otc", "GBPAUD_otc", "GBPJPY_otc", "GBPUSD_otc", "NZDJPY_otc", "NZDUSD_otc",
    "USDCAD_otc", "USDCHF_otc", "USDJPY_otc", "USDRUB_otc", "EURRUB_otc", "CHFNOK_otc",
    "EURHUF_otc", "USDCNH_otc", "EURTRY_otc", "USDINR_otc", "USDSGD_otc", "USDCLP_otc",
    "USDMYR_otc", "USDTHB_otc", "USDVND_otc", "USDPKR_otc", "USDCOP_otc", "USDEGP_otc",
    "USDPHP_otc", "USDMXN_otc", "USDDZD_otc", "USDARS_otc", "USDIDR_otc", "USDBRL_otc",
    "USDBDT_otc", "YERUSD_otc", "LBPUSD_otc", "TNDUSD_otc", "MADUSD_otc", 
    "BHDCNY_otc", "AEDCNY_otc", "SARCNY_otc", "QARCNY_otc", "OMRCNY_otc", "JODCNY_otc", 
    "NGNUSD_otc", "KESUSD_otc", "ZARUSD_otc", "UAHUSD_otc"
]

# Change this to allow the bot to run now
MORNING_BLOCK = (0, 24) 
PEAK_BLOCK = (0, 0)

def is_trading_session():
    now_utc = datetime.datetime.now(pytz.utc)
    return MORNING_BLOCK[0] <= now_utc.hour < MORNING_BLOCK[1] or \
           PEAK_BLOCK[0] <= now_utc.hour < PEAK_BLOCK[1]

async def send_tg_alert(bot, msg):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e:
        print(f"Telegram Error: {e}")
async def check_ssid_health(client, bot):
    try:
        balance = await client.get_balance()
        print(f"💰 Connection Verified! Current Balance: {balance}")
        return True
    except Exception as e:
        print(f"❌ Health Check Failed: {e}")
        return False

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

async def trade_loop(client, asset, bot):
    print(f"Monitoring: {asset}")
    while True:
        if not is_trading_session():
            await asyncio.sleep(60)
            continue
        try:
            candles = await client.get_candles_dataframe(asset=asset, timeframe=60, count=50)
            if candles.empty:
                await asyncio.sleep(10)
                continue

            candles['rsi'] = calculate_rsi(candles['close'])
            c1, c2, c3 = candles.iloc[-3], candles.iloc[-2], candles.iloc[-1]
            current_rsi = c3['rsi']

            # Bullish FVG (Gap between Candle 1 High and Candle 3 Low)
            if c3['low'] > c1['high'] and current_rsi < 35:
                await send_tg_alert(bot, f"🚀 BUY SIGNAL: {asset}\nType: CALL 🟢\nRSI: {current_rsi:.2f}")

            # Bearish FVG (Gap between Candle 1 Low and Candle 3 High)
            elif c3['high'] < c1['low'] and current_rsi > 65:
                await send_tg_alert(bot, f"📉 SELL SIGNAL: {asset}\nType: PUT 🔴\nRSI: {current_rsi:.2f}")

        except Exception as e:
            print(f"Error analyzing {asset}: {e}")
        await asyncio.sleep(60)
async def main():
    if not all([SSID, TELEGRAM_TOKEN, CHAT_ID]):
        print("❌ ERROR: Missing Environment Variables!")
        return
    
    bot = Bot(token=TELEGRAM_TOKEN)
    client = AsyncPocketOptionClient(SSID, is_demo=True)
    
    print("Connecting to Pocket Option...")
    # Attempt connection with a few retries
    connected = False
    for i in range(3):
        if await client.connect():
            connected = True
            break
        print(f"Attempt {i+1} failed, retrying...")
        await asyncio.sleep(5)

    if not connected:
        print("Initial Connection Failed. Check if SSID is valid/expired.")
        return

    # Check health after a short delay to let the socket stabilize
    await asyncio.sleep(2)
    if not await check_ssid_health(client, bot):
        return

    # ... rest of your code
    # 2. Connect
    print("Connecting to Pocket Option...")
    if not await client.connect():
        print("Initial Connection Failed.")
        return

    # 3. Health Check
    if not await check_ssid_health(client, bot):
        return

    status = "🟢 ACTIVE" if is_trading_session() else "🟡 STANDBY"
    await send_tg_alert(bot, f"🤖 Bot Online\nStatus: {status}\nPairs: {len(PAIRS)}")

    # 4. Start Loops
    await asyncio.gather(*[trade_loop(client, p, bot) for p in PAIRS])

if __name__ == "__main__":
    asyncio.run(main())
