# app/main.py
import os
import asyncio
import threading
import sys
import traceback
from pathlib import Path

from fastapi import FastAPI, Response
from dotenv import load_dotenv

# Charger le .env (en local) — sur Render, les VARs sont dans le dashboard
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ENV_PATH, encoding="utf-8")

from app.bot.bot import bot  # importe après load_dotenv

app = FastAPI(title="Zenavia Ranked API")

TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
if not TOKEN:
    print("⚠️  DISCORD_TOKEN manquant (OK sur Render si défini dans les env vars).", file=sys.stderr)

# --- Routes racines & santé ---
@app.get("/", include_in_schema=False)
def root():
    return {"message": "Bienvenue sur l'API Zenavia Ranked!", "bot": "running"}

@app.head("/", include_in_schema=False)
def root_head():
    return Response(status_code=200)

@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}

@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}

# --- Lancement du bot dans un thread ---
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
    if not _bot_started and TOKEN:
        threading.Thread(target=run_bot_thread, daemon=True).start()
        _bot_started = True

@app.on_event("shutdown")
async def _stop_bot():
    try:
        await bot.close()
    except Exception:
        pass
