import sqlite3
import os

# Exakter Pfad zu deiner Datenbank
db_path = os.path.join("app", "db", "kompass.db")

def migrate():
    if not os.path.exists(db_path):
        print(f"Fehler: Datenbankdatei unter '{db_path}' nicht gefunden!")
        return

    print(f"Verwende Datenbank: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Spalte 'dauer' hinzufügen
    try:
        cursor.execute("ALTER TABLE jugendgruppen ADD COLUMN dauer INTEGER;")
        print("✔ Spalte 'dauer' erfolgreich hinzugefügt.")
    except sqlite3.OperationalError as e:
        print(f"ℹ Hinweis zu 'dauer': {e}")

    # Spalte 'gruppenart' hinzufügen
    try:
        cursor.execute("ALTER TABLE jugendgruppen ADD COLUMN gruppenart TEXT;")
        print("✔ Spalte 'gruppenart' erfolgreich hinzugefügt.")
    except sqlite3.OperationalError as e:
        print(f"ℹ Hinweis zu 'gruppenart': {e}")

    conn.commit()
    conn.close()
    print("Fertig! Die Datenbank wurde erfolgreich aktualisiert.")

if __name__ == "__main__":
    migrate()