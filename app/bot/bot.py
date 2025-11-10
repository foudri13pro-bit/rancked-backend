# infected_ranked_refactor.py
# Discord.py 2.x
# Requiert: python-dotenv
#
# .env:
# DISCORD_TOKEN=xxxxx

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

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

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

DB_PATH = "infected_ranked.db"

# —— Rangs par MMR (⚠️ logique conservée telle quelle)
RANKS: List[Tuple[int, str]] = [
    (2500, "🔥 Alpha-Z"),
    (2000, "🧌 Mutant"),
    (1500, "🧟 Zombie"),
    (1000, "🪦 Survivant"),
    (-10**9, "🌿 Novice"),  # fallback
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

# —— CONFIG MMR centralisée (logique inchangée)
MMR_CFG = {
    "humain": {
        "win_survivor": 30,     # victoire humains et joueur survivant
        "survive_on_loss": 10,  # survivant malgré défaite
        "kill": 2,
        "assist": 1,
        "survival_time_step": 20,  # +1 tous les 20s (cap 10)
        "survival_time_cap": 10,
        "team_loss_penalty": -15,
    },
    "firstz": {
        "team_win_bonus": 25,
        "team_loss_penalty": -15,
        "kill": 3,  # pas d'objectif dégâts
    },
    "infected": {
        "base_loss": -5,        # a perdu en tant qu'humain
        "kill": 3,
        "dmg_step": 15,         # +1 tous les 15 dmg
        "dmg_cap": 100,
    },
}

# =========================
#     SCENARIOS & MAPS
# =========================

# Chaque scénario a un "balance_score" :
#  -3 = très avantage Zombies | 0 = neutre | +3 = très avantage Humains
SCENARIOS: Dict[str, int] = {
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
    "BlackOut": +1,
    "ScénarioChoose": +1,
    "DoubleCoeur": +1,
    "DernierSurvivant": +1,
    "LuckyShoot": +2,
    "Sacrifice": +2,
    "Invisible": +3,
    "IEM": +3,
    "MapRDM": +3,
}

# Taille des maps selon la capacité
MAPS: Dict[str, str] = {
    # Small — maps compactes (idéales < 10 joueurs)
    "ByteVault": "small",
    "Vertigo": "small",
    "Volcano": "small",
    "Kermor": "small",
    "Museum": "small",
    "Dome": "small",
    "EgoutZ": "small",
    "Mirage": "small",
    "Bayfront": "small",

    # Mid — maps équilibrées (10–50 joueurs)
    "Frozen": "mid",
    "Ravin": "mid",
    "Nature": "mid",
    "SquareT": "mid",
    "Inferno": "mid",
    "Aztec": "mid",
    "Osthera": "mid",
    "Parc": "mid",
    "Manoir": "mid",
    "Melted": "mid",
    "Harran": "mid",
    "Split": "mid",
    "Strell": "mid",

    # Large — maps étendues (50+ joueurs)
    "Port": "large",
    "Colisé": "large",
    "Whitewood": "large",
    "Villa": "large",
    "Costa": "large",
    "Nuke": "large",
    "Menos": "large",
    "Canyon": "large",
    "PlageCheepCheep": "large",
    "BlockFort": "large",
}

# =========================
#       DB UTILITAIRES
# =========================

def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """Crée les tables si absentes et ajoute les colonnes utilisées par le code.
    ⚠️ Non destructif et idempotent.
    """
    with connect_db() as conn:
        c = conn.cursor()

        # players
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

        # matches
        c.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            match_id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            winner TEXT NOT NULL
        )
        """)

        # match_players
        c.execute("""
        CREATE TABLE IF NOT EXISTS match_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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

        # bot_config (clé/valeur)
        c.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)

        # Colonnes legacy ajoutées a posteriori si manquantes
        c.execute("PRAGMA table_info(players)")
        cols = {row[1] for row in c.fetchall()}
        if "last_change" not in cols:
            c.execute("ALTER TABLE players ADD COLUMN last_change INTEGER DEFAULT 0")
        if "season_id" not in cols:
            c.execute("ALTER TABLE players ADD COLUMN season_id INTEGER DEFAULT 1")
        if "active_ranked" not in cols:
            c.execute("ALTER TABLE players ADD COLUMN active_ranked INTEGER DEFAULT 1")

