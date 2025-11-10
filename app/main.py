# app/main.py
import threading, sys, traceback, asyncio
from fastapi import FastAPI
from dotenv import load_dotenv
from app.bot.bot import bot

app = FastAPI(title="Zenavia Ranked API")
TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()

def run_bot_thread():
    try:
        asyncio.run(bot.start(TOKEN))
    except Exception as e:
        print("Bot thread stopped with error:", e, file=sys.stderr)
        traceback.print_exc()

_bot_started = False

@app.on_event("startup")
async def _start_bot():
    global _bot_started
    if not _bot_started:
        threading.Thread(target=run_bot_thread, daemon=True).start()
        _bot_started = True

@app.on_event("shutdown")
async def _stop_bot():
    try:
        await bot.close()
    except Exception:
        pass
