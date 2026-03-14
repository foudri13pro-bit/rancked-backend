import time
import logging
import requests

from sqlalchemy import Column, Integer, DateTime
from datetime import datetime, timezone

from app.core.database import SessionLocal, Base, engine
from app.models.player import Player
from app.api.zenavia_api import ZenaviaAPI
from app.services.match_processor import process_match

log = logging.getLogger("match_sync")
api = ZenaviaAPI()

# Petit délai mini après endAt si l'API le met tôt
FINALIZE_DELAY_SECONDS = 20

# Nombre de polls identiques requis avant traitement
REQUIRED_STABLE_POLLS = 2

# État temporaire des matchs en attente de stabilité
pending_matches = {}


class ProcessedGame(Base):
    __tablename__ = "processed_games"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, unique=True, index=True, nullable=False)
    processed_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_api_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


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


def build_match_snapshot(raw_match: dict) -> dict:
    """
    Construit un snapshot compact du match.
    Si ce snapshot continue de changer, la partie n'est pas vraiment finie.
    """
    players = raw_match.get("players", [])
    compact_players = []

    for p in players:
        compact_players.append((
            str(
                p.get("playerId")
                or p.get("id")
                or p.get("minecraftNickname")
                or p.get("pseudo")
                or "unknown"
            ),
            int(p.get("kills", 0) or 0),
            int(p.get("damage", 0) or 0),
            int(p.get("infectedCount", 0) or 0),
            int(p.get("survivalTime", 0) or 0),
            str(p.get("role") or p.get("team") or ""),
        ))

    compact_players.sort()

    return {
        "endAt": raw_match.get("endAt"),
        "durationSec": int(raw_match.get("durationSec", 0) or 0),
        "winner": str(raw_match.get("winner") or ""),
        "player_count": len(players),
        "players": compact_players,
    }


def is_match_ready_for_processing(game_id: int) -> bool:
    """
    Le match n'est prêt que si :
    - endAt existe
    - un petit délai mini est passé
    - ET les stats sont stables sur plusieurs polls
    """
    try:
        raw_match = api.get_game_detail(game_id)
    except Exception as e:
        log.error(f"Impossible de récupérer le détail du match {game_id} : {e}")
        return False

    end_at_raw = raw_match.get("endAt")
    end_at_dt = parse_api_datetime(end_at_raw)

    log.info(
        f"Vérification match {game_id} : "
        f"startAt={raw_match.get('startAt')} "
        f"endAt={raw_match.get('endAt')} "
        f"durationSec={raw_match.get('durationSec')} "
        f"mapId={raw_match.get('mapId')}"
    )

    duration = int(raw_match.get("durationSec", 0) or 0)

    if duration < 100:
        pending_matches.pop(game_id, None)
        log.info(f"Match {game_id} ignoré : durée trop faible ({duration}s).")
        return False

    # Si pas de endAt, match encore en cours.
    if not end_at_dt:
        pending_matches.pop(game_id, None)
        log.info(f"Match {game_id} ignoré : endAt absent, partie probablement encore en cours.")
        return False

    elapsed = (utc_now() - end_at_dt).total_seconds()

    if elapsed < FINALIZE_DELAY_SECONDS:
        log.info(
            f"Match {game_id} terminé mais encore en délai minimum "
            f"({int(elapsed)}/{FINALIZE_DELAY_SECONDS}s)."
        )
        return False

    new_snapshot = build_match_snapshot(raw_match)

    if game_id not in pending_matches:
        pending_matches[game_id] = {
            "snapshot": new_snapshot,
            "stable_count": 0,
        }
        log.info(f"Match {game_id} en attente de stabilité des stats (1er snapshot).")
        return False

    old_snapshot = pending_matches[game_id]["snapshot"]

    if new_snapshot == old_snapshot:
        pending_matches[game_id]["stable_count"] += 1
        log.info(
            f"Match {game_id} snapshot stable "
            f"({pending_matches[game_id]['stable_count']}/{REQUIRED_STABLE_POLLS})."
        )
    else:
        pending_matches[game_id]["snapshot"] = new_snapshot
        pending_matches[game_id]["stable_count"] = 0
        log.info(f"Match {game_id} snapshot modifié, attente prolongée.")
        return False

    if pending_matches[game_id]["stable_count"] < REQUIRED_STABLE_POLLS:
        return False

    pending_matches.pop(game_id, None)
    log.info(f"Match {game_id} confirmé : stats stables, prêt pour traitement.")
    return True



def check_new_games_for_player(player: Player) -> bool:
    if not player.minecraft_name:
        log.warning(f"Joueur ignoré car minecraft_name manquant : id={player.id}")
        return False

    games = api.get_player_games(player.minecraft_name, page=0, size=1)

    for game in games:
        game_id = game.get("gameId")
        if not game_id:
            continue

        if is_game_processed(game_id):
            continue

        if not is_match_ready_for_processing(game_id):
            continue

        if not try_reserve_game(game_id):
            continue

        log.info(f"Nouvelle game confirmée et réservée : {game_id} pour {player.minecraft_name}")

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

                return True

            elif result.get("status") == "already_processed":
                log.info(f"Game {game_id} déjà traitée côté matches.")
                return True

            elif result.get("status") == "error":
                log.error(f"Erreur logique process_match({game_id}) : {result}")
                unreserve_game(game_id)
                return False

            else:
                log.warning(
                    f"Statut inattendu pour process_match({game_id}) : {result}"
                )
                unreserve_game(game_id)
                return False

        except Exception as e:
            log.error(f"Erreur process_match({game_id}) : {e}")
            unreserve_game(game_id)
            return False

    return False


def check_all_ranked_players():
    players = get_ranked_players()

    if not players:
        log.warning("Aucun joueur active_ranked=True trouvé.")
        return

    log.info(f"{len(players)} joueur(x) ranked surveillé(s).")

    for player in players:
        try:
            processed = check_new_games_for_player(player)
            if processed:
                log.info("Un match a été traité sur ce cycle, arrêt du scan jusqu'au prochain poll.")
                return
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

        time.sleep(60)