def fetch_player(discord_id: int) -> Optional[sqlite3.Row]:
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT minecraft_name, mmr, last_change, wins_humain, wins_zombie, losses,
                   kills_zombie, kills_humain, assists, dmg_dealt, season_id
            FROM players WHERE discord_id = ?
        """, (str(discord_id),))
        return c.fetchone()

def upsert_player(discord_id: int, minecraft_name: str) -> Tuple[bool, str]:
    """Retourne (created, message)."""
    try:
        with connect_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO players (discord_id, minecraft_name) VALUES (?, ?)",
                (str(discord_id), minecraft_name)
            )
            return True, "créé"
    except sqlite3.IntegrityError:
        return False, "existe"

def update_player(
    c: sqlite3.Cursor,
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
        SET mmr = mmr + ?,
            last_change = ?,
            wins_humain = wins_humain + ?,
            wins_zombie = wins_zombie + ?,
            losses = losses + ?,
            kills_zombie = kills_zombie + ?,
            kills_humain = kills_humain + ?,
            assists = assists + ?,
            dmg_dealt = dmg_dealt + ?
        WHERE discord_id = ?
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
            "INSERT INTO bot_config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value)
        )

def get_config(key: str) -> Optional[str]:
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM bot_config WHERE key = ?", (key,))
        row = c.fetchone()
        return row[0] if row else None

# =========================
#        LOGIQUE MMR
# =========================

def calculate_mmr(
    role: str,
    winner: str,
    is_survivor: bool,
    kills: int,
    assists: int,
    dmg: int,
    survival_time: int,
    scenarios: Optional[List[str]] = None,
    map_name: Optional[str] = None,
) -> int:
    """Calcule le gain/perte de MMR, avec pondération selon scénario et map. (inchangé)"""
    base_mmr = 0

    # --- Calcul de base
    if role == "humain":
        cfg = MMR_CFG["humain"]
        if winner == "humains" and is_survivor:
            base_mmr += cfg["win_survivor"]
        elif is_survivor:
            base_mmr += cfg["survive_on_loss"]
        base_mmr += kills * cfg["kill"]
        base_mmr += assists * cfg["assist"]
        base_mmr += min(survival_time // cfg["survival_time_step"], cfg["survival_time_cap"])
        if winner == "zombies":
            base_mmr += cfg["team_loss_penalty"]

    elif role == "firstz":
        cfg = MMR_CFG["firstz"]
        base_mmr += (cfg["team_win_bonus"] if winner == "zombies" else cfg["team_loss_penalty"])
        base_mmr += kills * cfg["kill"]

    elif role == "infected":
        cfg = MMR_CFG["infected"]
        base_mmr += cfg["base_loss"]
        base_mmr += kills * cfg["kill"]
        base_mmr += min(dmg, cfg["dmg_cap"]) // cfg["dmg_step"]

    # --- Pondération scénario
    if scenarios:
        valid = [SCENARIOS.get(s, 0) for s in scenarios]
        if valid:
            scenario_factor = sum(valid) / len(valid)
            # Bonus côté désavantagé
            if scenario_factor > 0 and winner == "zombies":
                base_mmr *= (1 + (scenario_factor / 20))  # zombies récompensés si partie difficile
            elif scenario_factor < 0 and winner == "humains":
                base_mmr *= (1 + (abs(scenario_factor) / 20))  # humains récompensés si partie difficile

    # --- Pondération taille de map
    if map_name and map_name in MAPS:
        size = MAPS[map_name]
        if size == "small":
            # map favorable aux zombies → buff humains gagnants
            base_mmr *= 1.05 if winner == "humains" else 0.95
        elif size == "large":
            # map favorable aux humains → buff zombies gagnants
            base_mmr *= 1.05 if winner == "zombies" else 0.95

    return int(base_mmr)

# =========================
#        BOT SETUP
# =========================

class RankedBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.synced = False  # éviter sync multiple

    async def setup_hook(self) -> None:
        init_db()

bot = RankedBot()

# =========================
#            UI
# =========================

async def send_rank_alert(guild: discord.Guild, minecraft_name: str, new_rank_label: str):
    """Alerte RP lors d’un changement de rang (conservée)."""
    # Tu peux brancher ici un salon dédié si besoin.
    log.info(f"[RANK-UP] {minecraft_name} -> {new_rank_label}")

async def update_hall(guild: discord.Guild):
    """Met à jour le message du Hall des Légendes en utilisant l'ID stocké en config."""
    msg_id = get_config("hall_message_id")
    if not msg_id:
        log.warning("[Hall] Aucun hall_message_id en config – rien à mettre à jour.")
        return

    try:
        msg_id_int = int(msg_id)
    except ValueError:
        log.warning(f"[Hall] hall_message_id invalide: {msg_id!r}")
        return

    # Récupère la saison courante + top 10
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("SELECT MAX(season_id) FROM players")
        cur_season = c.fetchone()[0] or 1
        c.execute("""
            SELECT minecraft_name, mmr FROM players
            WHERE season_id = ?
            ORDER BY mmr DESC
            LIMIT 10
        """, (cur_season,))
        rows = c.fetchall()

    # Construit l'embed
    if not rows:
        embed = discord.Embed(
            title=f"🏛️ Hall des Légendes — Saison {cur_season}",
            description="*Aucun nom n’a encore été gravé dans la pierre...*",
            color=discord.Color.dark_grey()
        )
    else:
        embed = discord.Embed(
            title=f"━━━━━━━━━ 🏛️ HALL DES LÉGENDES ━━━━━━━━━",
            description=f"⚔️ Saison {cur_season} — *Les noms gravés dans la pierre*",
            color=discord.Color.gold()
        )
        medals = ["👑", "🥈", "🥉"]
        for i, r in enumerate(rows, start=1):
            rank_label = get_rank(r["mmr"])
            prefix = medals[i-1] if i <= 3 else f"#{i}"

            if "Alpha-Z" in rank_label:
                flair = "🔥 Porteur du fléau originel"
            elif "Apocalypse" in rank_label:
                flair = "💀 Incarnation du chaos"
            elif "Mutant" in rank_label:
                flair = "🧌 Déformation de la chair"
            elif "Zombie" in rank_label:
                flair = "🧟 Chair affamée"
            else:
                flair = "🌿 Survivant fragile"

            if i <= 3:
                embed.add_field(
                    name=f"{prefix} {r['minecraft_name']} — {rank_label}",
                    value=f"{flair}\n🏆 {r['mmr']} MMR",
                    inline=False
                )
            else:
                embed.add_field(
                    name=f"{prefix} {r['minecraft_name']}",
                    value=f"{rank_label} | {r['mmr']} MMR",
                    inline=False
                )
        embed.set_footer(text="Les noms effacés disparaissent dans l’oubli...")

    # Trouve le salon
    HALL_CHANNEL_ID = 1423665644519297034  # adapte si besoin
    channel = guild.get_channel(HALL_CHANNEL_ID) or discord.utils.get(guild.text_channels, name="👑・hall-des-légendes")
    if not channel:
        log.warning("[Hall] Salon '👑・hall-des-légendes' introuvable.")
        return

    # Édite le message, ou recrée si supprimé
    try:
        msg = await channel.fetch_message(msg_id_int)
        await msg.edit(embed=embed)
    except discord.NotFound:
        log.warning("[Hall] Message du Hall introuvable – recréation + mise à jour de l’ID.")
        new_msg = await channel.send(embed=embed)
        set_config("hall_message_id", str(new_msg.id))
    except discord.Forbidden:
        log.warning("[Hall] Permission insuffisante (il faut 'Lire l’historique des messages').")
    except Exception as e:
        log.warning(f"[Hall] Erreur update: {e}")


async def setup_or_update_hall(guild: discord.Guild):
    """
    1) Garantit qu'il existe UN message du Hall (créé si supprimé).
    2) Confie ensuite à update_hall() la mise à jour du leaderboard dans CE message.
    """
    # Cherche le salon par ID connu ou par nom
    HALL_CHANNEL_ID = 1423665644519297034  # 👑・hall-des-légendes
    channel = guild.get_channel(HALL_CHANNEL_ID) or discord.utils.get(guild.text_channels, name="👑・hall-des-légendes")

    if not channel:
        log.warning("⚠️ Aucun salon '👑・hall-des-légendes' trouvé (ni ID ni nom).")
        return

    # Placeholder propre (sera remplacé par update_hall)
    placeholder = discord.Embed(
        title="🏛️ Hall des Légendes — Saison 1",
        description="*Chaque saison, les plus grands inscrivent leur nom dans ces murs.*",
        color=discord.Color.gold()
    )
    placeholder.add_field(name="En attente...", value="Le premier match n’a pas encore eu lieu.", inline=False)

    # Garantit le message unique (créé si besoin, sinon édité)
    await ensure_or_update_message(channel, config_key="hall_message_id", embed=placeholder)

    # Met à jour le contenu avec le vrai top (si joueurs)
    await update_hall(guild)
    

    # Récupérer le leaderboard
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("SELECT MAX(season_id) FROM players")
        cur_season = c.fetchone()[0] or 1
        c.execute("""
            SELECT minecraft_name, mmr FROM players
            WHERE season_id = ?
            ORDER BY mmr DESC
            LIMIT 10
        """, (cur_season,))
        rows = c.fetchall()

    # Si pas de joueurs -> message par défaut
    if not rows:
        embed = discord.Embed(
            title=f"🏛️ Hall des Légendes — Saison {cur_season}",
            description="*Aucun nom n’a encore été gravé dans la pierre...*",
            color=discord.Color.dark_grey()
        )
    else:
        embed = discord.Embed(
            title=f"━━━━━━━━━ 🏛️ HALL DES LÉGENDES ━━━━━━━━━",
            description=f"⚔️ Saison {cur_season} — *Les noms gravés dans la pierre*",
            color=discord.Color.gold()
        )

        medals = ["👑", "🥈", "🥉"]
        for i, r in enumerate(rows, start=1):
            rank_label = get_rank(r["mmr"])
            prefix = medals[i-1] if i <= 3 else f"#{i}"

            # Texte RP selon rang (conservé)
            if "Alpha-Z" in rank_label:
                flair = "🔥 Porteur du fléau originel"
            elif "Apocalypse" in rank_label:  # incohérence d’origine conservée volontairement
                flair = "💀 Incarnation du chaos"
            elif "Mutant" in rank_label:
                flair = "🧌 Déformation de la chair"
            elif "Zombie" in rank_label:
                flair = "🧟 Chair affamée"
            else:
                flair = "🌿 Survivant fragile"

            if i <= 3:
                embed.add_field(
                    name=f"{prefix} {r['minecraft_name']} — {rank_label}",
                    value=f"{flair}\n🏆 {r['mmr']} MMR",
                    inline=False
                )
            else:
                embed.add_field(
                    name=f"{prefix} {r['minecraft_name']}",
                    value=f"{rank_label} | {r['mmr']} MMR",
                    inline=False
                )

        embed.set_footer(text="Les noms effacés disparaissent dans l’oubli...")

    channel = discord.utils.get(guild.text_channels, name="👑・hall-des-légendes")
    if channel:
        try:
            msg = await channel.fetch_message(int(hall_id))
            await msg.edit(embed=embed)
        except Exception as e:
            log.warning(f"[Erreur Hall] Impossible de mettre à jour : {e}")

