import json
from typing import Any


def parse_winner(winner_team: str) -> str:
    """
    Convertit le winner API vers le format interne du bot.
    """
    if winner_team == "HUMANS":
        return "humains"
    if winner_team == "INFECTED":
        return "zombies"
    return "humains"


def parse_role(team_start: str, team_end: str) -> str:
    """
    Déduit le rôle interne du joueur à partir des données API.
    """
    if team_start == "INFECTED":
        return "firstz"

    if team_start == "HUMANS" and team_end == "HUMAN":
        return "humain"

    if team_start == "HUMANS" and team_end == "INFECTED":
        return "infected"

    return "humain"


def parse_is_survivor(team_start: str, team_end: str) -> bool:
    """
    Un survivant est uniquement un joueur qui a commencé HUMANS
    et qui finit HUMAN.
    """
    return team_start == "HUMANS" and team_end == "HUMAN"


def parse_scenarios(raw_scenario_id: str | None) -> list[str]:
    """
    L'API renvoie souvent une string JSON du style :
    '{"scenario1":"VAMPIRE","scenario2":"MAPRANDOM"}'

    On la transforme en liste Python :
    ["VAMPIRE", "MAPRANDOM"]
    """
    if not raw_scenario_id:
        return []

    try:
        parsed = json.loads(raw_scenario_id)
        values = [v for v in parsed.values() if v]
        return values
    except Exception:
        return []


def convert_api_scenario_to_internal(api_name: str) -> str:
    """
    Convertit le nom de scénario API vers ton nom interne bot.
    """
    mapping = {
        "NOHEAL": "NoHeal",
        "MUTATION": "Mutation",
        "VAMPIRE": "Vampire",
        "PUNCH": "Punch",
        "PROTECTTHEKING": "ProtectTheKing",
        "DOUBLETRANCHANT": "DoubleTranchant",
        "BOMB": "Bomb",
        "GLOWING": "Glowing",
        "CAC": "CAC",
        "RUSH": "Rush",
        "INITIALD": "InitialD",
        "SWAP": "Swap",
        "BLACKOUT": "BlackOut",
        "SCENARIOCHOOSE": "ScénarioChoose",
        "DOUBLECOEUR": "DoubleCoeur",
        "DERNIERSURVIVANT": "DernierSurvivant",
        "LUCKYSHOOT": "LuckyShoot",
        "SACRIFICE": "Sacrifice",
        "INVISIBLE": "Invisible",
        "IEM": "IEM",
        "MAPRANDOM": "MapRDM",
    }
    return mapping.get(api_name.upper(), api_name)


def parse_players(players: list[dict[str, Any]], duration_sec: int) -> list[dict[str, Any]]:
    """
    Convertit la liste players API vers un format exploitable par calculate_mmr().
    """
    result = []

    for p in players:
        team_start = p.get("teamStart", "")
        team_end = p.get("teamEnd", "")

        role = parse_role(team_start, team_end)
        is_survivor = parse_is_survivor(team_start, team_end)

        # L'API Zenavia ne fournit pas actuellement l'instant exact d'infection/mort.
        # On utilise donc une version fiable mais simple :
        # - humain survivant -> durée complète du match
        # - sinon -> 0
        survival_time = duration_sec if is_survivor else 0

        result.append({
            "player_id": p.get("playerId"),
            "team_start": team_start,
            "team_end": team_end,
            "role": role,
            "is_survivor": is_survivor,
            "kills": int(p.get("kills", 0) or 0),
            "assists": int(p.get("assists", 0) or 0),
            "dmg": int(p.get("damageGiven", 0) or 0),
            "deaths": int(p.get("deaths", 0) or 0),
            "infections": int(p.get("infections", 0) or 0),
            "survival_time": survival_time,
        })

    return result


def parse_game_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """
    Convertit une game API complète vers un format ranked exploitable.
    """
    duration_sec = int(detail.get("durationSec", 0) or 0)

    raw_scenarios = parse_scenarios(detail.get("scenarioId"))
    internal_scenarios = [
        convert_api_scenario_to_internal(s)
        for s in raw_scenarios
    ]

    parsed = {
        "game_id": detail.get("gameId"),
        "map_id": detail.get("mapId"),
        "winner": parse_winner(detail.get("winnerTeam", "")),
        "first_zombies": detail.get("firstZombies"),
        "winner_player": detail.get("winnerPlayer"),
        "duration_sec": duration_sec,
        "rate": detail.get("rate"),
        "scenarios_api": raw_scenarios,
        "scenarios_internal": internal_scenarios,
        "players": parse_players(detail.get("players", []), duration_sec),
    }

    return parsed