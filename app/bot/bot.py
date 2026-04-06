from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import discord
import psycopg2
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv, set_key
from psycopg2.extras import DictCursor
from sqlalchemy.exc import IntegrityError

from app.api.zenavia_api import ZenaviaAPI
from app.core.database import SessionLocal
from app.models.match import Match
from app.models.mmr_history import MMRHistory
from app.models.player import Player

api = ZenaviaAPI()

# =========================
#          LOGGING
# =========================
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ranked_infected")

# =========================
#          CONFIG
# =========================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

REGISTRE_MORTS_CHANNEL_ID = 1423818703010664570
HALL_CHANNEL_ID = 1423665644519297034  # 👑・hall-des-légendes

# Intents Discord
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# —— Rangs par MMR
RANKS: List[Tuple[int, str]] = [
    (2500, "🔥 Alpha-Z"),
    (2000, "💀 Apocalypse"),
    (1500, "🧌 Mutant"),
    (1000, "🧟 Zombie"),
    (0, "🪦 Survivant"),
    (-10**9, "🌿 Réfugié"),
]


def get_rank(mmr: int) -> str:
    for threshold, name in RANKS:
        if mmr >= threshold:
            return name
    return RANKS[-1][1]


def rank_color(rank_label: str) -> discord.Color:
    if "Alpha-Z" in rank_label:
        return discord.Color.red()
    if "Mutant" in rank_label:
        return discord.Color.purple()
    if "Zombie" in rank_label:
        return discord.Color.green()
    if "Survivant" in rank_label:
        return discord.Color.light_grey()
    return discord.Color.blue()


# =========================
#       DB UTILITAIRES
# =========================
def connect_db():
    """
    Connexion à la base Neon (Postgres) via DATABASE_URL.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL n'est pas défini (URL Neon).")
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
    return conn


def init_db() -> None:
    """Crée les tables si absentes (version Postgres, idempotent)."""
    with connect_db() as conn:
        c = conn.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS players (
            discord_id TEXT PRIMARY KEY,
            minecraft_name TEXT NOT NULL,
            mmr INTEGER DEFAULT 1000,
            wins_humain INTEGER DEFAULT 0,
            wins_zombie INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            kills_zombie INTEGER DEFAULT 0,
            kills_humain INTEGER DEFAULT 0,
            assists INTEGER DEFAULT 0,
            dmg_dealt INTEGER DEFAULT 0,
            survival_time_best INTEGER DEFAULT 0,
            survival_time_avg INTEGER DEFAULT 0,
            last_change INTEGER DEFAULT 0,
            season_id INTEGER DEFAULT 1,
            active_ranked INTEGER DEFAULT 1
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            match_id SERIAL PRIMARY KEY,
            date TEXT NOT NULL,
            winner TEXT NOT NULL
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS match_players (
            id SERIAL PRIMARY KEY,
            match_id INTEGER,
            discord_id TEXT,
            role TEXT,
            kills INTEGER,
            dmg INTEGER,
            mmr_change INTEGER,
            survivor INTEGER,
            FOREIGN KEY (match_id) REFERENCES matches(match_id),
            FOREIGN KEY (discord_id) REFERENCES players(discord_id)
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)

        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS last_change INTEGER DEFAULT 0")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS season_id INTEGER DEFAULT 1")
        c.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS active_ranked INTEGER DEFAULT 1")

        conn.commit()


def fetch_player(discord_id: int) -> Optional[dict]:
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT minecraft_name, mmr, last_change, wins_humain, wins_zombie, losses,
                   kills_zombie, kills_humain, assists, dmg_dealt, season_id
            FROM players WHERE discord_id = %s
        """, (str(discord_id),))
        return c.fetchone()


def upsert_player(discord_id: int, minecraft_name: str) -> Tuple[bool, str]:
    """Retourne (created, message)."""
    try:
        with connect_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO players (discord_id, minecraft_name) VALUES (%s, %s)",
                (str(discord_id), minecraft_name)
            )
            conn.commit()
            return True, "créé"
    except Exception:
        return False, "existe"


def update_player(
    c,
    discord_id: int,
    *,
    mmr_change: int = 0,
    wins_h: int = 0,
    wins_z: int = 0,
    losses: int = 0,
    kills_z: int = 0,
    kills_h: int = 0,
    assists: int = 0,
    dmg: int = 0,
) -> None:
    c.execute("""
        UPDATE players
        SET mmr = mmr + %s,
            last_change = %s,
            wins_humain = wins_humain + %s,
            wins_zombie = wins_zombie + %s,
            losses = losses + %s,
            kills_zombie = kills_zombie + %s,
            kills_humain = kills_humain + %s,
            assists = assists + %s,
            dmg_dealt = dmg_dealt + %s
        WHERE discord_id = %s
    """, (mmr_change, mmr_change, wins_h, wins_z, losses, kills_z, kills_h, assists, dmg, str(discord_id)))


def current_season() -> int:
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("SELECT MAX(season_id) FROM players")
        return c.fetchone()[0] or 1


# =========================
#   CONFIG BOT (clé/valeur)
# =========================
def set_config(key: str, value: str):
    with connect_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO bot_config (key, value) VALUES (%s, %s) "
            "ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
            (key, value)
        )
        conn.commit()


def get_config(key: str) -> Optional[str]:
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM bot_config WHERE key = %s", (key,))
        row = c.fetchone()
        return row[0] if row else None


# =========================
#    CONFIG .ENV PERSISTANTE
# =========================
def set_env_value(key: str, value: str):
    try:
        os.environ[key] = value
        set_key(".env", key, value)
        log.info(f"[.env] {key} mis à jour -> {value}")
    except Exception as e:
        log.warning(f"[.env] Impossible de mettre à jour {key}: {e}")


def get_env_value(key: str) -> Optional[str]:
    return os.getenv(key)


# =========================
#        BOT SETUP
# =========================
class RankedBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.synced = False

    async def setup_hook(self) -> None:
        self.add_view(HallPaginationView())


# =========================
#     HALL PAGINATION
# =========================
HALL_PAGE_SIZE = 10
hall_pages_cache: dict[int, list[discord.Embed]] = {}


def _hall_flair(rank_label: str) -> str:
    if "Alpha-Z" in rank_label:
        return "🔥 Porteur du fléau originel"
    if "Apocalypse" in rank_label:
        return "💀 Incarnation du chaos"
    if "Mutant" in rank_label:
        return "🧌 Déformation de la chair"
    if "Zombie" in rank_label:
        return "🧟 Chair affamée"
    return "🌿 Survivant fragile"