async def finalize_match(
    interaction: discord.Interaction,
    players: List[str],
    roles: Dict[str, str],
    kills: Dict[str, int],
    dmg: Dict[str, int],
    scenarios: Optional[List[str]] = None,
    map_name: Optional[str] = None,
) -> None:
    """Calcule vainqueur, applique MMR, écrit en DB, et envoie un embed résumé + registre RP."""
    winner = "humains" if any(role == "humain" for role in roles.values()) else "zombies"

    with connect_db() as conn:
        c = conn.cursor()

        # mapping minecraft_name -> (discord_id, active_ranked, mmr)
        c.execute("SELECT discord_id, minecraft_name, active_ranked, mmr FROM players")
        name_to_data = {name: (did, active, mmr) for (did, name, active, mmr) in c.fetchall()}

        # Créer le match
        c.execute("INSERT INTO matches (date, winner) VALUES (?, ?)", (datetime.now(timezone.utc).isoformat(), winner))
        match_id = c.lastrowid

        lines = []
        for name in players:
            player_data = name_to_data.get(name)
            if not player_data:
                continue

            discord_id, active_ranked, current_mmr = player_data

            # 💤 Si mode chill → on ignore les updates Ranked
            if not active_ranked:
                lines.append(f"😴 **{name}** (mode chill) — aucune variation de MMR")
                continue

            role = roles.get(name, "humain")
            is_survivor = (role == "humain")  # logique simplifiée actuelle (conservée)
            k = kills.get(name, 0)
            d = dmg.get(name, 0)

            # --- Vérification du changement de rang ---
            old_rank = get_rank(current_mmr)

            change = calculate_mmr(
                role, winner, is_survivor, k, assists=0, dmg=d, survival_time=0,
                scenarios=scenarios, map_name=map_name
            )
            new_rank = get_rank(current_mmr + change)

            # --- Si le rang change, envoie une alerte RP ---
            if old_rank != new_rank:
                await send_rank_alert(interaction.guild, name, new_rank)

            wins_h = 1 if (role == "humain" and winner == "humains" and is_survivor) else 0
            wins_z = 1 if (role == "firstz" and winner == "zombies") else 0
            losses = 1 if ((role == "humain" and winner == "zombies") or (role in ("infected", "firstz") and winner == "humains")) else 0
            kills_z = k if role in ("infected", "firstz") else 0
            kills_h = k if role == "humain" else 0

            update_player(
                c,
                discord_id=int(discord_id),
                mmr_change=change,
                wins_h=wins_h,
                wins_z=wins_z,
                losses=losses,
                kills_z=kills_z,
                kills_h=kills_h,
                assists=0,
                dmg=d,
            )
            c.execute("""
                INSERT INTO match_players (match_id, discord_id, role, kills, dmg, mmr_change, survivor)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (match_id, str(discord_id), role, k, d, change, 1 if is_survivor else 0))

            role_icon = "🏹" if role == "humain" else "🧟" if role == "infected" else "🦠"
            color_emoji = "🟢" if change > 0 else "🔴" if change < 0 else "⚪"
            if role == "infected":
                lines.append(f"{'✅' if is_survivor else '❌'} {role_icon} **{name}** — ⚔️ {k} kills / 💥 {d} dmg — {color_emoji} **{change:+} MMR**")
            elif role == "firstz":
                lines.append(f"{'✅' if is_survivor else '❌'} {role_icon} **{name}** — ⚔️ {k} kills — {color_emoji} **{change:+} MMR**")
            else:
                lines.append(f"{'✅' if is_survivor else '❌'} {role_icon} **{name}** — ⚔️ {k} kills — {color_emoji} **{change:+} MMR**")

    # --- Embed résumé du match
    embed = discord.Embed(
        title="📢 Fin de match",
        description=f"Vainqueurs: **{winner.upper()}**",
        color=discord.Color.green() if winner == "humains" else discord.Color.red()
    )
    for line in lines:
        embed.add_field(name="—", value=line, inline=False)

    await interaction.followup.send(embed=embed)
    await update_hall(interaction.guild)

    # --- Rapport RP (style dossier classifié) ---
    channel_registre = discord.utils.get(interaction.guild.text_channels, name="🪦・registre-des-morts")
    if channel_registre:
        header = (
            "━━━━━━━━━━ 🪦 REGISTRE DES MORTS ━━━━━━━━━━\n"
            f"📜 Rapport #{match_id} — {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')}\n"
            f"🏆 Résultat : **{winner.upper()}**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        )

        body = ""
        for name in players:
            role = roles.get(name, "humain")
            k = kills.get(name, 0)
            d = dmg.get(name, 0)
            change = calculate_mmr(role, winner, role == "humain", k, assists=0, dmg=d, survival_time=0,
                                   scenarios=scenarios, map_name=map_name)

            # Icône de rôle
            if role == "humain":
                icon = "🏹 Humain"
            elif role == "infected":
                icon = "🧟 Infecté"
            elif role == "firstz":
                icon = "🦠 First Z"
            else:
                icon = "❔ Inconnu"

            surv = "✅ Survivant" if (role == "humain" and winner == "humains") else "☠️ Décédé"

            # Ligne style autopsie
            body += (
                f"\n📌 Nom : **{name}**\n"
                f"   ▸ Rôle : {icon}\n"
                f"   ▸ Statut : {surv}\n"
                f"   ▸ Dossier : ⚔️ {k} kills"
            )
            if role == "infected":
                body += f" | 💥 {d} dmg"
            body += f"\n   ▸ Variation : {'🟢' if change > 0 else '🔴' if change < 0 else '⚪'} {change:+} MMR\n"
            body += "   ────────────────────────────────"

        footer = "\nFin du rapport — Archivé dans le registre.\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        await channel_registre.send(header + body + footer)

# =========================
#     HELPERS & EVENTS
# =========================

def find_channel(guild: discord.Guild, *fragments: str) -> Optional[discord.TextChannel]:
    """
    Cherche un salon dont le nom contient un ou plusieurs fragments (insensible aux emojis et décorations).
    Exemple: find_channel(guild, "sirene", "alertes")
    """
    fragments = [f.lower() for f in fragments]
    for ch in guild.text_channels:
        for frag in fragments:
            if frag in ch.name.lower():
                log.info(f"✅ Match salon: '{frag}' -> {ch.name}")
                return ch
    return None

# --- Helpers pour créer/mettre à jour un message "statique" dans un salon
async def ensure_or_update_message(channel: discord.TextChannel, *, config_key: str, embed: discord.Embed):
    """
    Édite le message mémorisé dans bot_config[config_key] s'il existe.
    Si le fetch échoue par NotFound -> on recrée.
    Si le fetch échoue par Forbidden/permissions -> on NE recrée PAS (pour éviter le spam).
    """
    if channel is None:
        return

    msg_id = get_config(config_key)
    if msg_id:
        try:
            msg = await channel.fetch_message(int(msg_id))
            await msg.edit(embed=embed)
            return
        except discord.NotFound:
            # le message n'existe plus -> on peut recréer proprement
            pass
        except discord.Forbidden:
            log.warning(f"[ensure_or_update_message] Accès refusé pour fetch le message {msg_id} dans #{channel.name}. "
                        f"Vérifie 'Lire l’historique des messages'. Pas de recréation pour éviter le spam.")
            return
        except Exception as e:
            log.warning(f"[ensure_or_update_message] Erreur fetch/éditer msg {msg_id}: {e}. "
                        f"Pas de recréation automatique pour éviter le spam.")
            return

    # Créer un nouveau message uniquement si on est sûr que l’ancien n’existe plus
    try:
        sent = await channel.send(embed=embed)
        set_config(config_key, str(sent.id))
        log.info(f"[ensure_or_update_message] Nouveau message créé (id={sent.id}) dans #{channel.name}")
    except discord.Forbidden:
        log.error(f"[ensure_or_update_message] Forbidden pour envoyer dans #{channel.name}. Vérifie les permissions.")
    except Exception as e:
        log.error(f"[ensure_or_update_message] Impossible d'envoyer le message dans #{channel.name}: {e}")

def build_manual_embed() -> discord.Embed:
    # >>> METS ICI LA VERSION QUE TU VEUX AFFICHER <<<
    return discord.Embed(
        title="📖 Manuel de Survie",
        description=(
            "Ta survie dépend de ces ordres :\n\n"
            "🧩 `/register [Pseudo Minecraft]` → enregistre ton identifiant pour participer.\n"
            "🟢 `/ranked_on` → rejoins le système Ranked.\n"
            "🔴 `/ranked_off` → quitte le Ranked.\n"
            "📊 `/rank` → consulte ton rang et ton MMR.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "### ⚖️ RÈGLES OFFICIELLES — Ranked (Projet Z.E.N.A.)\n"
            "**1) Accès & Identité**\n"
            "• `/register [Pseudo]` obligatoire.\n"
            "• Active/désactive via `/ranked_on` / `/ranked_off`.\n"
            "• Multi-comptes / identités falsifiées → exclusion.\n\n"
            "**2) Conduite en match**\n"
            "• Fair-play : pas d’abandon volontaire / throw / AFK.\n"
            "• Interdits : cheats, macros abusives, exploits de bug, ghosting, stream-sniping.\n"
            "• Le staff Z.E.N.A. tranche en cas de litige.\n\n"
            "**3) Déroulement & Rehost**\n"
            "• Les stats se saisissent via `/matchend`.\n"
            "• Déco < 2 min au début : **rehost possible** si la majorité l’accepte.\n"
            "• Au-delà, le match continue sauf décision du staff.\n\n"
            "**4) MMR & Classement**\n"
            "• Le MMR varie selon rôle, kills, dégâts, scénario et taille de map.\n"
            "• Abandon injustifié = **pénalité MMR**.\n"
            "• Classement : `/leaderboard`.\n\n"
            "**5) Sanctions & Preuves**\n"
            "• Triche = résultats annulés + suspension Ranked.\n"
            "• Toxicité grave = sanctions Discord + Ranked.\n"
            "• Fournis **clips / logs / screens** au salon prévu.\n\n"
            "_En participant, tu acceptes le protocole Ranked du Projet Z.E.N.A._ 🧬"
        ),
        color=discord.Color.green()
    )

@bot.event
async def on_ready():
    # Sync global (une seule fois)
    if not bot.synced:
        await bot.tree.sync()
        bot.synced = True
    log.info(f"✅ Connecté en tant que {bot.user} ({bot.user.id})")

    if not bot.guilds:
        return
    guild = bot.guilds[0]

    # 📂 Debug : liste des salons
    log.info("📂 Salons textuels détectés :")
    for ch in guild.text_channels:
        log.info(f"- {ch.name}")

    # 1) Sirène d’alertes
    channel_alertes = find_channel(guild, "sirene", "alertes")
    if channel_alertes and not channel_alertes.last_message_id:
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
        await channel_alertes.send(embed=embed)

    # 2) Lois du camp
    channel_lois = find_channel(guild, "lois-du-camp", "lois")
    if channel_lois and not channel_lois.last_message_id:
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
        await channel_lois.send(embed=embed)

    # 3) Manuel de survie — message unique (auto update)
    channel_manuel = find_channel(guild, "manuel", "survie")
    if channel_manuel:
        await ensure_or_update_message(
            channel_manuel,
            config_key="manual_message_id",
            embed=build_manual_embed()
        )

    # 4) Dossier d’évaluation — présentation des rangs
    channel_rangs = find_channel(guild, "dossier", "evaluation")
    if channel_rangs and not channel_rangs.last_message_id:
        embed = discord.Embed(
            title="🎖️ Les Rangs du Ranked Infecté",
            description="Voici ton chemin de survie... ou de damnation.",
            color=discord.Color.gold()
        )
        embed.add_field(name="🪦 Survivant (0–999 MMR)", value="*Derniers humains, fragiles mais encore debout.*", inline=False)
        embed.add_field(name="🧟 Zombie (1000–1499 MMR)", value="*La chair à canon de l’essaim, affamés et sans répit.*", inline=False)
        embed.add_field(name="🧌 Mutant (1500–1999 MMR)", value="*La chair se déforme, l’esprit s’éteint : un nouvel être naît.*", inline=False)
        embed.add_field(name="💀 Apocalypse (2000–2499 MMR)", value="*Incarnation de la fin, porteur du désespoir et du chaos.*", inline=False)
        embed.add_field(name="🔥 Alpha-Z (2500+ MMR)", value="*Alpha et Omega, porteur du fléau originel.*", inline=False)
        await channel_rangs.send(embed=embed)

    # 5) Hall des Légendes — auto setup + auto update
    await setup_or_update_hall(guild)

    # 🔥 Messages RP automatiques (feu de camp + radio)
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
    created, status = upsert_player(interaction.user.id, minecraft_name)
    if created:
        await interaction.response.send_message(
            f"✅ {interaction.user.mention} enregistré comme **{minecraft_name}** avec 1000 MMR.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"⚠️ {interaction.user.mention}, tu es déjà enregistré.",
            ephemeral=True
        )

@bot.tree.command(name="rank", description="Afficher ton rang et ton MMR (ou celui d'un autre).")
@app_commands.describe(member="Joueur ciblé (optionnel)")
async def rank(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    user = member or interaction.user
    row = fetch_player(user.id)
    if not row:
        await interaction.response.send_message(f"❌ {user.mention} n’est pas encore enregistré.", ephemeral=True)
        return
    mmr = row["mmr"]
    last = row["last_change"]
    r = get_rank(mmr)
    await interaction.response.send_message(f"🏅 **{row['minecraft_name']}** — {r} | {mmr} ({last:+})")

@bot.tree.command(name="stats", description="Afficher les stats complètes d'un joueur.")
@app_commands.describe(member="Joueur ciblé (optionnel)")
async def stats(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    user = member or interaction.user
    row = fetch_player(user.id)
    if not row:
        await interaction.response.send_message(f"❌ {user.mention} n’est pas encore enregistré.", ephemeral=True)
        return

    rank_label = get_rank(row["mmr"])
    color = rank_color(rank_label)
    total_wins = int(row["wins_humain"] or 0) + int(row["wins_zombie"] or 0)
    total_games = total_wins + int(row["losses"] or 0)
    winrate = round((total_wins / total_games) * 100, 1) if total_games > 0 else 0

    embed = discord.Embed(title=f"📊 Stats de {row['minecraft_name']}", color=color)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="📈 MMR", value=f"{row['mmr']} ({row['last_change']:+})", inline=True)
    embed.add_field(name="🎖 Rang", value=rank_label, inline=True)
    embed.add_field(name="📅 Saison", value=row["season_id"], inline=True)
    embed.add_field(name="🏆 Victoires Humains", value=row["wins_humain"], inline=True)
    embed.add_field(name="🧟 Victoires Zombies", value=row["wins_zombie"], inline=True)
    embed.add_field(name="❌ Défaites", value=row["losses"], inline=True)
    embed.add_field(name="⚔️ Kills Zombies", value=row["kills_zombie"], inline=True)
    embed.add_field(name="🏹 Kills Humains", value=row["kills_humain"], inline=True)
    embed.add_field(name="🤝 Assists", value=row["assists"], inline=True)
    embed.add_field(name="💥 Dégâts", value=row["dmg_dealt"], inline=True)
    embed.add_field(name="📊 Winrate", value=f"{winrate}% ({total_games} games)", inline=False)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="history", description="Afficher les 5 dernières parties d'un joueur.")
@app_commands.describe(member="Joueur ciblé (optionnel)")
async def history(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    user = member or interaction.user
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT m.date, m.winner, mp.role, mp.kills, mp.dmg, mp.mmr_change, mp.survivor
            FROM match_players mp
            JOIN matches m ON mp.match_id = m.match_id
            WHERE mp.discord_id = ?
            ORDER BY m.date DESC
            LIMIT 5
        """, (str(user.id),))
        rows = c.fetchall()

    if not rows:
        await interaction.response.send_message("⚠️ Pas encore de parties enregistrées.", ephemeral=True)
        return

    embed = discord.Embed(title=f"📖 Historique de {user.display_name}", color=discord.Color.blue())
    for r in rows:
        date = r["date"][:16]
        winner = r["winner"].upper()
        role = r["role"]
        kills = r["kills"] or 0
        dmg = r["dmg"] or 0
        mmr_change = r["mmr_change"] or 0
        survivor = bool(r["survivor"])

        if role == "humain":
            role_icon = "🏹"
        elif role == "infected":
            role_icon = "🧟"
        elif role == "firstz":
            role_icon = "🦠"
        else:
            role_icon = "❔"

        color_emoji = "🟢" if mmr_change > 0 else "🔴" if mmr_change < 0 else "⚪"
        surv_text = "✅ Survivant" if survivor else "❌ Mort"
        val = f"{role_icon} {role.capitalize()} | ⚔️ {kills} kills"
        if role == "infected":
            val += f" | 💥 {dmg} dmg"
        val += f" | {color_emoji} {mmr_change:+} MMR | {surv_text}"

        embed.add_field(
            name=f"📅 {date} — 🏆 {winner}",
            value=val,
            inline=False
        )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Top 10 de la saison en cours.")
