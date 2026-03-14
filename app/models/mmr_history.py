from sqlalchemy import Column, Integer, ForeignKey, DateTime, JSON
from datetime import datetime
from app.core.database import Base


class MMRHistory(Base):
    __tablename__ = "mmr_history"

    id = Column(Integer, primary_key=True, index=True)

    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)

    old_mmr = Column(Integer, nullable=False)
    new_mmr = Column(Integer, nullable=False)
    delta = Column(Integer, nullable=False)

    meta = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)