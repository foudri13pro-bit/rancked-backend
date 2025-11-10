# app/main.py
import os
import sys
import asyncio
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI
import uvicorn

from dotenv import load_dotenv

# === Charger le .env (à la racine du repo) ===
env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=env_path, encoding="utf-8")

TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0"))  # optionnel: sync rapide sur 1 serveur

# === Web API (ton site) ===
app = FastAPI(title="Zenavia Ranked API")

@app.get("/")
def read_root():
    return {"message": "Bienvenue sur l'API Zenavia Ranked!", "bot": "running"}

@app.get("/healthz")
def health():
    return {"status": "ok"}

# === Importer TON bot (copié dans app/bot/bot.py) ===
# ⚠️ Important: on n'appelle PAS bot.run() ici, on lance dans un thread.
from app.bot.bot import bot  # adapte si ton chemin diffère

def run_bot_thread():
    try:
        asyncio.run(bot.start(TOKEN))
    except Exception as e:
        print("Bot thread stopped with error:", e, file=sys.stderr)
        traceback.print_exc()

if __name__ == "__main__":
    if not TOKEN:
        print("❌ DISCORD_TOKEN manquant dans .env", file=sys.stderr)
        sys.exit(1)

    # Démarrer le bot en parallèle (daemon)
    threading.Thread(target=run_bot_thread, daemon=True).start()

    # Lancer FastAPI (Render injecte PORT)
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, log_level="info")
