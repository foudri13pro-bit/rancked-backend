from app.core.database import SessionLocal
from app.models.match import Match
from app.models.mmr_history import MMRHistory
from app.models.player import Player

from app.game_parser import parse_game_detail
from app.services.mmr_calculator import calculate_match_mmr
import inspect
print("DEBUG match_processor file =", __file__)
print("DEBUG imported calculate_match_mmr =", calculate_match_mmr)
print("DEBUG imported signature =", inspect.signature(calculate_match_mmr))
from app.api.zenavia_api import ZenaviaAPI

import logging

log = logging.getLogger("ranked_backend")

api = ZenaviaAPI()

def get_map_size_from_max_players(max_players: int) -> str:
    if max_players <= 20:
        return "small"
    if max_players <= 80:
        return "mid"
    return "large"

def process_match(match_id: str):
    db = SessionLocal()

    try:
        log.info(f"Processing match {match_id}")

        # 1) récupérer le match via l'API Zenavia
        raw_match = api.get_game_detail(int(match_id))

        import json

        print("=== RAW MATCH FULL ===")
        print(json.dumps(raw_match, indent=2, ensure_ascii=False))

        print("=== DEBUG RAW MATCH PLAYERS ===")
        for p in raw_match.get("players", []):
            print(p)

        # 2) parser le match
        parsed_match = parse_game_detail(raw_match)
        maps_index = api.get_maps_index()

        raw_map_id = str(parsed_match.get("map_id"))
        map_info = maps_index.get(raw_map_id)

        if map_info:
            parsed_match["map_name"] = map_info["name"]
            parsed_match["map_min_players"] = map_info["min_players"]
            parsed_match["map_max_players"] = map_info["max_players"]
            parsed_match["map_size"] = get_map_size_from_max_players(map_info["max_players"])
        else:
            parsed_match["map_name"] = raw_map_id
            parsed_match["map_min_players"] = 0
            parsed_match["map_max_players"] = 0
            parsed_match["map_size"] = "unknown"

        # 3) vérifier si déjà traité
        existing = db.query(Match).filter(Match.external_id == match_id).first()
        if existing:
            log.info("Match déjà traité")
            return {"status": "already_processed"}

        
        players_games = {}

        for p in parsed_match.get("players", []):
            pid = str(p.get("player_id"))

            player_obj = db.query(Player).filter(
                Player.zenavia_player_id == pid
            ).first()

            if player_obj:
                players_games[pid] = player_obj.games_played
            else:
                players_games[pid] = 0# 4) calcul MMR
        mmr_results = calculate_match_mmr(parsed_match, players_games)

        # 5) sauvegarder le match
        match = Match(
            external_id=match_id,
            map_name=str(parsed_match.get("map_name")),
            scenario=", ".join(parsed_match.get("scenarios_internal", [])),
            winner_team=parsed_match.get("winner"),
            raw_data=raw_match,
            parsed_data=parsed_match,
        )

        db.add(match)
        db.flush()

        # 6) mettre à jour uniquement les joueurs inscrits ET active_ranked=True
        players_updated = 0

        for result in mmr_results:
            player_id = str(result.get("player_id"))

            player = db.query(Player).filter(
                Player.zenavia_player_id == player_id
            ).first()

            if not player:
                log.info(f"Joueur {player_id} ignoré (non inscrit ranked)")
                continue

            if not player.active_ranked:
                log.info(f"Joueur {player.minecraft_name} ignoré (active_ranked=False)")
                continue

            old_mmr = player.current_mmr or 1000
            delta = int(result.get("delta", 0) or 0)
            new_mmr = old_mmr + delta

            player.current_mmr = new_mmr
            player.games_played += 1
            player.kills += int(result.get("kills", 0) or 0)
            player.infections += int(result.get("infections", 0) or 0)

            if int(result.get("survival_time", 0) or 0) >= 90:
                player.survivals += 1

            if result.get("role") == "firstz":
                player.first_z_count += 1

            if parsed_match.get("winner") == "humains" and result.get("role") == "humain":
                player.wins += 1
            elif parsed_match.get("winner") == "zombies" and result.get("role") in ["infected", "firstz"]:
                player.wins += 1
            else:
                player.losses += 1

            history = MMRHistory(
                match_id=match.id,
                player_id=player.id,
                old_mmr=old_mmr,
                new_mmr=new_mmr,
                delta=delta,
                meta=result,
            )
            db.add(history)
            players_updated += 1

        db.commit()

        log.info("Match + MMR sauvegardés")

        top_changes = []
        player_summaries = []

        for result in mmr_results:
            player_id = str(result.get("player_id"))

            player_obj = db.query(Player).filter(
                Player.zenavia_player_id == player_id
            ).first()

            if not player_obj:
                continue

            display_name = f"Player {player_id}"
            if player_obj.minecraft_name:
                display_name = player_obj.minecraft_name
            elif player_obj.zenavia_player_id:
                display_name = f"Zenavia#{player_obj.zenavia_player_id}"
            else:
                display_name = f"player_{player_obj.id}"

            delta = int(result.get("delta", 0) or 0)

            player_row = {
                "name": display_name,
                "delta": delta,
                "role": result.get("role", "unknown"),
                "kills": int(result.get("kills", 0) or 0),
                "infections": int(result.get("infections", 0) or 0),
                "dmg": int(result.get("dmg", 0) or 0),
                "survival_time": int(result.get("survival_time", 0) or 0),
                "is_registered": player_obj is not None,
                "active_ranked": bool(player_obj.active_ranked) if player_obj else False,
            }

            player_summaries.append(player_row)
            top_changes.append({
                "name": display_name,
                "delta": delta,
            })

        top_changes.sort(key=lambda x: x["delta"], reverse=True)
        player_summaries.sort(key=lambda x: x["delta"], reverse=True)

        return {
            "status": "processed",
            "match_id": match.id,
            "players_updated": players_updated,
            "winner": parsed_match.get("winner"),
            "map_name": str(parsed_match.get("map_name")),
            "scenarios": parsed_match.get("scenarios_internal", []),
            "top_changes": top_changes[:5],
            "player_summaries": player_summaries[:10],
            "duration": int(parsed_match.get("duration", 0) or 0),
        }

    except Exception as e:
        db.rollback()
        log.error(e)
        return {"status": "error", "error": str(e)}

    finally:
        db.close()