async def leaderboard(interaction: discord.Interaction):
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("SELECT MAX(season_id) FROM players")
        cur_season = c.fetchone()[0] or 1
        c.execute("""
            SELECT minecraft_name, mmr FROM players
            WHERE season_id = ?
            ORDER BY mmr DESC
            LIMIT 10
        """, (cur_season,))
        rows = c.fetchall()

    if not rows:
        await interaction.response.send_message("⚠️ Aucun joueur enregistré pour l’instant.", ephemeral=True)
        return

    embed = discord.Embed(title=f"🏆 Leaderboard Infecté — Saison {cur_season}", color=discord.Color.gold())
    medals = ["👑", "🥈", "🥉"]
    for i, r in enumerate(rows, start=1):
        rank_label = get_rank(r["mmr"])
        prefix = medals[i-1] if i <= 3 else f"#{i}"
        name_line = f"{prefix} {r['minecraft_name']}" if i <= 3 else f"#{i} {r['minecraft_name']}"
        embed.add_field(name=name_line, value=f"{rank_label} | {r['mmr']} MMR", inline=False)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="card", description="Carte de profil immersive RP.")
@app_commands.describe(member="Joueur ciblé (optionnel)")
async def card(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    user = member or interaction.user
    row = fetch_player(user.id)
    if not row:
        await interaction.response.send_message(
            f"❌ {user.mention} n’est pas encore enregistré.",
            ephemeral=True
        )
        return

    total_wins = int(row["wins_humain"] or 0) + int(row["wins_zombie"] or 0)
    total_games = total_wins + int(row["losses"] or 0)
    winrate = round((total_wins / total_games) * 100, 1) if total_games > 0 else 0
    mmr = row["mmr"]
    rank_label = get_rank(mmr)

    # Vérifie si joueur est Top 1 de sa saison
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("SELECT minecraft_name FROM players WHERE season_id = ? ORDER BY mmr DESC LIMIT 1", (row["season_id"],))
        row_top = c.fetchone()
    crown = " 👑 Patient Zero" if row_top and row_top[0] == row["minecraft_name"] else ""

    # Couleur selon rang
    color = rank_color(rank_label)

    # Description RP + emoji (conservée, y.c. incohérences éventuelles)
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

    # --- Barre sanglante RP ---
    if "Alpha-Z" in rank_label:
        progress_bar = "👑 Rang ultime atteint."
    else:
        total_range = max_mmr - min_mmr
        progress = (mmr - min_mmr) / total_range if total_range > 0 else 0
        filled = int(progress * 10)
        bar = "🔴" * filled + "⚫" * (10 - filled)
        progress_bar = f"{bar} {int(progress*100)}%"

    # --- Embed RP ---
    embed = discord.Embed(
        title=f"📜 Dossier de {row['minecraft_name']}{crown} {emoji_rank}",
        description=desc_text,
        color=color
    )
    embed.set_thumbnail(url=user.display_avatar.url)

    # Identité
    embed.add_field(name="🎖 Rang", value=rank_label, inline=True)
    embed.add_field(name="📈 MMR", value=f"{mmr} ({row['last_change']:+})", inline=True)
    embed.add_field(name="📅 Saison", value=row["season_id"], inline=True)

    # Stats Combat
    embed.add_field(name="⚔️ Kills Humains", value=row["kills_humain"], inline=True)
    embed.add_field(name="🧟 Kills Zombies", value=row["kills_zombie"], inline=True)
    embed.add_field(name="💥 Dégâts", value=row["dmg_dealt"], inline=True)

    # Victoires / Défaites
    embed.add_field(name="🏆 Victoires", value=total_wins, inline=True)
    embed.add_field(name="❌ Défaites", value=row["losses"], inline=True)
    embed.add_field(name="📊 Winrate", value=f"{winrate}% ({total_games} games)", inline=True)

    # Barre de progression sanglante
    embed.add_field(name="🩸 Progression vers le prochain rang", value=progress_bar, inline=False)

    # Footer RP
    if crown:
        embed.set_footer(text="👑 Le Patient Zero : celui qui inaugure chaque contagion.")
    else:
        embed.set_footer(text="⚠️ Les faibles tombent, seuls les plus endurcis survivent.")

    await interaction.response.send_message(embed=embed)

# ---------- Commandes Admin ----------

@bot.tree.command(name="unlink", description="Délier un joueur (admin).")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(member="Joueur à délier")
async def unlink(interaction: discord.Interaction, member: discord.Member):
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM players WHERE discord_id = ?", (str(member.id),))
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
                season_id = ?
        """, (new_season,))
    await interaction.response.send_message(
        f"🆕 La saison {new_season} commence ! Tous les joueurs ont été reset à 1000 MMR.",
        ephemeral=True
    )

# ---------- Ranked ON/OFF ----------

@bot.tree.command(name="ranked_on", description="Réactiver le mode Ranked (tes parties comptent à nouveau)")
async def ranked_on(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE players SET active_ranked = 1 WHERE discord_id = ?", (str(interaction.user.id),))
    await interaction.followup.send("✅ Ton mode **Ranked** est maintenant **activé** !", ephemeral=True)

@bot.tree.command(name="ranked_off", description="Désactiver le Ranked (jouer chill, parties ignorées)")
async def ranked_off(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE players SET active_ranked = 0 WHERE discord_id = ?", (str(interaction.user.id),))
    await interaction.followup.send("⏸️ Tu es maintenant en mode **chill** : tes parties ne compteront plus pour le Ranked.", ephemeral=True)

@bot.tree.command(name="sync", description="Resynchronise les commandes slash")
async def sync(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        synced = await bot.tree.sync()
        await interaction.followup.send(f"✅ {len(synced)} slash commands resynchronisées.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Erreur lors de la synchro : {e}", ephemeral=True)

# ---------- Fin de match (flow complet) ----------

# ========= BLOC UNIQUE DE REMPLACEMENT : Stats + /matchend =========
import asyncio

# --- Accusé de réception safe (évite "Échec de l'interaction")
async def safe_ack(interaction: discord.Interaction, *, ephemeral: bool = True):
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral)

# ----- store temporaire pour la saisie -----
class StatsStore:
    """Stocke les résultats d'un match pendant la saisie."""
    def __init__(self, players: list[str]):
        self.players = players
        self.index = 0
        self.results_kills: dict[str, int] = {}
        self.results_dmg: dict[str, int] = {}

    def has_next(self) -> bool:
        return self.index < len(self.players)

    def next_player(self) -> str:
        p = self.players[self.index]
        self.index += 1
        return p

# -------- Vue "Continuer" (ouvre le prochain modal ou finalise) --------
class NextModalView(discord.ui.View):
    def __init__(
        self,
        store: StatsStore,
        roles: Dict[str, str],
        players_all: list[str],
        selected_scenarios: list[str] | None,
        map_name: str | None
    ):
        super().__init__(timeout=300)
        self.store = store
        self.roles_all = roles
        self.players_all = players_all
        self.selected_scenarios = selected_scenarios or []
        self.map_name = map_name

    @discord.ui.button(label="➡️ Continuer (joueur suivant)", style=discord.ButtonStyle.primary)
    async def next_btn(self, inter: discord.Interaction, button: discord.ui.Button):
        # S'il reste un joueur → ouvrir son modal
        if self.store.has_next():
            nxt = self.store.next_player()
            nxt_role = self.roles_all.get(nxt, "humain")
            await inter.response.send_modal(
                PlayerStatsModal(
                    self.store, nxt, nxt_role,
                    roles=self.roles_all,
                    players_all=self.players_all,
                    selected_scenarios=self.selected_scenarios,
                    map_name=self.map_name
                )
            )
            return

        # Sinon → finaliser
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True)

        await finalize_match(
            inter,
            self.players_all,
            self.roles_all,
            self.store.results_kills,
            self.store.results_dmg,
            self.selected_scenarios,
            self.map_name
        )
        await inter.followup.send("✅ Match finalisé avec succès.", ephemeral=True)
        self.stop()

