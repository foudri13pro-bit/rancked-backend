import os
import logging
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from app.bot.bot import bot  # ← ton RankedBot défini dans bot.py

# =========================
#          LOGGING
# =========================
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ranked_backend")

# =========================
#         CONFIG
# =========================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# =========================
#        FASTAPI APP
# =========================
app = FastAPI(title="Ranked Infected Backend")

# (optionnel, mais pratique si plus tard tu exposes des routes)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "status": "ok",
        "message": "Ranked Infected backend is running.",
    }


@app.get("/ping")
async def ping():
    return {"pong": True}


# =========================
#     DISCORD BOT LIFECYCLE
# =========================

bot_task: asyncio.Task | None = None


@app.on_event("startup")
async def on_startup():
    """Démarre le bot Discord en tâche de fond quand FastAPI démarre."""
    global bot_task

    log.info("🚀 FastAPI startup")

    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN est manquant dans les variables d'environnement."
        )

    loop = asyncio.get_event_loop()
    # bot.start() est async → on le lance dans une task
    bot_task = loop.create_task(bot.start(TOKEN))
    log.info("🤖 Discord bot démarré en tâche de fond.")


@app.on_event("shutdown")
async def on_shutdown():
    """Arrête proprement le bot quand FastAPI s'arrête."""
    global bot_task

    log.info("🛑 FastAPI shutdown")

    if bot_task and not bot_task.done():
        try:
            await bot.close()
        except Exception as e:
            log.warning(f"Erreur lors de la fermeture du bot : {e}")
        bot_task.cancel()
        bot_task = None
        log.info("✅ Bot Discord arrêté.")


# =========================
#      LANCEMENT UVICORN
# =========================

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    log.info(f"🌐 Lancement d'uvicorn sur le port {port}...")

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,  # important sur Render
    )
