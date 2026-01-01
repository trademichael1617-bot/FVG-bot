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
def home(): return "Bot is Running"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

Thread(target=run, daemon=True).start()

# --- CONFIGURATION ---
SSID = os.environ.get("POCKET_SSID")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Global tracker to prevent duplicate alerts for the same candle
last_alerts = {}

async def send_tg_alert(bot, msg):
    """Helper to send Telegram messages safely."""
    try:
        await bot.send_message(chat_id=CHAT_ID, text=msg)
        print(f"Telegram Sent: {msg.splitlines()[0]}")
    except Exception as e:
        print(f"Telegram Error: {e}")

async def heartbeat_loop(bot, client):
    """Sends a status update every 60 minutes."""
    while True:
        await asyncio.sleep(3600)
        status = "Connected ✅" if client.websocket_client.is_connected else "Disconnected ❌"
        try:
            balance = await client.get_balance()
        except:
            balance = "Error fetching"
        
        await send_tg_alert(bot, f"💓 Bot Heartbeat\nStatus: {status}\nBalance: {balance}")

def calculate_rsi(series, period=14):
    """Standard RSI calculation with zero-division protection."""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

async def trade_loop(client, asset, bot):
    """Monitors a specific asset for FVG and RSI signals."""
    print(f"Started monitoring: {asset}")
    while True:
        try:
            if not client.websocket_client.is_connected:
                await asyncio.sleep(10)
                continue

            candles = await client.get_candles_dataframe(asset=asset, timeframe=60, count=50)
            if candles is None or candles.empty:
                await asyncio.sleep(20)
                continue

            candles['rsi'] = calculate_rsi(candles['close'])
            c1, c3 = candles.iloc[-3], candles.iloc[-1]
            rsi = c3['rsi']
            timestamp = c3.name

            # Check if alert already sent for this candle
            if last_alerts.get(asset) == timestamp:
                await asyncio.sleep(15)
                continue

            msg = None
            # Bullish FVG + Oversold RSI
            if c3['low'] > c1['high'] and rsi < 35:
                msg = f"🚀 BUY SIGNAL: {asset}\nType: CALL 🟢\nRSI: {rsi:.2f}\nPayout: 92%"
            
            # Bearish FVG + Overbought RSI
            elif c3['high'] < c1['low'] and rsi > 65:
                msg = f"📉 SELL SIGNAL: {asset}\nType: PUT 🔴\nRSI: {rsi:.2f}\nPayout: 92%"

            if msg:
                await send_tg_alert(bot, msg)
                last_alerts[asset] = timestamp

        except Exception as e:
            print(f"Loop Error ({asset}): {e}")
        
        await asyncio.sleep(15)

async def main():
    if not all([SSID, TELEGRAM_TOKEN, CHAT_ID]):
        print("❌ Missing environment variables (SSID, TOKEN, or CHAT_ID)")
        return

    bot = Bot(token=TELEGRAM_TOKEN)
    
    while True:
        print("🔄 Attempting to connect to Pocket Option...")
        client = AsyncPocketOptionClient(SSID, is_demo=True)
        
        try:
            if await client.connect():
                await send_tg_alert(bot, "🟢 Bot Connected. Filtering pairs...")
                
                # Discovery logic
                all_assets = await client.get_all_asset_info()
                target_pairs = []

                for asset in all_assets:
                    name = asset[1]
                    payout = asset[2] # Payout index in current API
                    
                    # Filter: Currencies ONLY, OTC ONLY, Payout 92%
                    is_currency = "#" not in name and "_otc" in name
                    if is_currency and payout == 92:
                        target_pairs.append(name)

                balance = await client.get_balance()
                await send_tg_alert(bot, f"✅ Setup Complete\nBalance: {balance}\nScanning {len(target_pairs)} pairs @ 92% Payout.")

                # Start Heartbeat and all Trade Loops
                tasks = [heartbeat_loop(bot, client)]
                for p in target_pairs:
                    tasks.append(trade_loop(client, p, bot))
                
                await asyncio.gather(*tasks)
        except Exception as e:
            print(f"Main Loop Error: {e}")
        
        print("🔌 Connection lost. Retrying in 30s...")
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())
