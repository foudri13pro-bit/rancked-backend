import sqlite3

conn = sqlite3.connect("rankedinfected.db")
cursor = conn.cursor()

print("=== Colonnes de la table players ===")
cursor.execute("PRAGMA table_info(players)")
for row in cursor.fetchall():
    print(row)

print("\n=== Contenu de la table players ===")
cursor.execute("SELECT * FROM players")
rows = cursor.fetchall()

print(f"Nombre de lignes : {len(rows)}")
for row in rows:
    print(row)

conn.close()