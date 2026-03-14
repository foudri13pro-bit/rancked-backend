import time
import logging
import requests

from sqlalchemy import Column, Integer, DateTime
from datetime import datetime

from app.core.database import SessionLocal, Base, engine
from app.models.player import Player
from app.api.zenavia_api import ZenaviaAPI
from app.services.match_processor import process_match

log = logging.getLogger("match_sync")
api = ZenaviaAPI()


class ProcessedGame(Base):
    __tablename__ = "processed_games"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, unique=True, index=True, nullable=False)
    processed_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


def get_ranked_players():
    db = SessionLocal()
    try:
        players = (
            db.query(Player)
            .filter(Player.active_ranked == True)
            .all()
        )
        return players
    finally:
        db.close()


def is_game_processed(game_id: int) -> bool:
    db = SessionLocal()
    try:
        existing = (
            db.query(ProcessedGame)
            .filter(ProcessedGame.game_id == game_id)
            .first()
        )
        return existing is not None
    finally:
        db.close()


def try_reserve_game(game_id: int) -> bool:
    """
    Essaie de réserver la game pour traitement.
    Retourne True si la réservation a réussi.
    Retourne False si la game est déjà réservée / déjà traitée.
    """
    db = SessionLocal()
    try:
        existing = (
            db.query(ProcessedGame)
            .filter(ProcessedGame.game_id == game_id)
            .first()
        )

        if existing:
            return False

        db.add(ProcessedGame(game_id=game_id))
        db.commit()
        return True

    except Exception as e:
        db.rollback()
        log.error(f"Erreur try_reserve_game({game_id}) : {e}")
        return False

    finally:
        db.close()


def unreserve_game(game_id: int):
    """
    À utiliser seulement si process_match échoue,
    afin de permettre une nouvelle tentative plus tard.
    """
    db = SessionLocal()
    try:
        existing = (
            db.query(ProcessedGame)
            .filter(ProcessedGame.game_id == game_id)
            .first()
        )

        if existing:
            db.delete(existing)
            db.commit()
            log.info(f"Réservation supprimée pour la game {game_id}")
    except Exception as e:
        db.rollback()
        log.error(f"Erreur unreserve_game({game_id}) : {e}")
    finally:
        db.close()


def check_new_games_for_player(player: Player):
    if not player.minecraft_name:
        log.warning(f"Joueur ignoré car minecraft_name manquant : id={player.id}")
        return

    games = api.get_player_games(player.minecraft_name, page=0, size=10)

    for game in games:
        game_id = game.get("gameId")
        if not game_id:
            continue

        # Réservation atomique simple
        if not try_reserve_game(game_id):
            continue

        log.info(f"Nouvelle game réservée : {game_id} pour {player.minecraft_name}")

        result = None

        try:
            result = process_match(str(game_id))
            log.info(f"Résultat process_match({game_id}) : {result}")

            if result.get("status") == "processed":
                try:
                    response = requests.post(
                        "http://127.0.0.1:8000/announce_match_report",
                        json=result,
                        timeout=5
                    )
                    log.info(
                        f"Annonce Discord match {game_id} -> "
                        f"{response.status_code} {response.text}"
                    )
                except Exception as announce_error:
                    log.error(
                        f"Erreur annonce Discord pour match {game_id} : {announce_error}"
                    )

            elif result.get("status") == "already_processed":
                log.info(f"Game {game_id} déjà traitée côté matches.")

            elif result.get("status") == "error":
                log.error(f"Erreur logique process_match({game_id}) : {result}")
                unreserve_game(game_id)

            else:
                log.warning(
                    f"Statut inattendu pour process_match({game_id}) : {result}"
                )
                unreserve_game(game_id)

        except Exception as e:
            log.error(f"Erreur process_match({game_id}) : {e}")
            unreserve_game(game_id)


def check_all_ranked_players():
    players = get_ranked_players()

    if not players:
        log.warning("Aucun joueur active_ranked=True trouvé.")
        return

    log.info(f"{len(players)} joueur(x) ranked surveillé(s).")

    for player in players:
        try:
            check_new_games_for_player(player)
        except Exception as e:
            log.error(
                f"Erreur pendant la vérification de {player.minecraft_name}: {e}"
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    log.info("=== SURVEILLANCE DES MATCHS ZENAVIA ===")

    while True:
        try:
            check_all_ranked_players()
        except Exception as e:
            log.error(f"Erreur globale pendant la surveillance : {e}")

        time.sleep(30)