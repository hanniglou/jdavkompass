import sqlite3
import os

# Exakter Pfad zur Account-Datenbank
db_path = os.path.join("app", "db", "accounts.db")


def migrate():
    if not os.path.exists(db_path):
        print(f"Fehler: Datenbankdatei unter '{db_path}' nicht gefunden!")
        return

    print(f"Verwende Datenbank: {db_path}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Spalte 'anzeigename' zu 'accounts' hinzufügen
    try:
        cursor.execute("ALTER TABLE accounts ADD COLUMN anzeigename TEXT;")
        print("✔ Spalte 'anzeigename' erfolgreich zu 'accounts' hinzugefügt.")
    except sqlite3.OperationalError as e:
        print(f"ℹ Hinweis zu 'anzeigename': {e}")

    # Bestehende Accounts mit 'uname' vorbelegen, falls noch NULL oder leer
    try:
        cursor.execute("""
            UPDATE accounts
            SET anzeigename = uname
            WHERE anzeigename IS NULL OR TRIM(anzeigename) = '';
        """)
        print(f"✔ Bestehende Accounts aktualisiert ({cursor.rowcount} Zeilen).")
    except sqlite3.OperationalError as e:
        print(f"ℹ Hinweis beim Vorbelegen: {e}")

    conn.commit()
    conn.close()
    print("Fertig! Die Account-Datenbank wurde erfolgreich aktualisiert.")


if __name__ == "__main__":
    migrate()