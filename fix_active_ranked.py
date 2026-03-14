import sqlite3

db_path = "rankedinfected.db"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("""
    UPDATE players
    SET active_ranked = 1
    WHERE active_ranked IS NULL
""")

print(f"✅ Lignes mises à jour : {cursor.rowcount}")

conn.commit()
conn.close()

print("Correction terminée.")