# ----- Modal pour UN joueur (saisie simple) -----
class PlayerStatsModal(discord.ui.Modal):
    def __init__(
        self,
        store: StatsStore,
        player: str,
        role: str,
        *,
        roles: Dict[str, str],
        players_all: list[str],
        selected_scenarios: list[str] | None,
        map_name: str | None
    ):
        super().__init__(title=f"Stats — {player}", timeout=300)

        self.store = store
        self.player = player
        self.role = role
        self.roles_all = roles
        self.players_all = players_all
        self.selected_scenarios = selected_scenarios or []
        self.map_name = map_name

        self.input_kills = discord.ui.TextInput(
            label="⚔️ Kills",
            style=discord.TextStyle.short,
            required=True,
            min_length=1,
            max_length=3,
            default="0"
        )
        self.input_dmg = discord.ui.TextInput(
            label="💥 Dégâts (si Infecté/First Z, sinon 0)",
            style=discord.TextStyle.short,
            required=True,
            min_length=1,
            max_length=6,
            default="0"
        )
        self.add_item(self.input_kills)
        self.add_item(self.input_dmg)

    async def on_submit(self, interaction: discord.Interaction):
        # parse safe
        def to_int(s: str, default: int = 0) -> int:
            try:
                return int((s or "").strip() or default)
            except Exception:
                return default

        k = to_int(self.input_kills.value, 0)
        d = to_int(self.input_dmg.value, 0)
        if self.role == "humain":
            d = 0
        d = max(0, min(d, 1_000_000))

        self.store.results_kills[self.player] = k
        self.store.results_dmg[self.player] = d

        # ➜ On ne rouvre PAS un modal directement (ça peut 400).
        #    On envoie un message avec un bouton "Continuer" (nouvelle interaction propre).
        view = NextModalView(
            self.store,
            roles=self.roles_all,
            players_all=self.players_all,
            selected_scenarios=self.selected_scenarios,
            map_name=self.map_name
        )

        if self.store.has_next():
            text = f"✅ Données enregistrées pour **{self.player}**.\nClique sur **Continuer** pour le joueur suivant."
        else:
            text = f"✅ Données enregistrées pour **{self.player}**.\nClique sur **Continuer** pour **finaliser** le match."

        await interaction.response.send_message(text, ephemeral=True, view=view)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        if not interaction.response.is_done():
            await interaction.response.send_message(f"❌ Erreur dans la saisie : {error}", ephemeral=True)