def build_hall_embeds(rows: list[Player], page_size: int = HALL_PAGE_SIZE) -> list[discord.Embed]:
    if not rows:
        embed = discord.Embed(
            title="🏛️ Hall des Légendes",
            description="*Aucun nom n’a encore été gravé dans la pierre...*",
            color=discord.Color.dark_grey()
        )
        embed.set_footer(text="Page 1/1 • 0 joueurs classés")
        return [embed]

    embeds = []
    total_players = len(rows)
    total_pages = (total_players + page_size - 1) // page_size
    medals = ["👑", "🥈", "🥉"]

    for page_index in range(total_pages):
        start = page_index * page_size
        end = start + page_size
        page_rows = rows[start:end]

        embed = discord.Embed(
            title="━━━━━━━━━ 🏛️ HALL DES LÉGENDES ━━━━━━━━━",
            description="⚔️ Classement vivant du camp",
            color=discord.Color.gold()
        )

        for local_index, player in enumerate(page_rows, start=1):
            global_rank = start + local_index
            mmr = player.current_mmr or 1000
            rank_label = get_rank(mmr)
            flair = _hall_flair(rank_label)

            display_name = (
                player.minecraft_name
                or (f"Zenavia#{player.zenavia_player_id}" if player.zenavia_player_id else f"player_{player.id}")
            )

            prefix = medals[global_rank - 1] if global_rank <= 3 else f"#{global_rank}"

            if global_rank <= 3:
                embed.add_field(
                    name=f"{prefix} {display_name} — {rank_label}",
                    value=f"{flair}\n🏆 {mmr} MMR",
                    inline=False
                )
            else:
                embed.add_field(
                    name=f"{prefix} {display_name}",
                    value=f"{rank_label} | {mmr} MMR",
                    inline=False
                )

        embed.set_footer(
            text=f"Page {page_index + 1}/{total_pages} • {total_players} joueurs classés"
        )
        embeds.append(embed)

    return embeds


class HallPaginationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def _get_page_info(self, guild_id: int, message_id: int) -> tuple[list[discord.Embed], int]:
        embeds = hall_pages_cache.get(message_id, [])
        if not embeds:
            fallback = discord.Embed(
                title="🏛️ Hall des Légendes",
                description="Le classement n’est pas encore prêt ou a été réinitialisé.",
                color=discord.Color.dark_grey()
            )
            fallback.set_footer(text="Page 1/1")
            return [fallback], 0

        current_page = 0
        return embeds, current_page

    def _extract_page_from_footer(self, embed: discord.Embed) -> int:
        if not embed.footer or not embed.footer.text:
            return 0

        text = embed.footer.text
        # format attendu : "Page X/Y • ..."
        if "Page " not in text or "/" not in text:
            return 0

        try:
            page_part = text.split("Page ", 1)[1].split("•", 1)[0].strip()
            current = int(page_part.split("/")[0].strip())
            return max(current - 1, 0)
        except Exception:
            return 0

    async def _change_page(self, interaction: discord.Interaction, direction: int):
        message = interaction.message
        if not message:
            await interaction.response.send_message("❌ Message introuvable.", ephemeral=True)
            return

        embeds = hall_pages_cache.get(message.id)
        if not embeds:
            await interaction.response.send_message(
                "⚠️ Le cache du Hall a été perdu. Réactualise le Hall avec une mise à jour du classement.",
                ephemeral=True
            )
            return

        current_page = 0
        if message.embeds:
            current_page = self._extract_page_from_footer(message.embeds[0])

        new_page = current_page + direction
        if new_page < 0:
            new_page = 0
        if new_page >= len(embeds):
            new_page = len(embeds) - 1

        await interaction.response.edit_message(
            embed=embeds[new_page],
            view=self
        )

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.secondary, custom_id="hall_prev_page")
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._change_page(interaction, -1)

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.secondary, custom_id="hall_next_page")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._change_page(interaction, 1)

bot = RankedBot()

# =========================
#            UI
# =========================
async def _get_or_create_hall_message(guild: discord.Guild) -> Optional[discord.Message]:
    channel = guild.get_channel(HALL_CHANNEL_ID) or discord.utils.get(
        guild.text_channels, name="👑・hall-des-légendes"
    )
    if not channel:
        log.warning("⚠️ Aucun salon '👑・hall-des-légendes' trouvé (ni ID ni nom).")
        return None

    bot_user = guild.me
    target: discord.Message | None = None

    try:
        async for m in channel.history(limit=20):
            if m.author == bot_user:
                target = m
                break
    except discord.Forbidden:
        log.warning(f"[Hall] Pas la permission de lire l'historique dans #{channel.name}")
        return None
    except Exception as e:
        log.warning(f"[Hall] Erreur lors de la lecture de l'historique : {e}")
        return None

    if target is None:
        placeholder = discord.Embed(
            title="🏛️ Hall des Légendes — Saison 1",
            description="*Chaque saison, les plus grands inscrivent leur nom dans ces murs.*",
            color=discord.Color.gold()
        )
        placeholder.add_field(name="En attente...", value="Le premier match n’a pas encore eu lieu.", inline=False)
        try:
            target = await channel.send(embed=placeholder)
            log.info(f"[Hall] Création du message du Hall (id={target.id})")
        except Exception as e:
            log.warning(f"[Hall] Impossible de créer le message du Hall : {e}")
            return None

    return target


async def update_hall(guild: discord.Guild):
    msg = await _get_or_create_hall_message(guild)
    if msg is None:
        return

    db = SessionLocal()

    try:
        rows = (
            db.query(Player)
            .filter(Player.active_ranked == True)
            .order_by(Player.current_mmr.desc())
            .all()
        )

        embeds = build_hall_embeds(rows, page_size=HALL_PAGE_SIZE)
        hall_pages_cache[msg.id] = embeds

        await msg.edit(
            embed=embeds[0],
            view=HallPaginationView()
        )
        log.info("[Hall] Hall des Légendes paginé mis à jour.")

    except Exception as e:
        log.warning(f"[Hall] Erreur update: {e}")

    finally:
        db.close()


