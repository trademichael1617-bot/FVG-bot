import asyncio
import datetime
import pytz
import os
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

# Start the server in the background
Thread(target=run, daemon=True).start()

# --- CONFIGURATION ---
# Note: Ensure SSID is the full 42["auth",...] string
SSID = "YOUR_SSID"
TELEGRAM_TOKEN = "YOUR_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

PAIRS = [
    "EURUSD_otc", "AUDCAD_otc", "AUDCHF_otc", "AUDJPY_otc", "AUDNZD_otc", "AUDUSD_otc",
    "CADCHF_otc", "CADJPY_otc", "CHFJPY_otc", "EURCHF_otc", "EURGBP_otc", "EURJPY_otc",
    "EURNZD_otc", "GBPAUD_otc", "GBPJPY_otc", "GBPUSD_otc", "NZDJPY_otc", "NZDUSD_otc",
    "USDCAD_otc", "USDCHF_otc", "USDJPY_otc", "USDRUB_otc", "EURRUB_otc", "CHFNOK_otc",
    "EURHUF_otc", "USDCNH_otc", "EURTRY_otc", "USDINR_otc", "USDSGD_otc", "USDCLP_otc",
    "USDMYR_otc", "USDTHB_otc", "USDVND_otc", "USDPKR_otc", "USDCOP_otc", "USDEGP_otc",
    "USDPHP_otc", "USDMXN_otc", "USDDZD_otc", "USDARS_otc", "USDIDR_otc", "USDBRL_otc",
    "USDBRL_otc", "USDBDT_otc", "YERUSD_otc", "LBPUSD_otc", "TNDUSD_otc", "MADUSD_otc", 
    "BHDCNY_otc", "AEDCNY_otc", "SARCNY_otc", "QARCNY_otc", "OMRCNY_otc", "JODCNY_otc", 
    "NGNUSD_otc", "KESUSD_otc", "ZARUSD_otc", "UAHUSD_otc"
]

MORNING_BLOCK = (8, 12)
PEAK_BLOCK = (13, 16)

def is_trading_session():
    now_utc = datetime.datetime.now(pytz.utc)
    return MORNING_BLOCK[0] <= now_utc.hour < MORNING_BLOCK[1] or \
           PEAK_BLOCK[0] <= now_utc.hour < PEAK_BLOCK[1]

async def send_tg_alert(bot, msg):
    try:
        # Use simple Markdown to avoid underscore errors in asset names
        await bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e:
        print(f"Telegram Error: {e}")

async def check_ssid_health(client, bot):
    """Verifies if the SSID is active by checking the balance."""
    try:
        balance_data = await client.get_balance()
        if balance_data is None:
            await send_tg_alert(bot, "⚠️ SSID Expired! Bot cannot fetch balance.")
            return False
        return True
    except Exception as e:
        print(f"Health Check Error: {e}")
        return False

async def trade_loop(client, asset, bot):
    print(f"Monitoring: {asset}")
    while True:
        if not is_trading_session():
            await asyncio.sleep(60)
            continue
        
        # Logic for FVG/RSI/Fractals goes here
        await asyncio.sleep(1) 

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    client = AsyncPocketOptionClient(SSID, is_demo=True)
    
    print("Connecting to Pocket Option...")
    connected = await client.connect()
    
    if not connected:
        print("Initial Connection Failed. Check your SSID.")
        return

    # Check if SSID actually works
    if not await check_ssid_health(client, bot):
        return

    status = "🟢 ACTIVE" if is_trading_session() else "🟡 STANDBY"
    await send_tg_alert(bot, f"🤖 Pocket Option Bot Online\nStatus: {status}\nMonitoring {len(PAIRS)} Pairs")

    # Start all trade loops concurrently
    await asyncio.gather(*[trade_loop(client, p, bot) for p in PAIRS])

if __name__ == "__main__":
    asyncio.run(main())