# ---------- Commande /matchend (flow complet) ----------
@bot.tree.command(name="matchend", description="Enregistrer la fin d'un match Ranked.")
async def matchend(interaction: discord.Interaction):
    """Flux complet : sélection de la map, scénarios, joueurs, rôles, puis saisie des stats (1 joueur = 1 modal)."""
    await interaction.response.defer(ephemeral=True)

    # Étape 0 : chargement joueurs
    with connect_db() as conn:
        c = conn.cursor()
        c.execute("SELECT minecraft_name FROM players")
        all_players = [row[0] for row in c.fetchall()]

    if not all_players:
        await interaction.edit_original_response(content="⚠️ Aucun joueur enregistré dans la base.", view=None)
        return

    # Utilitaire Select générique (timeout plus long)
    def make_select(placeholder: str, options: list[discord.SelectOption], *, min_v=1, max_v=1):
        view = discord.ui.View(timeout=300)
        sel = discord.ui.Select(placeholder=placeholder, options=options, min_values=min_v, max_values=max_v)

        async def _cb(inter: discord.Interaction):
            if not inter.response.is_done():
                await inter.response.defer()
            view.stop()

        sel.callback = _cb
        view.add_item(sel)
        return view, sel

    # Étape 1 : Catégorie
    categories = ["small", "mid", "large"]
    cat_opts = [discord.SelectOption(label=c.capitalize(), value=c) for c in categories]
    view_cat, sel_cat = make_select("📏 Choisis une catégorie de map", cat_opts)

    await interaction.edit_original_response(content="🗺️ Sélectionne la **taille** de la map :", view=view_cat)
    await view_cat.wait()
    if not sel_cat.values:
        await interaction.edit_original_response(content="❌ Aucune catégorie choisie, match annulé.", view=None)
        return

    chosen_cat = sel_cat.values[0]
    filtered_maps = [m for m, s in MAPS.items() if s == chosen_cat]
    if not filtered_maps:
        await interaction.edit_original_response(content="❌ Aucune map disponible pour cette catégorie.", view=None)
        return

    # Étape 2 : Map
    map_opts = [discord.SelectOption(label=m, value=m) for m in filtered_maps[:25]]
    view_map, sel_map = make_select(f"🌍 Choisis la map ({chosen_cat})", map_opts)

    await interaction.edit_original_response(content="🌍 Choisis la **map** :", view=view_map)
    await view_map.wait()
    if not sel_map.values:
        await interaction.edit_original_response(content="❌ Aucune map choisie, match annulé.", view=None)
        return

    map_name = sel_map.values[0]

    # Étape 3 : Scénarios
    scen_opts = [
        discord.SelectOption(label="Aucun", value="none", description="Aucune condition spéciale active 💤")
    ] + [
        discord.SelectOption(
            label=name,
            value=name,
            description=f"Avantage : {'🧟 Zombies' if val < 0 else '👤 Humains' if val > 0 else '⚖️ Neutre'}"
        )
        for name, val in SCENARIOS.items()
    ][:24]
    view_scen, sel_scen = make_select("🎭 Scénarios actifs (max 2)", scen_opts, min_v=0, max_v=2)

    await interaction.edit_original_response(content="🎭 Choisis les **scénarios** (0 à 2) :", view=view_scen)
    await view_scen.wait()
    selected_scenarios = [s for s in sel_scen.values if s != "none"]

    # Étape 4 : Joueurs (picker robuste)
    class PlayersPicker(discord.ui.View):
        def __init__(self, all_players: list[str]):
            super().__init__(timeout=300)
            self.all_players = list(all_players)
            self.available = list(all_players)
            self.selected: list[str] = []

            # Select d'un joueur (1 seul à la fois)
            self.sel_player = discord.ui.Select(
                placeholder="👤 Choisis un joueur à ajouter/enlever",
                min_values=1, max_values=1,
                options=[discord.SelectOption(label=p, value=p) for p in self.available[:25]]
            )
            self.sel_player.callback = self._on_select_change
            self.add_item(self.sel_player)

            # Bouton Ajouter
            self.btn_add = discord.ui.Button(label="➕ Ajouter", style=discord.ButtonStyle.primary)
            self.btn_add.callback = self._add_player
            self.add_item(self.btn_add)

            # Bouton Retirer
            self.btn_remove = discord.ui.Button(label="➖ Retirer", style=discord.ButtonStyle.secondary)
            self.btn_remove.callback = self._remove_player
            self.add_item(self.btn_remove)

            # Bouton Valider
            self.btn_confirm = discord.ui.Button(label="✅ Valider la sélection", style=discord.ButtonStyle.success, disabled=True)
            self.btn_confirm.callback = self._confirm
            self.add_item(self.btn_confirm)

        # — helpers UI —
        def _refresh_options(self):
            self.sel_player.options = [discord.SelectOption(label=p, value=p) for p in (self.available + self.selected)[:25]]

        def _summary_text(self) -> str:
            if not self.selected:
                return "👥 Aucun joueur sélectionné pour l’instant.\n➡️ Choisis un joueur puis clique sur **Ajouter**."
            return (
                "👥 Joueurs sélectionnés (**{}**): {}\n"
                "• Utilise **Retirer** pour enlever un nom.\n"
                "• Clique **Valider la sélection** quand c’est bon."
            ).format(len(self.selected), ", ".join(self.selected))

        async def _on_select_change(self, inter: discord.Interaction):
            if not inter.response.is_done():
                await inter.response.defer()

        async def _add_player(self, inter: discord.Interaction):
            if not self.sel_player.values:
                await inter.response.send_message("⚠️ Choisis d’abord un joueur.", ephemeral=True)
                return
            name = self.sel_player.values[0]
            if name in self.selected:
                await inter.response.send_message("ℹ️ Ce joueur est déjà dans la liste.", ephemeral=True)
                return
            self.selected.append(name)
            if name in self.available:
                self.available.remove(name)

            self.btn_confirm.disabled = len(self.selected) == 0

            self._refresh_options()
            text = self._summary_text()
            if not inter.response.is_done():
                await inter.response.edit_message(content=text, view=self)
            else:
                await inter.followup.edit_message(message_id=inter.message.id, content=text, view=self)

        async def _remove_player(self, inter: discord.Interaction):
            if not self.sel_player.values:
                await inter.response.send_message("⚠️ Choisis d’abord un joueur.", ephemeral=True)
                return
            name = self.sel_player.values[0]
            if name in self.selected:
                self.selected.remove(name)
                if name not in self.available:
                    self.available.append(name)

            self.btn_confirm.disabled = len(self.selected) == 0
            self._refresh_options()
            text = self._summary_text()
            if not inter.response.is_done():
                await inter.response.edit_message(content=text, view=self)
            else:
                await inter.followup.edit_message(message_id=inter.message.id, content=text, view=self)

        async def _confirm(self, inter: discord.Interaction):
            if not self.selected:
                await inter.response.send_message("⚠️ Ajoute au moins un joueur.", ephemeral=True)
                return
            if not inter.response.is_done():
                await inter.response.defer()
            self.stop()

    # -- utilisation du picker --
    picker = PlayersPicker(all_players)
    await interaction.edit_original_response(content="👥 **Sélection des joueurs**\n" + picker._summary_text(), view=picker)
    await picker.wait()

    participants = picker.selected
    if not participants:
        await interaction.edit_original_response(content="❌ Aucun joueur sélectionné, match annulé.", view=None)
        return

    # Étape 5 : Rôles + DÉMARRAGE de la saisie (1 joueur = 1 modal)
    class RolesSelect(discord.ui.View):
        def __init__(self, players: list[str], selected_scenarios=None, map_name=None):
            super().__init__(timeout=300)
            self.players_all = list(players)          # tous les joueurs
            self.players_left = list(players)         # joueurs restants
            self.roles: Dict[str, str] = {}           # {name: role}
            self.selected_scenarios = selected_scenarios or []
            self.map_name = map_name
            self._store: StatsStore | None = None

            # --- Select joueur
            self.sel_player = discord.ui.Select(
                placeholder="👤 Choisir un joueur à assigner",
                min_values=1, max_values=1,
                options=[]
            )
            self.sel_player.callback = self._on_player_changed
            self.add_item(self.sel_player)

            # --- Select rôle
            self.sel_role = discord.ui.Select(
                placeholder="🎭 Choisir un rôle",
                min_values=1, max_values=1,
                options=[
                    discord.SelectOption(label="Humain",  value="humain",  emoji="🏹"),
                    discord.SelectOption(label="Infecté", value="infected", emoji="🧟"),
                    discord.SelectOption(label="First Z", value="firstz",  emoji="🦠"),
                ]
            )
            self.sel_role.callback = self._on_role_changed
            self.add_item(self.sel_role)

            # --- Bouton Assigner
            self.btn_assign = discord.ui.Button(label="➕ Assigner le rôle au joueur", style=discord.ButtonStyle.primary)
            self.btn_assign.callback = self._assign_current
            self.add_item(self.btn_assign)

            # --- Bouton démarrer stats
            self.btn_start = discord.ui.Button(label="✅ Ouvrir la saisie des stats", style=discord.ButtonStyle.success, disabled=True)
            self.btn_start.callback = self._start_stats_flow
            self.add_item(self.btn_start)

            # --- Bouton Réinitialiser
            self.btn_reset = discord.ui.Button(label="♻️ Réinitialiser l'assignation", style=discord.ButtonStyle.secondary)
            self.btn_reset.callback = self._reset_all
            self.add_item(self.btn_reset)

            # Init
            self._refresh_player_select()

        # ---------- Helpers ----------
        def _refresh_player_select(self):
            if self.players_left:
                self.sel_player.disabled = False
                self.sel_player.options = [
                    discord.SelectOption(label=p, value=p) for p in self.players_left[:25]
                ]
            else:
                self.sel_player.disabled = True
                self.sel_player.options = [
                    discord.SelectOption(
                        label="✅ Tous les joueurs sont assignés",
                        value="_done",
                        description="Clique sur « Ouvrir la saisie des stats »"
                    )
                ]

        # ---------- Callbacks ----------
        async def _on_player_changed(self, inter: discord.Interaction):
            if not inter.response.is_done():
                await inter.response.defer()

        async def _on_role_changed(self, inter: discord.Interaction):
            if not inter.response.is_done():
                await inter.response.defer()

        async def _assign_current(self, inter: discord.Interaction):
            if not self.sel_player.values or self.sel_player.values[0] == "_done":
                await inter.response.send_message("⚠️ Choisis d'abord un joueur.", ephemeral=True)
                return
            if not self.sel_role.values:
                await inter.response.send_message("⚠️ Choisis d'abord un rôle.", ephemeral=True)
                return

            player = self.sel_player.values[0]
            role = self.sel_role.values[0]
            self.roles[player] = role

            if player in self.players_left:
                self.players_left.remove(player)

            self._refresh_player_select()
            self.btn_start.disabled = len(self.players_left) > 0

            text = (
                f"👥 Assignés: **{len(self.roles)}/{len(self.players_all)}**\n"
                f"• Dernier: **{player}** → **{role}**\n"
                f"{'✅ Tout le monde est assigné : tu peux lancer la saisie des stats.' if not self.players_left else '➡️ Continue d’assigner les rôles.'}"
            )
            if not inter.response.is_done():
                await inter.response.edit_message(content=text, view=self)
            else:
                await inter.followup.edit_message(message_id=inter.message.id, content=text, view=self)

        async def _reset_all(self, inter: discord.Interaction):
            self.players_left = list(self.players_all)
            self.roles.clear()
            self._refresh_player_select()
            self.btn_start.disabled = True
            text = "♻️ Assignations réinitialisées."
            if not inter.response.is_done():
                await inter.response.edit_message(content=text, view=self)
            else:
                await inter.followup.edit_message(message_id=inter.message.id, content=text, view=self)

        async def _start_stats_flow(self, inter: discord.Interaction):
            if len(self.roles) < len(self.players_all):
                await inter.response.send_message("⚠️ Tous les rôles n'ont pas encore été assignés.", ephemeral=True)
                return

            # NE PAS defer ici : on doit répondre par un modal
            self._store = StatsStore(self.players_all)
            first = self._store.next_player()
            role = self.roles.get(first, "humain")

            await inter.response.send_modal(
                PlayerStatsModal(
                    self._store, first, role,
                    roles=self.roles,
                    players_all=self.players_all,
                    selected_scenarios=self.selected_scenarios,
                    map_name=self.map_name
                )
            )
            # ⛔️ Rien d'autre ici : pas de boucle, pas de finalize.

    # Affiche la vue de rôles (⚠️ ceci doit rester DANS la fonction matchend)
    roles_view = RolesSelect(participants, selected_scenarios=selected_scenarios, map_name=map_name)
    await interaction.edit_original_response(
        content=(
            f"🧩 **Map** : {map_name} ({chosen_cat})\n"
            f"🎭 **Scénarios** : {', '.join(selected_scenarios) or 'Aucun'}\n\n"
            "Assigne les **rôles** aux joueurs puis clique sur **✅ Ouvrir la saisie des stats** :"
        ),
        view=roles_view
    )
    await roles_view.wait()
    return

