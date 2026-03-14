from sqlalchemy import Column, Integer, String, DateTime, Boolean
from datetime import datetime
from app.core.database import Base

class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, index=True)

    discord_id = Column(String, unique=True, nullable=True, index=True)
    minecraft_name = Column(String, unique=True, nullable=True, index=True)
    zenavia_player_id = Column(String, unique=True, nullable=True, index=True)

    current_mmr = Column(Integer, default=1000)

    games_played = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)

    kills = Column(Integer, default=0)
    deaths = Column(Integer, default=0)
    infections = Column(Integer, default=0)
    survivals = Column(Integer, default=0)
    first_z_count = Column(Integer, default=0)

    active_ranked = Column(Boolean, default=True)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)