async def send_match_report(guild: discord.Guild, match_data: dict):
    channel = guild.get_channel(REGISTRE_MORTS_CHANNEL_ID)

    if not channel:
        channel = discord.utils.get(guild.text_channels, name="🪦・registre-des-morts")

    if not channel:
        log.warning("⚠️ Aucun salon 'registre-des-morts' trouvé.")
        return

    match_id = match_data.get("match_id", "inconnu")
    winner_raw = str(match_data.get("winner", "inconnu")).lower()
    map_name = match_data.get("map_name", "Inconnue")
    scenarios = match_data.get("scenarios", [])
    players_updated = int(match_data.get("players_updated", 0) or 0)
    players_total = int(match_data.get("players_total", players_updated) or players_updated)
    player_summaries = match_data.get("player_summaries", [])
    duration = int(match_data.get("duration", 0) or 0)

    def format_duration(seconds: int) -> str:
        if seconds <= 0:
            return "Inconnue"
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes:02d}m {secs:02d}s"

    def winner_label(value: str) -> str:
        if value == "humains":
            return "Victoire des Humains"
        if value == "zombies":
            return "Victoire des Zombies"
        return "Issue inconnue"

    def winner_color(value: str) -> discord.Color:
        if value == "humains":
            return discord.Color.green()
        if value == "zombies":
            return discord.Color.red()
        return discord.Color.dark_grey()

    def role_icon(role: str) -> str:
        if role == "humain":
            return "🏹"
        if role == "infected":
            return "🧟"
        if role == "firstz":
            return "🦠"
        return "❔"

    def build_analysis(value: str, scenarios_list: list[str], mvp_name: str) -> str:
        scenario_text = ", ".join(scenarios_list) if scenarios_list else "aucun scénario spécial"
        if value == "humains":
            return (
                f"La résistance a tenu la zone malgré {scenario_text}. "
                f"Le sujet **{mvp_name}** a dépassé les seuils attendus."
            )
        if value == "zombies":
            return (
                f"La contamination a submergé la zone sous {scenario_text}. "
                f"L’entité **{mvp_name}** a été identifiée comme facteur majeur de rupture."
            )
        return (
            f"Les données du conflit restent partielles. "
            f"Le protocole signale néanmoins **{mvp_name}** comme sujet notable."
        )

    scenario_text = ", ".join(scenarios) if scenarios else "Aucun"
    date_text = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M")
    result_text = winner_label(winner_raw)

    mvp = None
    if player_summaries:
        mvp = max(player_summaries, key=lambda x: x.get("delta", 0))

    mvp_name = mvp["name"] if mvp else "Inconnu"
    mvp_delta = int(mvp.get("delta", 0)) if mvp else 0
    mvp_role = role_icon(mvp.get("role", "unknown")) if mvp else "❔"
    mvp_kills = int(mvp.get("kills", 0)) if mvp else 0
    mvp_infections = int(mvp.get("infections", 0)) if mvp else 0
    mvp_dmg = int(mvp.get("dmg", 0)) if mvp else 0
    mvp_survival = int(mvp.get("survival_time", 0)) if mvp else 0

    embed = discord.Embed(
        title="☣️ ARCHIVES Z.E.N.A. — RAPPORT POST-MATCH",
        description=(
            "_Analyse terminée. Les données biométriques du conflit ont été archivées._\n"
            "_Le protocole Z.E.N.A. publie ci-dessous le rapport d’incident._"
        ),
        color=winner_color(winner_raw)
    )

    # Bloc résumé match
    embed.add_field(name="🗺️ Zone", value=f"**{map_name}**", inline=True)
    embed.add_field(name="🏆 Issue", value=f"**{result_text}**", inline=True)
    embed.add_field(name="⏱️ Durée", value=f"**{format_duration(duration)}**", inline=True)

    embed.add_field(name="🎭 Scénarios", value=f"**{scenario_text}**", inline=False)

    embed.add_field(name="👥 Joueurs", value=f"**{players_total}** total", inline=True)
    embed.add_field(name="📊 Ranked pris en compte", value=f"**{players_updated}**", inline=True)
    embed.add_field(name="📜 Rapport", value=f"**#{match_id}**", inline=True)

    embed.add_field(name="🕒 Archivage", value=f"**{date_text} UTC**", inline=False)

    # Bloc MVP plus lisible
    if mvp:
        embed.add_field(
            name="🌟 Sujet prioritaire détecté — MVP",
            value=(
                f"**{mvp_name}** {mvp_role}\n"
                f"📈 **MMR** : `{mvp_delta:+}`\n"
                f"⚔️ **Kills** : `{mvp_kills}`\n"
                f"☣️ **Infections** : `{mvp_infections}`\n"
                f"💥 **Dégâts** : `{mvp_dmg}`\n"
                f"⏳ **Survie** : `{mvp_survival}s`"
            ),
            inline=False
        )

    # Top joueurs plus lisible
    if player_summaries:
        ranking_lines = []
        for i, row in enumerate(player_summaries[:5], start=1):
            medal = "👑" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"#{i}"
            name = row.get("name", "Inconnu")
            delta = int(row.get("delta", 0) or 0)
            kills = int(row.get("kills", 0) or 0)
            infections = int(row.get("infections", 0) or 0)
            dmg = int(row.get("dmg", 0) or 0)
            survival = int(row.get("survival_time", 0) or 0)
            icon = role_icon(row.get("role", "unknown"))

            ranking_lines.append(
                f"{medal} {icon} **{name}**\n"
                f"📈 `{delta:+} MMR` | ⚔️ `{kills}` | ☣️ `{infections}` | 💥 `{dmg}` | ⏳ `{survival}s`"
            )

        embed.add_field(
            name="🏅 Classement d’efficacité",
            value="\n\n".join(ranking_lines),
            inline=False
        )
    else:
        embed.add_field(
            name="🏅 Classement d’efficacité",
            value="Aucune variation de MMR enregistrée.",
            inline=False
        )

    embed.add_field(
        name="🤖 Analyse Z.E.N.A.",
        value=build_analysis(winner_raw, scenarios, mvp_name),
        inline=False
    )

    embed.set_footer(text="Projet Z.E.N.A. • Registre des morts • Observation continue")
    await channel.send(embed=embed)


async def setup_or_update_hall(guild: discord.Guild):
    await update_hall(guild)


# =========================
#     HELPERS & EVENTS
# =========================
def find_channel(guild: discord.Guild, *fragments: str) -> Optional[discord.TextChannel]:
    fragments = [f.lower() for f in fragments]
    for ch in guild.text_channels:
        for frag in fragments:
            if frag in ch.name.lower():
                log.info(f"✅ Match salon: '{frag}' -> {ch.name}")
                return ch
    return None


async def ensure_or_update_message(
    channel: discord.TextChannel,
    *,
    embed: discord.Embed,
):
    if channel is None:
        return

    bot_user = channel.guild.me
    target: discord.Message | None = None

    try:
        async for m in channel.history(limit=20):
            if m.author == bot_user:
                target = m
                break
    except discord.Forbidden:
        log.warning(f"[ensure_or_update_message] Pas la permission de lire l'historique sur #{channel.name}")
        return
    except Exception as e:
        log.warning(f"[ensure_or_update_message] Erreur history sur #{channel.name}: {e}")
        return

    if target:
        try:
            await target.edit(content="", embed=embed)
            log.info(f"[ensure_or_update_message] ✏️ Edit d'un message existant dans #{channel.name}")
            return
        except discord.Forbidden:
            log.warning(f"[ensure_or_update_message] Forbidden: pas d'édition possible dans #{channel.name}")
            return
        except Exception as e:
            log.warning(f"[ensure_or_update_message] Erreur d'édition: {e} -> tentative de recréation")

    try:
        await channel.send(embed=embed)
        log.info(f"[ensure_or_update_message] ✅ Nouveau message créé dans #{channel.name}")
    except discord.Forbidden:
        log.error(f"[ensure_or_update_message] Forbidden pour envoyer dans #{channel.name}")
    except Exception as e:
        log.error(f"[ensure_or_update_message] Échec d’envoi dans #{channel.name}: {e}")


