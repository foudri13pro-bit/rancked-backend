# app/bot.py
import os
import sys
import traceback
import threading
from pathlib import Path

import discord
from discord.ext import commands
from discord import app_commands

from dotenv import load_dotenv
from flask import Flask

# === Charger le .env ===
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path, encoding="utf-8")

TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0"))  # 0 = sync global

# === Mini serveur web pour Render (faux "site" pour port binding) ===
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Zenavia Bot actif"

def run_web():
    # PORT est injecté par Render pour les Web Services
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Lancer Flask en thread pour ne pas bloquer le bot Discord
threading.Thread(target=run_web, daemon=True).start()

# === Bot Discord ===
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Helper : ACK < 3s pour éviter "l’application ne répond pas"
async def ensure_defer(inter: discord.Interaction, *, thinking=True, ephemeral=True):
    if not inter.response.is_done():
        await inter.response.defer(thinking=thinking, ephemeral=ephemeral)

# Handler global d’erreurs slash
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    print("Slash error:", repr(error), file=sys.stderr)
    traceback.print_exc()
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Erreur interne.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Erreur interne.", ephemeral=True)
    except Exception:
        pass

@bot.event
async def on_ready():
    try:
        if GUILD_ID:
            synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        else:
            synced = await bot.tree.sync()
        print(f"Slash sync: {len(synced)} commandes")
    except Exception as e:
        print("Sync error:", e)
    print(f"✅ Bot connecté en tant que {bot.user} ({bot.user.id})")

# — Exemple de slash command safe (modèle à copier) —
@app_commands.command(name="ping", description="Test de latence")
async def ping(inter: discord.Interaction):
    await ensure_defer(inter)               # ACK immédiat
    await inter.followup.send("🏓 Pong !")  # réponse finale

# Enregistre la commande dans l’arbre (global ou guild)
if GUILD_ID:
    bot.tree.add_command(ping, guild=discord.Object(id=GUILD_ID))
else:
    bot.tree.add_command(ping)

if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERREUR : DISCORD_TOKEN non trouvé dans le .env")
        sys.exit(1)
    bot.run(TOKEN)
