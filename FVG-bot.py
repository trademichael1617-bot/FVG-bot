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

def calculate_rsi(series, period=10):
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
                df['rsi'] = calculate_rsi(df['close'], period=10)
                
                c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
                rsi_now = df['rsi'].iloc[-1]
                rsi_prev = df['rsi'].iloc[-2]
                current_price = c3['close']
                timestamp = df.index[-1]

                # Identify 30-Candle S/R
                sr_levels = find_sr_zones(df, window=30)
                is_near_sr = any(abs(current_price - level) / current_price < 0.0002 for level in sr_levels)

                if last_alerts.get(asset) != timestamp:
                    msg = None
                    
                    # BULLISH: FVG 50% + S/R + RSI 48/50 + REJECTION WICK
                    if c3['low'] > c1['high']:
                        fvg_mid = (c3['low'] + c1['high']) / 2
                        is_at_mid = abs(current_price - fvg_mid) / current_price < 0.0001
                        
                        # Rejection Check: Lower wick must be at least 30% of the candle body
                        candle_body = abs(c3['open'] - c3['close'])
                        lower_wick = min(c3['open'], c3['close']) - c3['low']
                        has_rejection = lower_wick > (candle_body * 0.3)

                        if is_at_mid and is_near_sr and (rsi_prev <= 50 <= rsi_now) and rsi_now >= 48 and has_rejection:
                            msg = f"🔥 ELITE BUY: {asset}\nGap: FVG 50% Fill ✅\nS/R: 30-Candle Support ✅\nRSI: 50 Cross (Base 48) ✅\nPrice: Rejection Wick Detected ✅"

                    # BEARISH: FVG 50% + S/R + RSI 52/50 + REJECTION WICK
                    elif c3['high'] < c1['low']:
                        fvg_mid = (c3['high'] + c1['low']) / 2
                        is_at_mid = abs(current_price - fvg_mid) / current_price < 0.0001
                        
                        # Rejection Check: Upper wick must be at least 30% of the candle body
                        candle_body = abs(c3['open'] - c3['close'])
                        upper_wick = c3['high'] - max(c3['open'], c3['close'])
                        has_rejection = upper_wick > (candle_body * 0.3)

                        if is_at_mid and is_near_sr and (rsi_prev >= 50 >= rsi_now) and rsi_now <= 52 and has_rejection:
                            msg = f"🧊 ELITE SELL: {asset}\nGap: FVG 50% Fill ✅\nS/R: 30-Candle Resist ✅\nRSI: 50 Cross (Base 52) ✅\nPrice: Rejection Wick Detected ✅"

                    if msg:
                        await send_tg_alert(bot, msg)
                        last_alerts[asset] = timestamp
        except: pass
        await asyncio.sleep(15)

# (main() function remains the same as previous version)
