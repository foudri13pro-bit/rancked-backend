from sqlalchemy import Column, Integer, String, DateTime, JSON
from datetime import datetime
from app.core.database import Base


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    external_id = Column(String, unique=True, index=True, nullable=False)

    map_name = Column(String, nullable=True)
    scenario = Column(String, nullable=True)
    winner_team = Column(String, nullable=True)

    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)

    raw_data = Column(JSON, nullable=True)
    parsed_data = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)