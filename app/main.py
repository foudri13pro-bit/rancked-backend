from app.core.database import Base, engine
from app.api.leaderboard import router as leaderboard_router
from app.models import Player, Match, MMRHistory
from app.api.match import router as match_router

import os
import logging
import asyncio

from fastapi import FastAPI

from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from fastapi import HTTPException
from pydantic import BaseModel
from app.bot.bot import bot, send_match_report, update_hall

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
app.include_router(match_router)
app.include_router(leaderboard_router)

# (optionnel, mais pratique si plus tard tu exposes des routes)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

@app.get("/")
async def root():
    return {
        "status": "ok",
        "message": "Ranked Infected backend is running.",
    }


@app.get("/ping")
async def ping():
    return {"pong": True}

class MatchReportPayload(BaseModel):
    status: str
    match_id: str | int | None = None
    players_updated: int = 0
    winner: str | None = None
    map_name: str | None = None
    scenarios: list[str] = []
    top_changes: list[dict] = []
    player_summaries: list[dict] = []
    duration: int = 0


@app.post("/announce_match_report")
async def announce_match_report(payload: MatchReportPayload):
    try:
        if not bot.guilds:
            raise HTTPException(status_code=503, detail="Bot Discord non connecté à un serveur.")

        guild = bot.guilds[0]

        await send_match_report(guild, payload.model_dump())
        await update_hall(guild)

        return {"status": "sent"}

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
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
