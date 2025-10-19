import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from pathlib import Path

# ✅ Charge le fichier .env (en UTF-8 pour Windows)
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path, encoding="utf-8")

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot connecté en tant que {bot.user}")

if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERREUR : DISCORD_TOKEN non trouvé dans le .env")
    else:
        print("🔑 Token chargé avec succès.")
        bot.run(TOKEN)
