from api.zenavia_api import ZenaviaAPI
from game_parser import parse_game_detail
from bot.bot import calculate_mmr

api = ZenaviaAPI()

print("=== LOGIN ===")
token = api.login()
print("Token JWT récupéré :", token[:25] + "...")

print("\n=== GAME DETAIL ===")
detail = api.get_game_detail(10269)
print(detail)

print("\n=== GAME PARSED ===")
parsed = parse_game_detail(detail)
print(parsed)

print("\n=== TEST MMR ===")
for player in parsed["players"]:
    mmr_change = calculate_mmr(
        role=player["role"],
        winner=parsed["winner"],
        is_survivor=player["is_survivor"],
        kills=player["kills"],
        assists=player["assists"],
        dmg=player["dmg"],
        survival_time=player["survival_time"],
        scenarios=parsed["scenarios_internal"],
        map_name=None,  # on branchera la map plus tard avec mapId -> nom
    )

    print(
        f"player_id={player['player_id']} | "
        f"role={player['role']} | "
        f"survivor={player['is_survivor']} | "
        f"kills={player['kills']} | "
        f"assists={player['assists']} | "
        f"dmg={player['dmg']} | "
        f"MMR={mmr_change:+}"
    )