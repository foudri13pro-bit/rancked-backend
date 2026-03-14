from fastapi import APIRouter
from app.core.database import SessionLocal
from app.models.player import Player

router = APIRouter()


@router.get("/leaderboard")
def leaderboard(limit: int = 20):
    db = SessionLocal()

    try:
        players = (
            db.query(Player)
            .order_by(Player.current_mmr.desc())
            .limit(limit)
            .all()
        )

        result = []
        rank = 1

        for p in players:
            display_name = p.minecraft_name or p.zenavia_player_id or f"player_{p.id}"

            result.append({
                "rank": rank,
                "username": display_name,
                "mmr": p.current_mmr,
                "games": p.games_played
            })

            rank += 1

        return result

    finally:
        db.close()