def build_manual_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📖 Manuel de Survie — Edition Compétitive",
        description=(
            "Bienvenue dans le mode **Ranked Infecté**.\n"
            "Ici, chaque action influence ton **MMR**, ton **rang** et ta **réputation compétitive**.\n"
            "Prépare-toi. Joue propre. Progresse."
        ),
        color=discord.Color.green()
    )

    embed.add_field(
        name="🎮 Commandes Essentielles",
        value=(
            "• 🧩 `/register [pseudo]` — Crée ton profil compétitif.\n"
            "• 🟢 `/ranked_on` — Active le mode classé.\n"
            "• 🔴 `/ranked_off` — Mode scrim / warm-up (aucun MMR).\n"
            "• 📊 `/rank` — Consulte ton rang.\n"
            "• 🧾 `/stats` — Analyse tes performances.\n"
            "• 🏆 `/leaderboard` — Classement officiel de la saison."
        ),
        inline=False
    )

    embed.add_field(
        name="⚔️ Ruleset Compétitif",
        value=(
            "**1️⃣ Identité & Intégrité**\n"
            "• Un seul compte par joueur.\n"
            "• Pseudo Minecraft obligatoire.\n"
            "• Doubles comptes / spoof → sanctions.\n\n"
            "**2️⃣ Contraintes**\n"
            "• Interdits : cheats, macros abusives, exploits.\n"
            "• AFK, throw ou sabotage → pertes MMR.\n"
            "• Respect obligatoire envers les autres joueurs.\n\n"
            "**3️⃣ Déroulement des Matchs**\n"
            "• Les matchs classés sont synchronisés automatiquement via l’API Zenavia.\n"
            "• Les résultats sont archivés dans le registre des morts.\n"
            "• Le classement est mis à jour automatiquement après traitement.\n\n"
            "**4️⃣ Discipline & Sanctions**\n"
            "• Triche = ban classé + reset.\n"
            "• Toxicité grave = sanctions Ranked.\n"
            "• Preuves acceptées : clips, screens, logs."
        ),
        inline=False
    )

    embed.add_field(
        name="📈 Calcul du MMR — Humains",
        value=(
            "• ⏱️ **Survie** → `+1` toutes les 30 sec (cap `+10`)\n"
            "• 🏆 **Victoire humain + survivant final** → `+4`\n"
            "• ⚔️ **Kill** → `+3`\n"
            "• 🤝 **Assist** → `+1`\n"
            "• ❌ **Faible impact** → `-4` si mort + aucun kill + aucune assist"
        ),
        inline=False
    )

    embed.add_field(
        name="☣️ Calcul du MMR — Zombies",
        value=(
            "• 🏆 **Victoire zombies** → `+3`\n"
            "• 💥 **Dégâts** → `+1` tous les 35 dmg (cap `+8`)\n"
            "• 🧟 **Infection** → `+4`\n"
            "• ⚔️ **Kill** → `+2`\n"
            "• 🤝 **Assist** → `+1`\n"
            "• ❌ **Faible impact** → `-4` si < 80 dmg et aucune action décisive"
        ),
        inline=False
    )

    embed.add_field(
        name="🦠 Calcul du MMR — First Z",
        value=(
            "• 🏆 **Victoire zombies** → `+7`\n"
            "• ❌ **Défaite zombies** → `-4`\n"
            "• 💥 **Dégâts** → `+1` tous les 30 dmg (cap `+9`)\n"
            "• 🧟 **Infection** → `+5`\n"
            "• ⚔️ **Kill** → `+2`\n"
            "• 🤝 **Assist** → `+1`\n"
            "• ❌ **Faible impact** → `-3` si < 100 dmg et aucune action décisive"
        ),
        inline=False
    )

    embed.add_field(
        name="🗺️ Modificateurs spéciaux",
        value=(
            "• **Petite map** : si les humains gagnent, les humains prennent `+1`\n"
            "• **Grande map** : si les zombies gagnent, zombie `+1` et First Z `+2`\n"
            "• **Scénarios défavorables renversés** : bonus si une équipe gagne malgré des conditions contre elle\n"
            "• **Nouveaux joueurs** : bonus d’apprentissage avant 10 parties"
        ),
        inline=False
    )

    embed.add_field(
        name="🧠 Règles finales du système",
        value=(
            "• Le score obtenu devient la variation de **MMR** du match.\n"
            "• Bonus débutant : moins de 5 parties = `x1.15`, moins de 10 parties = `x1.08`\n"
            "• Variation maximum par match : `+22 MMR`\n"
            "• Variation minimum par match : `-15 MMR`\n"
            "• Les joueurs en mode chill (`/ranked_off`) sont ignorés."
        ),
        inline=False
    )

    embed.add_field(
        name="🧪 Exemple rapide",
        value=(
            "**Zombie** : victoire + 2 infections + 140 dmg + 1 assist\n"
            "`+3` victoire • `+8` infections • `+4` dégâts • `+1` assist\n"
            "**Total : +16 MMR**"
        ),
        inline=False
    )

    embed.add_field(
        name="🎯 Structure des Rangs",
        value=(
            "🪦 **Survivant (0–999 MMR)** — Tier 5 : apprentissage.\n"
            "🧟 **Zombie (1000–1499 MMR)** — Tier 4 : joueurs réguliers.\n"
            "🧌 **Mutant (1500–1999 MMR)** — Tier 3 : niveau avancé.\n"
            "💀 **Apocalypse (2000–2499 MMR)** — Tier 2 : élite compétitive.\n"
            "🔥 **Alpha-Z (2500+ MMR)** — Tier 1 : sommet du ladder."
        ),
        inline=False
    )

    embed.add_field(
        name="🧬 Mentalité Classée",
        value=(
            "Le Ranked récompense l’impact réel dans une partie : survivre, infecter, assister, "
            "infliger des dégâts utiles et faire gagner son camp."
        ),
        inline=False
    )

    embed.set_footer(
        text="Projet Z.E.N.A. • Toute action a un coût • Les valeurs peuvent évoluer selon les saisons"
    )

    return embed

@bot.event
async def on_ready():
    if not bot.synced:
        await bot.tree.sync()
        bot.synced = True

    log.info(f"✅ Connecté en tant que {bot.user} ({bot.user.id})")

    if not bot.guilds:
        return
    guild = bot.guilds[0]

    log.info("📂 Salons textuels détectés :")
    for ch in guild.text_channels:
        log.info(f"- {ch.name}")

    channel_alertes = find_channel(guild, "sirene", "alertes")
    if channel_alertes:
        embed = discord.Embed(
            title="🚨 Les sirènes hurlent !",
            description=(
                "Une nouvelle silhouette franchit les barricades...\n\n"
                "Bienvenue survivant. Ici, chaque décision compte.\n\n"
                "➡️ Lis les **⚖️ lois-du-camp** pour connaître nos règles.\n"
                "➡️ Consulte le **📖 manuel-de-survie** pour apprendre à combattre l’infection.\n\n"
                "🔥 Que la survie commence."
            ),
            color=discord.Color.red()
        )
        await ensure_or_update_message(channel_alertes, embed=embed)

    channel_lois = find_channel(guild, "lois-du-camp", "lois")
    if channel_lois:
        embed = discord.Embed(
            title="⚖️ Lois du Camp",
            description=(
                "📜 Respecte les survivants – aucune insulte, aucun abus.\n"
                "🚫 Pas de spam, pas de pubs.\n"
                "🛡️ Les Sentinelles veillent à l’ordre du camp.\n"
                "🎮 Le fair play est obligatoire en Ranked.\n\n"
                "*Ignorer ces lois, c’est rejoindre la Horde.*"
            ),
            color=discord.Color.dark_grey()
        )
        await ensure_or_update_message(channel_lois, embed=embed)

    channel_manuel = find_channel(guild, "manuel", "survie")
    if channel_manuel:
        await ensure_or_update_message(
            channel_manuel,
            embed=build_manual_embed(),
        )

    await setup_or_update_hall(guild)

    try:
        ensure_rp_daemons_started()
        log.info("📻 Daemons RP (feu de camp + radio) démarrés avec délais aléatoires.")
    except Exception as e:
        log.error(f"⚠️ Impossible de démarrer les daemons RP : {e}")


