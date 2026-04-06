import inspect
print("DEBUG FILE =", __file__)
print("DEBUG calculate_match_mmr exists soon")
from typing import Any


MAX_DELTA = 18
MIN_DELTA = -18

SCENARIO_BALANCE = {
    "NoHeal": -3,
    "Mutation": -3,
    "Vampire": -3,
    "Punch": -2,
    "ProtectTheKing": -2,
    "DoubleTranchant": -2,
    "Bomb": -2,
    "Glowing": -1,
    "CAC": -1,
    "Rush": -1,
    "InitialD": 0,
    "Swap": 0,
    "BlackOut": 1,
    "ScénarioChoose": 1,
    "DoubleCoeur": 1,
    "DernierSurvivant": 1,
    "LuckyShoot": 2,
    "Sacrifice": 2,
    "Invisible": 3,
    "IEM": 3,
    "MapRDM": 3,
}


def clamp(value: int) -> int:
    if value > MAX_DELTA:
        return MAX_DELTA
    if value < MIN_DELTA:
        return MIN_DELTA
    return value


def human_score(player: dict[str, Any], winner: str) -> int:
    score = 0

    kills = int(player.get("kills", 0) or 0)
    assists = int(player.get("assists", 0) or 0)
    is_survivor = bool(player.get("is_survivor", False))
    survival_time = int(player.get("survival_time", 0) or 0)

    # survie : +1 point / 60 sec, cap 5
    score += min(survival_time // 60, 5)

    # victoire humains si survivant
    if winner == "humains" and is_survivor:
        score += 2
    else:
        score -= 3

    # kills
    score += kills * 2

    # assists
    score += assists * 1

    # impact très faible
    if not is_survivor and kills == 0 and assists == 0:
        score -= 2

    return score


def infected_score(player: dict[str, Any], winner: str) -> int:
    score = 0

    kills = int(player.get("kills", 0) or 0)
    assists = int(player.get("assists", 0) or 0)
    infections = int(player.get("infections", 0) or 0)
    dmg = int(player.get("dmg", 0) or 0)

    if winner == "zombies":
        score += 2
    else:
        score -= 3

    # damage : 1 point / 50 dmg, cap 5
    dmg_points = min(dmg // 50, 5)
    score += dmg_points

    # infections
    score += infections * 3

    # kills
    score += kills * 1

    # assists
    score += assists * 1

    # impact faible
    if dmg < 80 and infections == 0 and kills == 0 and assists == 0:
        score -= 4

    return score


def firstz_score(player: dict[str, Any], winner: str) -> int:
    score = 0

    kills = int(player.get("kills", 0) or 0)
    assists = int(player.get("assists", 0) or 0)
    infections = int(player.get("infections", 0) or 0)
    dmg = int(player.get("dmg", 0) or 0)

    if winner == "zombies":
        score += 4
    else:
        score -= 6

    # damage : 1 point / 45 dmg, cap 5
    dmg_points = min(dmg // 45, 5)
    score += dmg_points

    # infections
    score += infections * 3

    # kills
    score += kills * 1

    # assists
    score += assists * 1

    # impact faible
    if dmg < 100 and infections == 0 and kills == 0 and assists == 0:
        score -= 4

    return score


def apply_map_modifier(score: int, role: str, winner: str, map_size: str | None) -> int:
    if not map_size:
        return score

    if map_size == "small":
        if winner == "humains" and role == "humain":
            score += 1

    elif map_size == "large":
        if winner == "zombies":
            if role == "infected":
                score += 1
            elif role == "firstz":
                score += 2

    return score


def apply_scenario_modifier(score: int, role: str, winner: str, scenarios: list[str] | None) -> int:
    if not scenarios:
        return score

    values = [SCENARIO_BALANCE.get(s, 0) for s in scenarios]

    if not values:
        return score

    avg = sum(values) / len(values)

    # scénarios favorables aux humains, mais zombies gagnent
    if avg > 0 and winner == "zombies":
        if role == "infected":
            score += 1
        elif role == "firstz":
            score += 2

    # scénarios favorables aux zombies, mais humains gagnent
    elif avg < 0 and winner == "humains":
        if role == "humain":
            score += 1

    return score


def apply_new_player_boost(score: int, games_played: int) -> int:
    if games_played < 5:
        score = int(score * 1.10)
    elif games_played < 10:
        score = int(score * 1.05)

    return score


def calculate_player_mmr_delta(player: dict[str, Any], match_data: dict[str, Any], games_played: int = 0) -> int:
    role = player.get("role", "humain")
    winner = match_data.get("winner", "")
    map_size = match_data.get("map_size")
    scenarios = match_data.get("scenarios_internal", [])

    score = 0

    if role == "humain":
        score = human_score(player, winner)
    elif role == "infected":
        score = infected_score(player, winner)
    elif role == "firstz":
        score = firstz_score(player, winner)

    score = apply_map_modifier(score, role, winner, map_size)
    score = apply_scenario_modifier(score, role, winner, scenarios)
    score = apply_new_player_boost(score, games_played)
    score = clamp(score)

    return score


def calculate_match_mmr(parsed_match: dict[str, Any], players_games: dict[str, int]) -> list[dict[str, Any]]:
    results = []

    for player in parsed_match.get("players", []):
        player_id = str(player.get("player_id"))
        games_played = players_games.get(player_id, 0)

        delta = calculate_player_mmr_delta(player, parsed_match, games_played)

        results.append({
            "player_id": player_id,
            "delta": delta,
            "role": player.get("role"),
            "kills": player.get("kills", 0),
            "dmg": player.get("dmg", 0),
            "infections": player.get("infections", 0),
            "assists": player.get("assists", 0),
            "survival_time": player.get("survival_time", 0),
        })

    return results


print("DEBUG SIGNATURE =", inspect.signature(calculate_match_mmr))