# ============================================================
#  AUTO-RECOVERY : Hall des Légendes manquant
# ============================================================
async def ensure_hall_message(guild: discord.Guild):
    """
    Vérifie que le message du Hall des Légendes existe.
    Si supprimé, le recrée automatiquement et met à jour la config.
    """
    HALL_CHANNEL_ID = 1423665644519297034  # 👑・hall-des-légendes

    # 1️⃣ Priorité : cherche par ID
    channel = guild.get_channel(HALL_CHANNEL_ID)

    # 2️⃣ Fallback : cherche par nom
    if not channel:
        channel = discord.utils.get(guild.text_channels, name="👑・hall-des-légendes")

    if not channel:
        print("⚠️ Aucun salon '👑・hall-des-légendes' trouvé (ni ID ni nom). Impossible de créer le Hall.")
        return

    hall_id = get_config("hall_message_id")

    # Vérifie si le message configuré existe encore
    existing_msg = None
    if hall_id:
        try:
            existing_msg = await channel.fetch_message(int(hall_id))
        except discord.NotFound:
            print("⚠️ Le message du Hall a été supprimé.")
        except Exception as e:
            print(f"[Erreur Hall] Impossible de récupérer le message existant : {e}")

    # Si aucun message valide, recrée un Hall neuf
    if not existing_msg:
        print("🛠️ Création automatique d’un nouveau Hall des Légendes...")
        embed = discord.Embed(
            title="🏛️ Hall des Légendes — Saison 1",
            description="*Chaque saison, les plus grands inscrivent leur nom dans ces murs.*",
            color=discord.Color.gold()
        )
        embed.add_field(name="En attente...", value="Le premier match n’a pas encore eu lieu.", inline=False)
        msg = await channel.send(embed=embed)
        set_config("hall_message_id", str(msg.id))
        print(f"✅ Nouveau Hall créé automatiquement (msg ID {msg.id})")
    else:
        print("✅ Hall des Légendes déjà en place, aucune recréation nécessaire.")