# =========================
#        COMMANDES
# =========================
@bot.tree.command(name="register", description="Enregistrer ton pseudo Minecraft et créer ton profil Ranked.")
@app_commands.describe(minecraft_name="Ton pseudo Minecraft")
async def register(interaction: discord.Interaction, minecraft_name: str):
    db = SessionLocal()

    try:
        discord_id = str(interaction.user.id)
        minecraft_name = minecraft_name.strip()

        existing_discord = db.query(Player).filter(
            Player.discord_id == discord_id
        ).first()

        if existing_discord:
            await interaction.response.send_message(
                f"❌ Ton compte Discord est déjà lié au pseudo **{existing_discord.minecraft_name}**.",
                ephemeral=True
            )
            return

        zenavia_profile = api.get_player_profile(minecraft_name)

        if not zenavia_profile:
            await interaction.response.send_message(
                "❌ Impossible de récupérer les données depuis l’API Zenavia.",
                ephemeral=True
            )
            return

        zenavia_player_id = str(zenavia_profile.get("id")) if zenavia_profile.get("id") is not None else None
        returned_pseudo = zenavia_profile.get("pseudo") or minecraft_name

        if not zenavia_player_id or not returned_pseudo:
            await interaction.response.send_message(
                "❌ Réponse API invalide : identifiant ou pseudo manquant.",
                ephemeral=True
            )
            return

        existing_name = db.query(Player).filter(
            Player.minecraft_name == returned_pseudo
        ).first()

        if existing_name:
            await interaction.response.send_message(
                "❌ Ce pseudo Minecraft est déjà lié à un autre compte Discord.",
                ephemeral=True
            )
            return

        existing_zenavia_id = db.query(Player).filter(
            Player.zenavia_player_id == zenavia_player_id
        ).first()

        if existing_zenavia_id:
            await interaction.response.send_message(
                "❌ Ce compte Zenavia est déjà lié à un autre compte Discord.",
                ephemeral=True
            )
            return

        player = Player(
            discord_id=discord_id,
            minecraft_name=returned_pseudo,
            zenavia_player_id=zenavia_player_id,
            current_mmr=1000,
            games_played=0,
            wins=0,
            losses=0,
            kills=0,
            deaths=0,
            infections=0,
            survivals=0,
            first_z_count=0,
            active_ranked=True,
        )

        db.add(player)

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            await interaction.response.send_message(
                "❌ Enregistrement impossible : ce compte Discord, ce pseudo Minecraft ou cet ID Zenavia est déjà utilisé.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ {interaction.user.mention} enregistré comme **{returned_pseudo}** (ID Zenavia : {zenavia_player_id}).",
            ephemeral=True
        )

    except Exception as e:
        db.rollback()
        await interaction.response.send_message(
            f"❌ Erreur pendant l'enregistrement : {e}",
            ephemeral=True
        )

    finally:
        db.close()


