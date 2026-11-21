import sqlite3
from contextlib import contextmanager

from flask_login import UserMixin
from werkzeug.security import generate_password_hash


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    rows = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def init_db():
    statements_kompass = [
        """
        CREATE TABLE IF NOT EXISTS mitglieder (
            id INTEGER PRIMARY KEY,
            vorname TEXT not NULL,
            nachname TEXT not NULL,
            geburtsdatum DATE not NULL,
            geschlecht TEXT,
            adresse_id INTEGER,
            bilder BOOLEAN,
            unterschrift TEXT,
            mitgliedsnummer TEXT,
            archiviert INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (adresse_id) REFERENCES adressen(id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS adressen (
            id INTEGER PRIMARY KEY,
            strasse TEXT NOT NULL,
            hausnummer TEXT NOT NULL,
            plz TEXT NOT NULL,
            ort TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS erziehungsberechtigte (
            id INTEGER PRIMARY KEY,
            vorname TEXT NOT NULL,
            nachname TEXT NOT NULL,
            email TEXT NOT NULL,
            telefon TEXT NOT NULL,
            adresse_id INTEGER,
            FOREIGN KEY (adresse_id) REFERENCES adressen(id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS mitglied_erziehungsberechtigte (
            mitglied_id INTEGER,
            erziehungsberechtigter_id INTEGER,
            rolle TEXT,
            PRIMARY KEY (mitglied_id, erziehungsberechtigter_id),
            FOREIGN KEY (mitglied_id) REFERENCES mitglieder(id),
            FOREIGN KEY (erziehungsberechtigter_id) REFERENCES erziehungsberechtigte(id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS jugendgruppen (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            beschreibung TEXT,
            wochentag TEXT NOT NULL,
            startzeit TIME NOT NULL,
            endzeit TIME NOT NULL,
            dauer INTEGER,
            gruppenart TEXT
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS gruppenleiter (
            id INTEGER PRIMARY KEY,
            vorname TEXT NOT NULL,
            nachname TEXT NOT NULL,
            geburtsdatum DATE NOT NULL,
            iban TEXT NOT NULL,
            bic TEXT NOT NULL,
            bank TEXT NOT NULL,
            telefon TEXT NOT NULL,
            gruppenrolle TEXT NOT NULL, -- Jugendleiter, Helfer
            vereinsrolle TEXT NOT NULL, -- keine, Jugendausschuss, Jugendreferent
            account_id INTEGER
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS gruppenleiter_jugendgruppen (
            gruppenleiter_id INTEGER,
            jugendgruppe_id INTEGER,
            PRIMARY KEY (gruppenleiter_id, jugendgruppe_id),
            FOREIGN KEY (gruppenleiter_id) REFERENCES gruppenleiter(id),
            FOREIGN KEY (jugendgruppe_id) REFERENCES jugendgruppen(id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS mitglied_jugendgruppen (
            mitglied_id INTEGER,
            jugendgruppe_id INTEGER,
            PRIMARY KEY (mitglied_id, jugendgruppe_id),
            FOREIGN KEY (mitglied_id) REFERENCES mitglieder(id),
            FOREIGN KEY (jugendgruppe_id) REFERENCES jugendgruppen(id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS fortbildungen (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            datum DATE NOT NULL,
            gueltig_bis DATE
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS gruppenleiter_fortbildungen (
            gruppenleiter_id INTEGER,
            fortbildung_id INTEGER,
            PRIMARY KEY (gruppenleiter_id, fortbildung_id),
            FOREIGN KEY (gruppenleiter_id) REFERENCES gruppenleiter(id),
            FOREIGN KEY (fortbildung_id) REFERENCES fortbildungen(id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS anwesenheit (
            mitglied_id INTEGER,
            gruppe_id INTEGER,
            datum DATE NOT NULL,
            anwesend BOOLEAN NOT NULL,
            PRIMARY KEY (mitglied_id, gruppe_id, datum),
            FOREIGN KEY (mitglied_id) REFERENCES mitglieder(id),
            FOREIGN KEY (gruppe_id) REFERENCES jugendgruppen(id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS anwesenheit_leiter (
            gruppenleiter_id INTEGER,
            gruppe_id INTEGER,
            datum DATE NOT NULL,
            anwesend BOOLEAN NOT NULL,
            PRIMARY KEY (gruppenleiter_id, gruppe_id, datum),
            FOREIGN KEY (gruppenleiter_id) REFERENCES gruppenleiter(id),
            FOREIGN KEY (gruppe_id) REFERENCES jugendgruppen(id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS mitglieder_unapproved (
            id INTEGER PRIMARY KEY,
            vorname TEXT not NULL,
            nachname TEXT not NULL,
            geburtsdatum DATE not NULL,
            geschlecht TEXT,
            adresse_id INTEGER,
            unterschrift TEXT not NULL,
            mitgliedschaft BOOLEAN not NULL,
            beitraege BOOLEAN not NULL,
            datenschutz BOOLEAN not NULL,
            bilder BOOLEAN,
            richtigkeit BOOLEAN not NULL,
            FOREIGN KEY (adresse_id) REFERENCES adressen_unapproved(id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS adressen_unapproved (
            id INTEGER PRIMARY KEY,
            strasse TEXT NOT NULL,
            hausnummer TEXT NOT NULL,
            plz TEXT NOT NULL,
            ort TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS erziehungsberechtigte_unapproved (
            id INTEGER PRIMARY KEY,
            vorname TEXT NOT NULL,
            nachname TEXT NOT NULL,
            email TEXT NOT NULL,
            telefon TEXT NOT NULL,
            adresse_id INTEGER,
            FOREIGN KEY (adresse_id) REFERENCES adressen_unapproved(id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS mitglied_erziehungsberechtigte_unapproved (
            mitglied_id INTEGER,
            erziehungsberechtigter_id INTEGER,
            rolle TEXT,
            PRIMARY KEY (mitglied_id, erziehungsberechtigter_id),
            FOREIGN KEY (mitglied_id) REFERENCES mitglieder_unapproved(id),
            FOREIGN KEY (erziehungsberechtigter_id) REFERENCES erziehungsberechtigte_unapproved(id)
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS kuendigungen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            erstellt_am DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            mitglied_vorname TEXT NOT NULL,
            mitglied_nachname TEXT NOT NULL,
            eb_vorname TEXT NOT NULL,
            eb_nachname TEXT NOT NULL,
            eb_email TEXT NOT NULL,
            kuendigungsart TEXT NOT NULL, -- fruehestmoeglich | quartalsende | monatsende
            kuendigungsdatum DATE NOT NULL,
            dav_sektion_kuendigen BOOLEAN NOT NULL DEFAULT 0,
            grund TEXT,
            datenschutz_bestaetigt BOOLEAN NOT NULL,
            richtigkeit_bestaetigt BOOLEAN NOT NULL,
            unterschrift TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'eingegangen', -- eingegangen | bestaetigt | abgelehnt
            eingangsbestaetigung_gesendet BOOLEAN NOT NULL DEFAULT 0,
            vorgangsnummer TEXT
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS mail_einstellungen (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            smtp_server TEXT NOT NULL DEFAULT 'smtp.ionos.de',
            smtp_port INTEGER NOT NULL DEFAULT 465,
            smtp_username TEXT NOT NULL DEFAULT 'kompass@jdav-hildesheim.de',
            smtp_password TEXT,
            sender_name TEXT NOT NULL DEFAULT 'JDAV Kompass Hildesheim'
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS mail_templates (
            schluessel TEXT PRIMARY KEY,
            bezeichnung TEXT NOT NULL,
            betreff TEXT NOT NULL,
            text_vorlage TEXT NOT NULL,
            html_vorlage TEXT,
            aktualisiert_am DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS mail_adressbuch (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bezeichnung TEXT NOT NULL,
            email TEXT NOT NULL,
            aktiv BOOLEAN NOT NULL DEFAULT 1
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS mitglied_historie (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mitglied_id INTEGER NOT NULL,
            ereignis_typ TEXT NOT NULL,
            zeitpunkt TEXT NOT NULL,
            bearbeiter_uname TEXT,
            detail_text TEXT,
            vorgangsnummer TEXT
        );
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_mitglied_historie_mitglied_id
        ON mitglied_historie(mitglied_id);
        """,
    ]

    statements_accounts = [
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uname TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'pending',
            anzeigename TEXT
        );
        """,
    ]

    with get_kompass() as conn:
        cursor = conn.cursor()
        for statement in statements_kompass:
            cursor.execute(statement)

        # Migrationen für mitglieder
        if not _column_exists(cursor, "mitglieder", "archiviert"):
            cursor.execute("ALTER TABLE mitglieder ADD COLUMN archiviert INTEGER NOT NULL DEFAULT 0;")

        # Migrationen für jugendgruppen
        if not _column_exists(cursor, "jugendgruppen", "dauer"):
            cursor.execute("ALTER TABLE jugendgruppen ADD COLUMN dauer INTEGER;")
        if not _column_exists(cursor, "jugendgruppen", "gruppenart"):
            cursor.execute("ALTER TABLE jugendgruppen ADD COLUMN gruppenart TEXT;")

        # Migrationen für gruppenleiter & kuendigungen & mail_templates
        if not _column_exists(cursor, "gruppenleiter", "account_id"):
            cursor.execute("ALTER TABLE gruppenleiter ADD COLUMN account_id INTEGER;")
        if not _column_exists(cursor, "kuendigungen", "vorgangsnummer"):
            cursor.execute("ALTER TABLE kuendigungen ADD COLUMN vorgangsnummer TEXT;")
        if not _column_exists(cursor, "mail_templates", "html_vorlage"):
            cursor.execute("ALTER TABLE mail_templates ADD COLUMN html_vorlage TEXT;")

        # Migrationen für Kündigungsverarbeitung & Vermerk in 'mitglieder'
        if not _column_exists(cursor, "mitglieder", "kuendigung_datum"):
            cursor.execute("ALTER TABLE mitglieder ADD COLUMN kuendigung_datum DATE;")
        if not _column_exists(cursor, "mitglieder", "kuendigung_vorgangsnummer"):
            cursor.execute("ALTER TABLE mitglieder ADD COLUMN kuendigung_vorgangsnummer TEXT;")

        if not _column_exists(cursor, "kuendigungen", "bestaetigt_am"):
            cursor.execute("ALTER TABLE kuendigungen ADD COLUMN bestaetigt_am DATETIME;")
        if not _column_exists(cursor, "kuendigungen", "bestaetigt_von"):
            cursor.execute("ALTER TABLE kuendigungen ADD COLUMN bestaetigt_von TEXT;")
        if not _column_exists(cursor, "kuendigungen", "freitext_bestaetigung"):
            cursor.execute("ALTER TABLE kuendigungen ADD COLUMN freitext_bestaetigung TEXT;")
        if not _column_exists(cursor, "kuendigungen", "bestaetigung_gesendet"):
            cursor.execute("ALTER TABLE kuendigungen ADD COLUMN bestaetigung_gesendet BOOLEAN NOT NULL DEFAULT 0;")

        cursor.execute(
            """
            INSERT OR IGNORE INTO mail_einstellungen (
                id, smtp_server, smtp_port, smtp_username, smtp_password, sender_name
            ) VALUES (1, '', 465, '', NULL, '');
            """
        )

        # Template-Einträge mit neutralem Standardtext anlegen, damit sie im Admin-Panel bearbeitbar sind
        standard_templates = [
            (
                "kuendigung_eingang",
                "Kündigung – Eingangsbestätigung",
                "Eingangsbestätigung [#{{vorgangsnummer}}]",
                "Hallo {{eb_name}},\n\nvielen Dank für Deine Nachricht (Vorgangsnummer #{{vorgangsnummer}}).",
                None,
            ),
            (
                "kuendigung_bestaetigt",
                "Kündigung – Endgültige Bestätigung (Vorlage)",
                "Bestätigung der Kündigung [#{{vorgangsnummer}}]",
                "Hallo {{eb_name}},\n\nhiermit bestätigen wir Deine Kündigung (Vorgangsnummer #{{vorgangsnummer}}).",
                None,
            ),
        ]

        for tpl in standard_templates:
            cursor.execute(
                """
                INSERT OR IGNORE INTO mail_templates (
                    schluessel, bezeichnung, betreff, text_vorlage, html_vorlage
                ) VALUES (?, ?, ?, ?, ?);
                """,
                tpl,
            )

    with get_accounts() as conn:
        cursor = conn.cursor()
        for statement in statements_accounts:
            cursor.execute(statement)

        if not _column_exists(cursor, "accounts", "anzeigename"):
            cursor.execute("ALTER TABLE accounts ADD COLUMN anzeigename TEXT;")

        cursor.execute(
            """
            UPDATE accounts
            SET anzeigename = uname
            WHERE anzeigename IS NULL OR TRIM(anzeigename) = '';
            """
        )

        existing = cursor.execute(
            "SELECT id FROM accounts WHERE uname = ?",
            ("admin",),
        ).fetchone()

        if not existing:
            cursor.execute(
                """
                INSERT INTO accounts (uname, password, role, status, anzeigename)
                VALUES (?, ?, ?, ?, ?);
                """,
                ("admin", generate_password_hash("admin"), "4", "active", "admin"),
            )


@contextmanager
def get_accounts():
    conn = sqlite3.connect("app/db/accounts.db")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


@contextmanager
def get_kompass():
    conn = sqlite3.connect("app/db/kompass.db")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


class User(UserMixin):
    def __init__(self, id, username, role, anzeigename=None):
        self.id = id
        self.username = username
        self.role = role
        self.anzeigename = anzeigename

    @property
    def display_name(self):
        if self.anzeigename and self.anzeigename.strip():
            return self.anzeigename.strip()
        return self.username