import asyncio
import os
import pandas as pd
from flask import Flask
from threading import Thread
from telegram import Bot
from pocketoptionapi_async import AsyncPocketOptionClient

# --- KEEP-ALIVE SERVER ---
app = Flask('')
@app.route('/')
def home(): return "Bot is Online"

def run():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

Thread(target=run, daemon=True).start()

# --- CONFIGURATION ---
SSID = os.environ.get("POCKET_SSID")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
last_alerts = {}

async def send_tg_alert(bot, msg):
    try: 
        await bot.send_message(chat_id=CHAT_ID, text=msg)
    except Exception as e: 
        print(f"TG Error: {e}")

def calculate_indicators(df):
    # RSI 10
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=10).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=10).mean()
    df['rsi'] = 100 - (100 / (1 + (gain / (loss + 1e-9))))
    
    # Volume SMA for Surge
    df['vol_avg'] = df['volume'].rolling(window=10).mean()
    
    # Support/Resistant zone alignment check (RSI near 50)
    df['rsi_at_mid'] = (df['rsi'] >= 48) & (df['rsi'] <= 52)
    return df

def find_sr_zones(df, window=30):
    levels = []
    for i in range(window, len(df) - window):
        if df['high'].iloc[i] == df['high'].iloc[i-window:i+window].max():
            levels.append(df['high'].iloc[i])
        if df['low'].iloc[i] == df['low'].iloc[i-window:i+window].min():
            levels.append(df['low'].iloc[i])
    return levels

async def check_ssid_health(client, bot):
    try:
        is_connected = await client.connect()
        if not is_connected:
            await send_tg_alert(bot, "⚠️ SSID EXPIRED: Please refresh your Pocket Option SSID.")
            return False
        return True
    except Exception:
        return False

async def trade_loop(client, asset, bot):
    while True:
        try:
            df = await client.get_candles_dataframe(asset=asset, timeframe=60, count=250)
            if df is not None and not df.empty:
                df = calculate_indicators(df)
                c1, c3 = df.iloc[-3], df.iloc[-1]
                rsi_now, rsi_prev = df['rsi'].iloc[-1], df['rsi'].iloc[-2]
                vol_now, vol_avg = df['volume'].iloc[-1], df['vol_avg'].iloc[-1]
                current_price, timestamp = c3['close'], df.index[-1]

                sr_levels = find_sr_zones(df, window=30)
                is_near_sr = any(abs(current_price - level) / current_price < 0.0002 for level in sr_levels)
                is_vol_surge = vol_now > vol_avg

                if last_alerts.get(asset) != timestamp:
                    msg = None
                    body = abs(c3['open'] - c3['close'])

                    # BULLISH: FVG 50% + S/R + RSI 50 Cross Up
                    if c3['low'] > c1['high']:
                        fvg_mid = (c3['low'] + c1['high']) / 2
                        is_at_mid = abs(current_price - fvg_mid) / current_price < 0.0001
                        lower_wick = min(c3['open'], c3['close']) - c3['low']
                        
                        if is_at_mid and is_near_sr and is_vol_surge and (rsi_prev <= 50 <= rsi_now) and lower_wick > (body * 0.3):
                            msg = f"🏆 ELITE BULLISH: {asset}\nS/R + RSI 50 ✅\nFVG 50% Fill ✅\nVol Surge ✅"

                    # BEARISH: FVG 50% + S/R + RSI 50 Cross Down
                    elif c3['high'] < c1['low']:
                        fvg_mid = (c3['high'] + c1['low']) / 2
                        is_at_mid = abs(current_price - fvg_mid) / current_price < 0.0001
                        upper_wick = c3['high'] - max(c3['open'], c3['close'])

                        if is_at_mid and is_near_sr and is_vol_surge and (rsi_prev >= 50 >= rsi_now) and upper_wick > (body * 0.3):
                            msg = f"🏆 ELITE BEARISH: {asset}\nS/R + RSI 50 ✅\nFVG 50% Fill ✅\nVol Surge ✅"

                    if msg:
                        await send_tg_alert(bot, msg)
                        last_alerts[asset] = timestamp
        except: pass
        await asyncio.sleep(15)

async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    await send_tg_alert(bot, "🤖 Master Bot is starting on REAL account mode...")
    
    while True:
        client = AsyncPocketOptionClient(SSID, is_demo=False)
        if await check_ssid_health(client, bot):
            try:
                all_info = await client.get_all_asset_info()
                target_pairs = [a[1] for a in all_info if a[3] == 'currency' and a[5] >= 92]
                await send_tg_alert(bot, f"🛡️ Master Bot Active | {len(target_pairs)} Pairs (92%+)")

                tasks = [trade_loop(client, p, bot) for p in target_pairs]
                await asyncio.gather(*tasks)
            except: pass
        await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())
