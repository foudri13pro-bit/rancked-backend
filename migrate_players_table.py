import sqlite3

DB_PATH = "rankedinfected.db"

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

try:
    # Vérifie si la table players existe
    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name='players'
    """)
    table_exists = cursor.fetchone()

    if not table_exists:
        print("❌ La table 'players' n'existe pas.")
        conn.close()
        exit()

    # Renommer l'ancienne table
    cursor.execute("ALTER TABLE players RENAME TO players_old;")

    # Recréer la nouvelle table avec contraintes propres
    cursor.execute("""
        CREATE TABLE players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id VARCHAR NOT NULL UNIQUE,
            minecraft_name VARCHAR NOT NULL UNIQUE,
            zenavia_player_id VARCHAR NOT NULL UNIQUE,
            current_mmr INTEGER NOT NULL DEFAULT 1000,
            games_played INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            kills INTEGER NOT NULL DEFAULT 0,
            deaths INTEGER NOT NULL DEFAULT 0,
            infections INTEGER NOT NULL DEFAULT 0,
            survivals INTEGER NOT NULL DEFAULT 0,
            first_z_count INTEGER NOT NULL DEFAULT 0,
            active_ranked BOOLEAN NOT NULL DEFAULT 1,
            updated_at DATETIME
        );
    """)

    # Copier les données existantes
    cursor.execute("""
        INSERT INTO players (
            id,
            discord_id,
            minecraft_name,
            zenavia_player_id,
            current_mmr,
            games_played,
            wins,
            losses,
            kills,
            deaths,
            infections,
            survivals,
            first_z_count,
            active_ranked,
            updated_at
        )
        SELECT
            id,
            discord_id,
            minecraft_name,
            zenavia_player_id,
            current_mmr,
            games_played,
            wins,
            losses,
            kills,
            deaths,
            infections,
            survivals,
            first_z_count,
            active_ranked,
            updated_at
        FROM players_old;
    """)

    # Supprimer l'ancienne table
    cursor.execute("DROP TABLE players_old;")

    conn.commit()
    print("✅ Migration terminée : table 'players' recréée avec NOT NULL + UNIQUE.")

except Exception as e:
    conn.rollback()
    print(f"❌ Erreur pendant la migration : {e}")

finally:
    conn.close()