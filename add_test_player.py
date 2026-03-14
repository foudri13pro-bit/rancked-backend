from app.core.database import SessionLocal
from app.models.player import Player

db = SessionLocal()

player = Player(
    discord_id="123456789",
    minecraft_name="Foudri",
    zenavia_player_id=None,
    current_mmr=1000,
    games_played=0,
    wins=0,
    losses=0,
    kills=0,
    deaths=0,
    infections=0,
    survivals=0,
    first_z_count=0,
    active_ranked=True,
)

db.add(player)
db.commit()
db.close()

print("✅ Joueur de test ajouté.")