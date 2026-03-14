from fastapi import APIRouter
from app.services.match_processor import process_match

router = APIRouter()

@router.post("/process_match/{match_id}")
def process(match_id: str):
    return process_match(match_id)