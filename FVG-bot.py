import asyncio
import datetime
import pytz
import os
import pandas as pd
from flask import Flask
from threading import Thread
from telegram import Bot
from pocketoptionapi_async import AsyncPocketOptionClient

# --- KEEP-ALIVE ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Running"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

Thread(target=run, daemon=True).start()

# --- CONFIG ---
SSID = os.environ.get("POCKET_SSID")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Complete List
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

# Track last alert to prevent spamming the same candle
last_alerts = {} 

async def send_tg_alert(bot, msg):
    try: await bot.send_message(chat_id=CHAT_ID, text=msg)
    except: pass

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    return 100 - (100 / (1 + (gain / (loss + 1e-9))))

async def trade_loop(client, asset, bot):
    while True:
        try:
            if not client.websocket_client.is_connected:
                await asyncio.sleep(5)
                continue

            candles = await client.get_candles_dataframe(asset=asset, timeframe=60, count=50)
            if candles is None or candles.empty:
                await asyncio.sleep(10)
                continue

            candles['rsi'] = calculate_rsi(candles['close'])
            c1, c3 = candles.iloc[-3], candles.iloc[-1]
            rsi = c3['rsi']
            timestamp = c3.name # Current candle time

            # Check if we already alerted for this specific candle
            if last_alerts.get(asset) == timestamp:
                await asyncio.sleep(10)
                continue

            msg = None
            if c3['low'] > c1['high'] and rsi < 35:
                msg = f"🚀 BUY: {asset}\nType: CALL 🟢\nRSI: {rsi:.2f}"
            elif c3['high'] < c1['low'] and rsi > 65:
                msg = f"📉 SELL: {asset}\nType: PUT 🔴\nRSI: {rsi:.2f}"

            if msg:
                await send_tg_alert(bot, msg)
                last_alerts[asset] = timestamp # Mark as alerted

        except Exception:
            pass
        await asyncio.sleep(15) # Faster scanning for 50+ pairs

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    while True:
        client = AsyncPocketOptionClient(SSID, is_demo=True)
        try:
            if await client.connect():
                balance = await client.get_balance()
                await send_tg_alert(bot, f"✅ Bot Online\nBalance: {balance}\nMonitoring {len(PAIRS)} pairs.")
                # Run all loops. If connection drops, this will raise an error and trigger the except block.
                await asyncio.gather(*[trade_loop(client, p, bot) for p in PAIRS])
        except Exception as e:
            print(f"Connection lost: {e}. Reconnecting...")
        
        try: await client.close()
        except: pass
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