# =========================
#   MESSAGES RP AUTOMATIQUES (JITTER + COOLDOWN PERSISTANT)
# =========================
import asyncio
import random
import time

# 🔧 FENÊTRES ALÉATOIRES (en heures) — ajuste si tu veux
FIRECAMP_FIRST_DELAY_H = (1, 6)     # premier envoi 1–6 h après démarrage (jamais instantané)
FIRECAMP_WINDOW_H      = (72, 168)  # ensuite 3–7 jours

RADIO_FIRST_DELAY_H    = (1, 6)     # premier envoi 1–6 h après démarrage
RADIO_WINDOW_H         = (48, 96)   # ensuite 2–4 jours

# 🗄️ Clés de persistance (table bot_config)
CFG_FIRECAMP_LAST = "firecamp_last_sent"
CFG_RADIO_LAST    = "radio_last_sent"
CFG_RP_ENABLED    = "rp_auto_enabled"  # "1" ou "0" (par défaut: activé)

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
    return val is None or val == "1"  # si non configuré -> activé

async def firecamp_daemon():
    await bot.wait_until_ready()
    # Amorçage : JAMAIS d'envoi instantané
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

                # Post seulement si le dernier envoi est assez ancien
                if now - last >= min_gap:
                    msg = random.choice(firecamp_messages)
                    await channel.send(msg)
                    set_config(CFG_FIRECAMP_LAST, str(now))
                    log.info(f"🔥 Firecamp → {guild.name}/{channel.name}")

            # Prochain réveil aléatoire dans la fenêtre
            await _sleep_rand(*FIRECAMP_WINDOW_H)

        except Exception as e:
            log.error(f"[firecamp_daemon] {e}")
            await asyncio.sleep(_hours(1))  # backoff

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
            await asyncio.sleep(_hours(1))  # backoff

# ✅ Démarrage sûr (pour éviter de lancer 2×)
def ensure_rp_daemons_started():
    if not getattr(bot, "_rp_tasks_started", False):
        bot.loop.create_task(firecamp_daemon())
        bot.loop.create_task(radio_daemon())
        bot._rp_tasks_started = True
        log.info("📻 Daemons RP démarrés (feu de camp + radio) avec délais aléatoires.")

# (Optionnel) commandes admin pour activer/désactiver globalement
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
