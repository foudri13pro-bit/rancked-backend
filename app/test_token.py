import os, requests
from dotenv import load_dotenv
from pathlib import Path

env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path, encoding="utf-8")

token = os.getenv("DISCORD_TOKEN", "").strip()

print("🔍 Token chargé :", repr(token[:10]) + "...")

r = requests.get(
    "https://discord.com/api/v10/users/@me",
    headers={"Authorization": f"Bot {token}"}
)

print("Status:", r.status_code)
print("Body:", r.text)
