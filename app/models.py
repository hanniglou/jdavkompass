import sqlite3
from contextlib import contextmanager
from pathlib import Path

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
        """
        CREATE TABLE IF NOT EXISTS app_einstellungen (
            schluessel TEXT PRIMARY KEY,
            wert TEXT NOT NULL
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS gruppenarten (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            dauer_minuten INTEGER NOT NULL,
            betreuungsschluessel INTEGER NOT NULL DEFAULT 8,
            betreuungsgeld_euro TEXT NOT NULL,
            sortierung INTEGER NOT NULL DEFAULT 0
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS warteliste_tierwoerter (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wort TEXT UNIQUE NOT NULL,
            aktiv INTEGER NOT NULL DEFAULT 1
        );
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
        if not _column_exists(cursor, "jugendgruppen", "max_teilnehmer"):
            cursor.execute(
                "ALTER TABLE jugendgruppen ADD COLUMN max_teilnehmer INTEGER NOT NULL DEFAULT 12;"
            )
            cursor.execute(
                """
                UPDATE jugendgruppen
                SET max_teilnehmer = 6
                WHERE LOWER(TRIM(COALESCE(gruppenart, ''))) = 'minis'
                   OR LOWER(TRIM(COALESCE(name, ''))) LIKE '%mini%';
                """
            )
        if not _column_exists(cursor, "jugendgruppen", "jahrgang"):
            cursor.execute("ALTER TABLE jugendgruppen ADD COLUMN jahrgang INTEGER;")

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

        try:
            from app.services.mail_service import synchronisiere_mail_vorlagen_aus_json
            synchronisiere_mail_vorlagen_aus_json()
        except Exception:
            pass

        # Nachgestellte/führende Leerzeichen beim Vornamen in 'mitglieder' bereinigen
        cursor.execute(
            """
            UPDATE mitglieder
            SET vorname = TRIM(vorname)
            WHERE vorname IS NOT NULL AND vorname != TRIM(vorname);
            """
        )

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

        standard_einstellungen = [
            (
                "footer_text",
                "Notfallkontakt: Lou Hannig\n"
                "lou.hannig@jdav-hildesheim.de\n"
                "© 2026 JDAV Hildesheim. Alle Rechte vorbehalten.",
            ),
            (
                "footer_extern_text",
                "© 2026 JDAV Hildesheim. Alle Rechte vorbehalten.",
            ),
            (
                "anmeldung_intro_text",
                "Bitte gebe hier alle Daten für das anzumeldene Mitglied ein. "
                "Sollten sich in Zukunft Daten ändern, bitte teile dies den Gruppenleitenden mit. "
                "Für eine Teilname an einer Jugendgruppe ist eine Mitgliedschaft im DAV erforderlich.",
            ),
            (
                "anmeldung_beitraege_intro",
                "Neben der Mitgliedschaft im DAV Sektion Hildesheim erfordert die Teilnahme an einer "
                "Jugendgruppe eine monatliche Teilnahmegebühr, welche sich aus einem Betreuungsgeld und "
                "dem Jugendgruppeneintrittsgeld zusammen setzt und quartalsweise eingezogen wird. "
                "Es besteht die Möglichkeit bei den ersten zwei Termine zu schnuppern, ohne das eine "
                "Mitgliedschaft oder ein Beitrag fällig ist. Die Mitgliedschaft einer Jugendgruppe ist "
                "monatlich kündbar, dazu melden Sie sich bei jugend@jdav-hildesheim.de per Email.",
            ),
            (
                "anmeldung_betreuungsgeld_titel",
                "Betreuungsgeld: (gültig ab dem 1.3.2026)",
            ),
            (
                "anmeldung_betreuungsgeld_zeilen",
                "Jugendgruppe 1,5h: 19€\n"
                "Boudergruppe 1h: 16€\n"
                "Mini Gruppe 1h: 19€\n"
                "Inklusive Gruppe 14 tägig: 14€",
            ),
            (
                "anmeldung_eintritt_titel",
                "Jugendgruppeneintrittspreis: (gültig ab dem 1.02.2024)",
            ),
            (
                "anmeldung_eintritt_zeilen",
                "Kinder bis 14 Jahren: 13,50€\n"
                "Jugendliche ab 14 Jahren: 25€\n"
                "Für die Inklusive Gruppe wird kein Eintritt fällig",
            ),
        ]

        standard_tierwoerter = [
            "adler", "affe", "alpaka", "ameise", "baer", "biber", "chamäleon",
            "dachs", "delfin", "dino", "eidechse", "elch", "esel", "eule",
            "falke", "fisch", "fuchs", "gecko", "gemse", "giraffe", "habicht",
            "hamster", "hase", "igel", "iltis", "jaguar", "kamel", "katze",
            "koala", "kraehe", "kranich", "krokodil", "kuckuck", "lama", "luchs",
            "maulwurf", "maus", "murmeltier", "ochse", "otter", "panda", "panther",
            "papagei", "pfau", "pferd", "pinguin", "puma", "qualle", "rabe",
            "reh", "rentier", "robbe", "salamander", "schakal", "schildkroete",
            "schlange", "schwan", "specht", "steinbock", "storch", "tapir",
            "tiger", "tukan", "wal", "wiesel", "wolf", "yak", "zebra",
        ]
        for wort in standard_tierwoerter:
            cursor.execute(
                "INSERT OR IGNORE INTO warteliste_tierwoerter (wort, aktiv) VALUES (?, 1);",
                (wort,),
            )

        warteliste_templates = [
            (
                "warteliste_alt_bestaetigung",
                "Warteliste – 4-Wochen-Bestätigung (Alt-Einträge)",
                "Warteliste Jugendgruppe: Bitte Interesse bestätigen ({{kind_name}})",
                "Hey {{eb_name}},\n\n"
                "wir sind gerade dabei, unsere Warteliste in unser neues digitales Jugendgruppentool umzuziehen.\n\n"
                "Du hast dich am {{datum_einreichung}} bei uns auf die Warteliste gesetzt. "
                "Wenn du weiterhin Interesse hast, bestätige das bitte kurz hier:\n"
                "{{bestaetigungs_url}}\n\n"
                "Wenn wir in den nächsten vier Wochen (bis zum {{frist_datum}}) nichts mehr von dir hören, "
                "gehen wir davon aus, dass du kein Interesse mehr an der Jugendgruppe hast und streichen dich von der Warteliste.\n\n"
                "Wenn du weiter Interesse am Klettern hast, bekommst du im Anschluss einen Zugang zum Wartelistenportal, "
                "um deinen Platz auf der Liste einzusehen.\n\n"
                "Liebe Grüße, dein Team der JDAV Hildesheim",
                None,
            ),
            (
                "warteliste_anmeldebestaetigung",
                "Warteliste – Anmeldebestätigung mit Portal-Zugang",
                "Anmeldebestätigung & Zugang zum Wartelistenportal ({{kind_name}})",
                "Hallo {{eb_name}},\n\n"
                "{{uebernahme_hinweis}}"
                "vielen Dank – der Wartelisteneintrag für {{kind_name}} ist nun bestätigt.\n\n"
                "Zusammenfassung:\n"
                "- Kind: {{kind_name}}\n"
                "- Geburtsdatum: {{geburtsdatum}}\n"
                "- Erfahrung: {{erfahrung}}\n"
                "- Interesse an: {{interesse}}\n"
                "- Separate Warteliste Inklusionsgruppe: {{inklusionsgruppe}}\n"
                "- Kontaktperson: {{eb_name}}\n"
                "- E-Mail: {{eb_email}}\n"
                "- Telefon: {{eb_telefon}}\n"
                "- Eingetragen am: {{erstellt_am}}\n\n"
                "Zugang zum Wartelistenportal:\n"
                "Portal-Link: {{portal_url}}\n"
                "Login mit Name des Kindes: {{kind_name}}\n"
                "Passwort: {{tier_passwort}}\n\n"
                "Viele Grüße\nDein Team der JDAV Hildesheim",
                None,
            ),
            (
                "warteliste_schnupper_einladung",
                "Warteliste – Einladung zum Schnuppern",
                "Schnupper-Einladung Jugendgruppe JDAV Hildesheim ({{kind_name}})",
                "Hallo {{eb_name}},\n\n"
                "für {{kind_name}} ist in unserer Gruppe „{{gruppe_name}}“ ({{wochentag}}, {{startzeit}}–{{endzeit}}) "
                "ein Schnupperplatz frei geworden!\n\n"
                "Bitte melde dich kurz bei uns, ob ihr zum Schnuppern kommen möchtet.\n\n"
                "Viele Grüße\nDein Team der JDAV Hildesheim",
                None,
            ),
        ]
        for tpl in warteliste_templates:
            cursor.execute(
                """
                INSERT OR IGNORE INTO mail_templates (
                    schluessel, bezeichnung, betreff, text_vorlage, html_vorlage
                ) VALUES (?, ?, ?, ?, ?);
                """,
                tpl,
            )

        beitrags_einstellungen = [
            ("anmeldung_betreuungsgeld_gueltig_ab", "01.03.2026"),
            ("anmeldung_eintritt_gueltig_ab", "01.02.2024"),
            ("anmeldung_eintritt_unter_14", "13,50"),
            ("anmeldung_eintritt_ab_14", "25"),
            ("anmeldung_eintritt_hinweis", "Für die Inklusive Gruppe wird kein Eintritt fällig"),
        ]
        for schluessel, wert in beitrags_einstellungen:
            cursor.execute(
                """
                INSERT OR IGNORE INTO app_einstellungen (schluessel, wert)
                VALUES (?, ?);
                """,
                (schluessel, wert),
            )

        vorhandene_arten = cursor.execute("SELECT COUNT(*) FROM gruppenarten").fetchone()[0]
        if vorhandene_arten == 0:
            standard_gruppenarten = [
                ("Jugendgruppe", 90, 8, "19", 1),
                ("Bouldergruppe", 60, 8, "16", 2),
                ("Minis", 60, 6, "19", 3),
                ("Inklusionsgruppe", 90, 4, "14", 4),
            ]
            cursor.executemany(
                """
                INSERT INTO gruppenarten (
                    name, dauer_minuten, betreuungsschluessel, betreuungsgeld_euro, sortierung
                ) VALUES (?, ?, ?, ?, ?);
                """,
                standard_gruppenarten,
            )

        for schluessel, wert in standard_einstellungen:
            cursor.execute(
                """
                INSERT OR IGNORE INTO app_einstellungen (schluessel, wert)
                VALUES (?, ?);
                """,
                (schluessel, wert),
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

    with get_warteliste() as conn:
        _ensure_warteliste_schema(conn)


def _ensure_warteliste_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS warteliste_eintraege (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vorname TEXT NOT NULL,
            nachname TEXT NOT NULL,
            geburtsdatum DATE,
            kontakt_name TEXT,
            email TEXT NOT NULL,
            telefon TEXT,
            interesse TEXT,
            erfahrung TEXT,
            inklusionsgruppe INTEGER NOT NULL DEFAULT 0,
            quelle TEXT NOT NULL DEFAULT 'csv_import',
            erstellt_am DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            bestaetigungs_status TEXT NOT NULL DEFAULT 'ausstehend',
            bestaetigung_token TEXT UNIQUE,
            bestaetigung_gesendet_am DATETIME,
            bestaetigungsfrist_bis DATETIME,
            bestaetigt_am DATETIME,
            aktualisiert_am DATETIME,
            portal_passwort_hash TEXT,
            portal_passwort_hinweis TEXT,
            aus_alter_warteliste_uebernommen INTEGER NOT NULL DEFAULT 0,
            notizen TEXT
        );
        """
    )

    neue_spalten = [
        ("kontakt_vorname", "TEXT"),
        ("kontakt_nachname", "TEXT"),
        ("inklusionsgruppe", "INTEGER NOT NULL DEFAULT 0"),
        ("neuigkeiten_optin", "INTEGER NOT NULL DEFAULT 0"),
        ("bestaetigung_gesendet_am", "DATETIME"),
        ("aktualisiert_am", "DATETIME"),
        ("portal_passwort_hash", "TEXT"),
        ("portal_passwort_hinweis", "TEXT"),
        ("aus_alter_warteliste_uebernommen", "INTEGER NOT NULL DEFAULT 0"),
        ("schnupper_eingeladen_am", "DATETIME"),
        ("schnupper_gruppe_id", "INTEGER"),
    ]
    for spalte, def_sql in neue_spalten:
        if not _column_exists(cursor, "warteliste_eintraege", spalte):
            cursor.execute(f"ALTER TABLE warteliste_eintraege ADD COLUMN {spalte} {def_sql};")

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_warteliste_status_erstellt
        ON warteliste_eintraege(bestaetigungs_status, erstellt_am);
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_warteliste_name_geburtsdatum
        ON warteliste_eintraege(nachname, vorname, geburtsdatum);
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_warteliste_token
        ON warteliste_eintraege(bestaetigung_token);
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS warteliste_blockierte_gruppen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            eintrag_id INTEGER NOT NULL,
            gruppe_id INTEGER NOT NULL,
            erstellt_am DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(eintrag_id, gruppe_id)
        );
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_warteliste_blockiert_eintrag
        ON warteliste_blockierte_gruppen(eintrag_id);
        """
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


@contextmanager
def get_warteliste():
    db_path = Path("app/db/warteliste.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_warteliste_schema(conn)
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