@bot.tree.command(name="rank", description="Afficher ton rang et ton MMR (ou celui d'un autre).")
@app_commands.describe(member="Joueur ciblé (optionnel)")
async def rank(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    db = SessionLocal()

    try:
        user = member or interaction.user
        discord_id = str(user.id)

        player = db.query(Player).filter(
            Player.discord_id == discord_id
        ).first()

        if not player:
            await interaction.response.send_message(
                f"❌ {user.mention} n’est pas encore enregistré.",
                ephemeral=True
            )
            return

        mmr = player.current_mmr or 1000
        rank_label = get_rank(mmr)

        await interaction.response.send_message(
            f"🏅 **{player.minecraft_name or 'Inconnu'}** — {rank_label} | {mmr} MMR",
            ephemeral=True
        )

    finally:
        db.close()


@bot.tree.command(name="stats", description="Afficher les stats complètes d'un joueur.")
@app_commands.describe(member="Joueur ciblé (optionnel)")
async def stats(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    db = SessionLocal()

    try:
        user = member or interaction.user
        discord_id = str(user.id)

        player = db.query(Player).filter(
            Player.discord_id == discord_id
        ).first()

        if not player:
            await interaction.response.send_message(
                f"❌ {user.mention} n’est pas encore enregistré.",
                ephemeral=True
            )
            return

        mmr = player.current_mmr or 1000
        rank_label = get_rank(mmr)
        color = rank_color(rank_label)

        total_games = player.games_played or 0
        total_wins = player.wins or 0
        total_losses = player.losses or 0
        winrate = round((total_wins / total_games) * 100, 1) if total_games > 0 else 0

        embed = discord.Embed(
            title=f"📊 Stats de {player.minecraft_name or 'Inconnu'}",
            color=color
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        embed.add_field(name="📈 MMR", value=str(mmr), inline=True)
        embed.add_field(name="🎖 Rang", value=rank_label, inline=True)
        embed.add_field(name="🎮 Parties", value=str(total_games), inline=True)

        embed.add_field(name="🏆 Victoires", value=str(total_wins), inline=True)
        embed.add_field(name="❌ Défaites", value=str(total_losses), inline=True)
        embed.add_field(name="📊 Winrate", value=f"{winrate}%", inline=True)

        embed.add_field(name="⚔️ Kills", value=str(player.kills or 0), inline=True)
        embed.add_field(name="☠️ Morts", value=str(player.deaths or 0), inline=True)
        embed.add_field(name="🧟 Infections", value=str(player.infections or 0), inline=True)

        embed.add_field(name="🛡️ Survivals", value=str(player.survivals or 0), inline=True)
        embed.add_field(name="🦠 First Z", value=str(player.first_z_count or 0), inline=True)
        embed.add_field(name="🟢 Ranked", value="Oui" if player.active_ranked else "Non", inline=True)

        await interaction.response.send_message(embed=embed)

    finally:
        db.close()


@bot.tree.command(name="history", description="Afficher les 5 dernières parties d'un joueur.")
@app_commands.describe(member="Joueur ciblé (optionnel)")
async def history(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    db = SessionLocal()

    try:
        user = member or interaction.user
        discord_id = str(user.id)

        player = db.query(Player).filter(
            Player.discord_id == discord_id
        ).first()

        if not player:
            await interaction.response.send_message(
                f"❌ {user.mention} n’est pas encore enregistré.",
                ephemeral=True
            )
            return

        rows = (
            db.query(MMRHistory, Match)
            .join(Match, MMRHistory.match_id == Match.id)
            .filter(MMRHistory.player_id == player.id)
            .order_by(MMRHistory.created_at.desc())
            .limit(5)
            .all()
        )

        if not rows:
            await interaction.response.send_message(
                "⚠️ Pas encore de parties enregistrées.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📖 Historique de {player.minecraft_name or user.display_name}",
            color=discord.Color.blue()
        )

        for history_row, match in rows:
            meta = history_row.meta or {}

            role = meta.get("role", "inconnu")
            kills = meta.get("kills", 0)
            dmg = meta.get("dmg", 0)
            infections = meta.get("infections", 0)
            survival_time = meta.get("survival_time", 0)

            if role == "humain":
                role_icon = "🏹"
            elif role == "infected":
                role_icon = "🧟"
            elif role == "firstz":
                role_icon = "🦠"
            else:
                role_icon = "❔"

            mmr_change = history_row.delta or 0
            color_emoji = "🟢" if mmr_change > 0 else "🔴" if mmr_change < 0 else "⚪"

            date_text = match.created_at.strftime("%d/%m/%Y %H:%M") if match.created_at else "Date inconnue"
            winner = (match.winner_team or "inconnu").upper()
            map_name = match.map_name or "Map inconnue"

            val = f"{role_icon} {role.capitalize()} | ⚔️ {kills} kills"

            if role in ("infected", "firstz"):
                val += f" | 💥 {dmg} dmg | 🧟 {infections} infections"

            if role == "humain":
                surv_text = "✅ Survivant" if survival_time >= 90 else "❌ Mort/Infecté"
                val += f" | {surv_text}"

            val += f" | {color_emoji} {mmr_change:+} MMR"

            embed.add_field(
                name=f"📅 {date_text} — 🏆 {winner} — 🗺 {map_name}",
                value=val,
                inline=False
            )

        await interaction.response.send_message(embed=embed)

    finally:
        db.close()


@bot.tree.command(name="leaderboard", description="Top 10 du classement Ranked.")
async def leaderboard(interaction: discord.Interaction):
    db = SessionLocal()

    try:
        players = (
            db.query(Player)
            .order_by(Player.current_mmr.desc())
            .limit(10)
            .all()
        )

        if not players:
            await interaction.response.send_message(
                "⚠️ Aucun joueur enregistré pour l’instant.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🏆 Leaderboard Infecté",
            color=discord.Color.gold()
        )

        medals = ["👑", "🥈", "🥉"]

        for i, player in enumerate(players, start=1):
            mmr = player.current_mmr or 1000
            rank_label = get_rank(mmr)
            name = player.minecraft_name or (
                f"Zenavia#{player.zenavia_player_id}" if player.zenavia_player_id else f"player_{player.id}"
            )
            prefix = medals[i - 1] if i <= 3 else f"#{i}"

            embed.add_field(
                name=f"{prefix} {name}",
                value=f"{rank_label} | {mmr} MMR",
                inline=False
            )

        await interaction.response.send_message(embed=embed)

    finally:
        db.close()


@bot.tree.command(name="card", description="Carte de profil immersive RP.")
@app_commands.describe(member="Joueur ciblé (optionnel)")
async def card(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    db = SessionLocal()

    try:
        user = member or interaction.user
        discord_id = str(user.id)

        player = db.query(Player).filter(
            Player.discord_id == discord_id
        ).first()

        if not player:
            await interaction.response.send_message(
                f"❌ {user.mention} n’est pas encore enregistré.",
                ephemeral=True
            )
            return

        mmr = player.current_mmr or 1000
        rank_label = get_rank(mmr)
        color = rank_color(rank_label)

        total_wins = player.wins or 0
        total_losses = player.losses or 0
        total_games = player.games_played or 0
        winrate = round((total_wins / total_games) * 100, 1) if total_games > 0 else 0

        top_player = (
            db.query(Player)
            .order_by(Player.current_mmr.desc())
            .first()
        )

        crown = ""
        if top_player and top_player.id == player.id:
            crown = " 👑 Patient Zero"

        lore_desc = {
            "Survivant": ("*Encore debout… mais pour combien de temps ?*", "🪦", 0, 999),
            "Zombie": ("*La faim le dévore, ses pas résonnent dans la nuit.*", "🧟", 1000, 1499),
            "Mutant": ("*Son corps se tord, ses cris ne sont plus humains.*", "🧌", 1500, 1999),
            "Apocalypse": ("*Il n’annonce rien… si ce n’est la fin.*", "💀", 2000, 2499),
            "Alpha-Z": ("*Alpha et Omega — le fléau originel se dresse devant vous.*", "🔥", 2500, 3000),
        }

        desc_text, emoji_rank, min_mmr, max_mmr = next(
            (txt for key, txt in lore_desc.items() if key in rank_label),
            ("", "❔", 0, 0)
        )

        if "Alpha-Z" in rank_label:
            progress_bar = "👑 Rang ultime atteint."
        else:
            total_range = max_mmr - min_mmr
            progress = (mmr - min_mmr) / total_range if total_range > 0 else 0
            filled = int(progress * 10)
            bar = "🔴" * filled + "⚫" * (10 - filled)
            progress_bar = f"{bar} {int(progress * 100)}%"

        display_name = player.minecraft_name or (
            f"Zenavia#{player.zenavia_player_id}" if player.zenavia_player_id else "Inconnu"
        )

        embed = discord.Embed(
            title=f"📜 Dossier de {display_name}{crown} {emoji_rank}",
            description=desc_text,
            color=color
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        embed.add_field(name="🎖 Rang", value=rank_label, inline=True)
        embed.add_field(name="📈 MMR", value=str(mmr), inline=True)
        embed.add_field(name="🎮 Parties", value=str(total_games), inline=True)

        embed.add_field(name="🏆 Victoires", value=str(total_wins), inline=True)
        embed.add_field(name="❌ Défaites", value=str(total_losses), inline=True)
        embed.add_field(name="📊 Winrate", value=f"{winrate}%", inline=True)

        embed.add_field(name="⚔️ Kills", value=str(player.kills or 0), inline=True)
        embed.add_field(name="☠️ Morts", value=str(player.deaths or 0), inline=True)
        embed.add_field(name="🧟 Infections", value=str(player.infections or 0), inline=True)

        embed.add_field(name="🛡️ Survivals", value=str(player.survivals or 0), inline=True)
        embed.add_field(name="🦠 First Z", value=str(player.first_z_count or 0), inline=True)
        embed.add_field(name="🟢 Ranked", value="Oui" if player.active_ranked else "Non", inline=True)

        embed.add_field(
            name="🩸 Progression vers le prochain rang",
            value=progress_bar,
            inline=False
        )

        if crown:
            embed.set_footer(text="👑 Le Patient Zero : celui qui inaugure chaque contagion.")
        else:
            embed.set_footer(text="⚠️ Les faibles tombent, seuls les plus endurcis survivent.")

        await interaction.response.send_message(embed=embed)

    finally:
        db.close()


# ---------- Commandes Admin ----------
@bot.tree.command(name="unlink", description="Délier un joueur (admin).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(member="Joueur à délier")
async def unlink(interaction: discord.Interaction, member: discord.Member):
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM players WHERE discord_id = %s", (str(member.id),))
    await interaction.response.send_message(
        f"🔓 {member.mention} a été délié. Il peut refaire `/register`.",
        ephemeral=True
    )


@bot.tree.command(name="resetseason", description="Démarrer une nouvelle saison (admin).")
@app_commands.checks.has_permissions(administrator=True)
async def resetseason(interaction: discord.Interaction):
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("SELECT MAX(season_id) FROM players")
        current = c.fetchone()[0] or 1
        new_season = current + 1
        c.execute("""
            UPDATE players
            SET mmr = 1000,
                last_change = 0,
                wins_humain = 0,
                wins_zombie = 0,
                losses = 0,
                kills_zombie = 0,
                kills_humain = 0,
                assists = 0,
                dmg_dealt = 0,
                survival_time_best = 0,
                survival_time_avg = 0,
                season_id = %s
        """, (new_season,))
    await interaction.response.send_message(
        f"🆕 La saison {new_season} commence ! Tous les joueurs ont été reset à 1000 MMR.",
        ephemeral=True
    )


# ---------- Ranked ON/OFF ----------
@bot.tree.command(name="ranked_on", description="Réactiver le mode Ranked (tes parties comptent à nouveau)")
async def ranked_on(interaction: discord.Interaction):
    db = SessionLocal()

    try:
        discord_id = str(interaction.user.id)

        player = db.query(Player).filter(
            Player.discord_id == discord_id
        ).first()

        if not player:
            await interaction.response.send_message(
                "❌ Tu n’es pas encore enregistré. Utilise d’abord `/register pseudo_minecraft`.",
                ephemeral=True
            )
            return

        player.active_ranked = True
        db.commit()

        await interaction.response.send_message(
            "✅ Ton mode **Ranked** est maintenant **activé** !",
            ephemeral=True
        )

    except Exception as e:
        db.rollback()
        await interaction.response.send_message(
            f"❌ Erreur pendant l’activation du ranked : {e}",
            ephemeral=True
        )

    finally:
        db.close()


@bot.tree.command(name="ranked_off", description="Désactiver le Ranked (jouer chill, parties ignorées)")
async def ranked_off(interaction: discord.Interaction):
    db = SessionLocal()

    try:
        discord_id = str(interaction.user.id)

        player = db.query(Player).filter(
            Player.discord_id == discord_id
        ).first()

        if not player:
            await interaction.response.send_message(
                "❌ Tu n’es pas encore enregistré. Utilise d’abord `/register pseudo_minecraft`.",
                ephemeral=True
            )
            return

        player.active_ranked = False
        db.commit()

        await interaction.response.send_message(
            "⏸️ Tu es maintenant en mode **chill** : tes parties ne compteront plus pour le Ranked.",
            ephemeral=True
        )

    except Exception as e:
        db.rollback()
        await interaction.response.send_message(
            f"❌ Erreur pendant la désactivation du ranked : {e}",
            ephemeral=True
        )

    finally:
        db.close()


@bot.tree.command(name="sync", description="Resynchronise les commandes slash")
async def sync(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        synced = await bot.tree.sync()
        await interaction.followup.send(f"✅ {len(synced)} slash commands resynchronisées.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erreur lors de la synchro : {e}", ephemeral=True)

@bot.tree.command(name="give_elo", description="Ajouter du MMR à un joueur (admin).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    member="Joueur ciblé",
    amount="Montant de MMR à ajouter",
    reason="Raison du bonus (ex: TOTW S10, event, compensation)"
)
async def give_elo(
    interaction: discord.Interaction,
    member: discord.Member,
    amount: int,
    reason: str = "Aucune raison précisée"
):
    db = SessionLocal()

    try:
        if amount <= 0:
            await interaction.response.send_message(
                "❌ Le montant doit être supérieur à 0.",
                ephemeral=True
            )
            return

        player = db.query(Player).filter(
            Player.discord_id == str(member.id)
        ).first()

        if not player:
            await interaction.response.send_message(
                f"❌ {member.mention} n’est pas enregistré.",
                ephemeral=True
            )
            return

        old_mmr = player.current_mmr or 1000
        new_mmr = old_mmr + amount

        player.current_mmr = new_mmr

        if hasattr(player, "last_change"):
            player.last_change = amount

        db.commit()

        rank_label = get_rank(new_mmr)

        embed = discord.Embed(
            title="🟢 Modification manuelle du classement",
            description="Le protocole Z.E.N.A. a accordé un bonus de MMR.",
            color=discord.Color.green()
        )
        embed.add_field(name="👤 Joueur", value=member.mention, inline=True)
        embed.add_field(name="📛 Profil", value=player.minecraft_name or "Inconnu", inline=True)
        embed.add_field(name="🎯 Variation", value=f"`+{amount} MMR`", inline=True)
        embed.add_field(name="📈 Ancien MMR", value=str(old_mmr), inline=True)
        embed.add_field(name="📊 Nouveau MMR", value=str(new_mmr), inline=True)
        embed.add_field(name="🏅 Rang actuel", value=rank_label, inline=True)
        embed.add_field(name="📝 Motif", value=reason, inline=False)
        embed.set_footer(text=f"Action exécutée par {interaction.user.display_name}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

        if interaction.guild:
            await update_hall(interaction.guild)

    except Exception as e:
        db.rollback()
        await interaction.response.send_message(
            f"❌ Erreur pendant l’ajout de MMR : {e}",
            ephemeral=True
        )

    finally:
        db.close()

@bot.tree.command(name="remove_elo", description="Retirer du MMR à un joueur (admin).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    member="Joueur ciblé",
    amount="Montant de MMR à retirer",
    reason="Raison du retrait (ex: sanction, erreur, correction)"
)
async def remove_elo(
    interaction: discord.Interaction,
    member: discord.Member,
    amount: int,
    reason: str = "Aucune raison précisée"
):
    db = SessionLocal()

    try:
        if amount <= 0:
            await interaction.response.send_message(
                "❌ Le montant doit être supérieur à 0.",
                ephemeral=True
            )
            return

        player = db.query(Player).filter(
            Player.discord_id == str(member.id)
        ).first()

        if not player:
            await interaction.response.send_message(
                f"❌ {member.mention} n’est pas enregistré.",
                ephemeral=True
            )
            return

        old_mmr = player.current_mmr or 1000
        new_mmr = old_mmr - amount

        if new_mmr < 0:
            new_mmr = 0

        real_delta = new_mmr - old_mmr  # sera négatif ou 0

        player.current_mmr = new_mmr

        if hasattr(player, "last_change"):
            player.last_change = real_delta

        db.commit()

        rank_label = get_rank(new_mmr)

        embed = discord.Embed(
            title="🔴 Modification manuelle du classement",
            description="Le protocole Z.E.N.A. a appliqué un retrait de MMR.",
            color=discord.Color.red()
        )
        embed.add_field(name="👤 Joueur", value=member.mention, inline=True)
        embed.add_field(name="📛 Profil", value=player.minecraft_name or "Inconnu", inline=True)
        embed.add_field(name="🎯 Variation", value=f"`{real_delta} MMR`", inline=True)
        embed.add_field(name="📈 Ancien MMR", value=str(old_mmr), inline=True)
        embed.add_field(name="📊 Nouveau MMR", value=str(new_mmr), inline=True)
        embed.add_field(name="🏅 Rang actuel", value=rank_label, inline=True)
        embed.add_field(name="📝 Motif", value=reason, inline=False)
        embed.set_footer(text=f"Action exécutée par {interaction.user.display_name}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

        if interaction.guild:
            await update_hall(interaction.guild)

    except Exception as e:
        db.rollback()
        await interaction.response.send_message(
            f"❌ Erreur pendant le retrait de MMR : {e}",
            ephemeral=True
        )

    finally:
        db.close()

@bot.tree.command(name="totw_reward", description="Distribuer les récompenses Team Of The Week (admin).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    first="1er de la TOTW",
    second="2e de la TOTW",
    third="3e de la TOTW",
    fourth="4e de la TOTW",
    fifth="5e de la TOTW",
    season="Saison TOTW (ex: S10)"
)
async def totw_reward(
    interaction: discord.Interaction,
    first: discord.Member,
    second: discord.Member,
    third: discord.Member,
    fourth: discord.Member,
    fifth: discord.Member,
    season: str
):
    db = SessionLocal()

    rewards = [
        (first, 25, "🥇"),
        (second, 20, "🥈"),
        (third, 15, "🥉"),
        (fourth, 10, "4️⃣"),
        (fifth, 5, "5️⃣"),
    ]

    try:
        members_only = [m.id for m, _, _ in rewards]
        if len(set(members_only)) != 5:
            await interaction.response.send_message(
                "❌ Impossible de distribuer la TOTW : un même joueur a été sélectionné plusieurs fois.",
                ephemeral=True
            )
            return

        results = []
        skipped = []

        for member, amount, medal in rewards:
            player = db.query(Player).filter(
                Player.discord_id == str(member.id)
            ).first()

            if not player:
                skipped.append(f"{medal} {member.mention} — non enregistré")
                continue

            old_mmr = player.current_mmr or 1000
            new_mmr = old_mmr + amount

            player.current_mmr = new_mmr

            if hasattr(player, "last_change"):
                player.last_change = amount

            results.append(
                f"{medal} {member.mention} — `+{amount} MMR` • **{old_mmr} → {new_mmr}**"
            )

        db.commit()

        embed = discord.Embed(
            title="🏆 TEAM OF THE WEEK — Récompenses distribuées",
            description=(
                f"Les bonus compétitifs de la **{season}** ont été appliqués.\n"
                "Sélection manuelle validée par le protocole Z.E.N.A."
            ),
            color=discord.Color.gold()
        )

        if results:
            embed.add_field(
                name="📈 Récompenses appliquées",
                value="\n".join(results),
                inline=False
            )
        else:
            embed.add_field(
                name="📈 Récompenses appliquées",
                value="Aucune récompense n’a pu être attribuée.",
                inline=False
            )

        if skipped:
            embed.add_field(
                name="⚠️ Joueurs ignorés",
                value="\n".join(skipped),
                inline=False
            )

        embed.set_footer(text=f"Distribution TOTW • {season}")

        await interaction.response.send_message(embed=embed)

        if interaction.guild:
            await update_hall(interaction.guild)

    except Exception as e:
        db.rollback()
        await interaction.response.send_message(
            f"❌ Erreur distribution TOTW : {e}",
            ephemeral=True
        )

    finally:
        db.close()        
# =========================
#   MESSAGES RP AUTOMATIQUES
# =========================

# ⚠️ Ces constantes / listes doivent exister dans ton vrai projet.
# Si elles sont déjà définies ailleurs dans ton bot.py actuel, garde-les.
# FIRECAMP_CHANNEL_NAME = "🔥・feu-de-camp"
# RADIO_CHANNEL_NAME = "📻・radio"
# firecamp_messages = [...]
# radio_messages = [...]
# def glitch_text(text: str) -> str: ...

FIRECAMP_FIRST_DELAY_H = (1, 6)
FIRECAMP_WINDOW_H = (72, 168)

RADIO_FIRST_DELAY_H = (1, 6)
RADIO_WINDOW_H = (48, 96)

CFG_FIRECAMP_LAST = "firecamp_last_sent"
CFG_RADIO_LAST = "radio_last_sent"
CFG_RP_ENABLED = "rp_auto_enabled"


def _now() -> int:
    return int(time.time())


def _hours(h: float) -> int:
    return int(h * 3600)


def _rand_seconds(hmin: int, hmax: int) -> int:
    return random.randint(_hours(hmin), _hours(hmax))


async def _sleep_rand(hmin: int, hmax: int):
    await asyncio.sleep(_rand_seconds(hmin, hmax))


def _rp_enabled() -> bool:
    val = get_config(CFG_RP_ENABLED)
    return val is None or val == "1"


async def firecamp_daemon():
    await bot.wait_until_ready()
    await _sleep_rand(*FIRECAMP_FIRST_DELAY_H)

    while not bot.is_closed():
        try:
            if not _rp_enabled():
                await asyncio.sleep(_hours(6))
                continue

            for guild in list(bot.guilds):
                channel = discord.utils.get(guild.text_channels, name=FIRECAMP_CHANNEL_NAME)
                if not channel:
                    continue

                last = int(get_config(CFG_FIRECAMP_LAST) or "0")
                now = _now()
                min_gap = _hours(FIRECAMP_WINDOW_H[0])

                if now - last >= min_gap:
                    msg = random.choice(firecamp_messages)
                    await channel.send(msg)
                    set_config(CFG_FIRECAMP_LAST, str(now))
                    log.info(f"🔥 Firecamp → {guild.name}/{channel.name}")

            await _sleep_rand(*FIRECAMP_WINDOW_H)

        except Exception as e:
            log.error(f"[firecamp_daemon] {e}")
            await asyncio.sleep(_hours(1))


async def radio_daemon():
    await bot.wait_until_ready()
    await _sleep_rand(*RADIO_FIRST_DELAY_H)

    while not bot.is_closed():
        try:
            if not _rp_enabled():
                await asyncio.sleep(_hours(6))
                continue

            for guild in list(bot.guilds):
                channel = discord.utils.get(guild.text_channels, name=RADIO_CHANNEL_NAME)
                if not channel:
                    continue

                last = int(get_config(CFG_RADIO_LAST) or "0")
                now = _now()
                min_gap = _hours(RADIO_WINDOW_H[0])

                if now - last >= min_gap:
                    base_msg = random.choice(radio_messages)
                    glitched_msg = glitch_text(base_msg)
                    await channel.send(glitched_msg)
                    set_config(CFG_RADIO_LAST, str(now))
                    log.info(f"📻 Radio → {guild.name}/{channel.name}")

            await _sleep_rand(*RADIO_WINDOW_H)

        except Exception as e:
            log.error(f"[radio_daemon] {e}")
            await asyncio.sleep(_hours(1))


def ensure_rp_daemons_started():
    if not getattr(bot, "_rp_tasks_started", False):
        bot.loop.create_task(firecamp_daemon())
        bot.loop.create_task(radio_daemon())
        bot._rp_tasks_started = True
        log.info("📻 Daemons RP démarrés (feu de camp + radio) avec délais aléatoires.")


@bot.tree.command(name="rp_auto_on", description="Activer les messages RP automatiques (global).")
@app_commands.checks.has_permissions(administrator=True)
async def rp_auto_on(interaction: discord.Interaction):
    set_config(CFG_RP_ENABLED, "1")
    await interaction.response.send_message("✅ Messages RP automatiques **activés**.", ephemeral=True)


@bot.tree.command(name="rp_auto_off", description="Désactiver les messages RP automatiques (global).")
@app_commands.checks.has_permissions(administrator=True)
async def rp_auto_off(interaction: discord.Interaction):
    set_config(CFG_RP_ENABLED, "0")
    await interaction.response.send_message("⏸️ Messages RP automatiques **désactivés**.", ephemeral=True)


@bot.tree.command(name="send_radio", description="Forcer une transmission ZenaFM brouillée (admin).")
@app_commands.checks.has_permissions(administrator=True)
async def send_radio(interaction: discord.Interaction):
    base_msg = random.choice(radio_messages)
    glitched_msg = glitch_text(base_msg)
    await interaction.response.send_message(f"📻 Transmission envoyée dans {RADIO_CHANNEL_NAME}.", ephemeral=True)
    channel = discord.utils.get(interaction.guild.text_channels, name=RADIO_CHANNEL_NAME)
    if channel:
        await channel.send(glitched_msg)


# =========================
#        LANCEMENT
# =========================
if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("Le token Discord est introuvable. Créez un fichier .env avec DISCORD_TOKEN=...")
    bot.run(TOKEN)