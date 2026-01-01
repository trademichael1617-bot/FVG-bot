import asyncio
import os
import pandas as pd
from flask import Flask
from threading import Thread
from telegram import Bot
from pocketoptionapi_async import AsyncPocketOptionClient

# --- KEEP-ALIVE ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Online"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

Thread(target=run, daemon=True).start()

# --- CONFIG ---
SSID = os.environ.get("POCKET_SSID")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
last_alerts = {}

async def send_tg_alert(bot, msg):
    try: await bot.send_message(chat_id=CHAT_ID, text=msg)
    except: pass

def calculate_rsi(series, period=10): # UPDATED TO 10
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    return 100 - (100 / (1 + (gain / (loss + 1e-9))))

def find_sr_zones(df, window=30):
    levels = []
    for i in range(window, len(df) - window):
        if df['high'].iloc[i] == df['high'].iloc[i-window:i+window].max():
            levels.append(df['high'].iloc[i])
        if df['low'].iloc[i] == df['low'].iloc[i-window:i+window].min():
            levels.append(df['low'].iloc[i])
    return levels

async def trade_loop(client, asset, bot):
    while True:
        try:
            df = await client.get_candles_dataframe(asset=asset, timeframe=60, count=250)
            if df is not None and not df.empty:
                df['rsi'] = calculate_rsi(df['close'], period=10) # RSI 10 applied
                
                c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
                rsi_now = df['rsi'].iloc[-1]
                rsi_prev = df['rsi'].iloc[-2]
                current_price = c3['close']
                timestamp = df.index[-1]

                sr_levels = find_sr_zones(df, window=30)
                is_near_sr = any(abs(current_price - level) / current_price < 0.0002 for level in sr_levels)

                if last_alerts.get(asset) != timestamp:
                    msg = None
                    
                    # BULLISH: FVG + 30-Candle Support + RSI 10 crosses 50 (from 48 base)
                    if (c3['low'] > c1['high']) and is_near_sr:
                        if rsi_prev <= 50 <= rsi_now and rsi_now >= 48:
                            msg = f"🟢 BULLISH (RSI 10): {asset}\nSupport: 30-Candle Zone ✅\nStructure: FVG Gap ✅\nAction: RSI 50 Cross UP (Base 48) ✅"
                    
                    # BEARISH: FVG + 30-Candle Resistance + RSI 10 crosses 50 (from 52 base)
                    elif (c3['high'] < c1['low']) and is_near_sr:
                        if rsi_prev >= 50 >= rsi_now and rsi_now <= 52:
                            msg = f"🔴 BEARISH (RSI 10): {asset}\nResist: 30-Candle Zone ✅\nStructure: FVG Gap ✅\nAction: RSI 50 Cross DOWN (Base 52) ✅"

                    if msg:
                        await send_tg_alert(bot, msg)
                        last_alerts[asset] = timestamp
        except: pass
        await asyncio.sleep(15)

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    while True:
        client = AsyncPocketOptionClient(SSID, is_demo=True)
        try:
            if await client.connect():
                all_info = await client.get_all_asset_info()
                target_pairs = [a[1] for a in all_info if a[3] == 'currency' and a[5] >= 92]
                
                await send_tg_alert(bot, "⚡ Bot Active: RSI 10 Mode\nLevels: 48, 50, 52 Confluence\nWindow: 30 Candles")
                
                tasks = [trade_loop(client, p, bot) for p in target_pairs]
                await asyncio.gather(*tasks)
        except: pass
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
