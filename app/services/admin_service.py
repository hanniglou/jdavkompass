from datetime import datetime
import os
import re
from flask import current_app
from flask_login import current_user

from app.models import get_accounts, get_kompass


def _get_app_log_pfad() -> str:
    try:
        root = os.path.dirname(current_app.root_path)
    except RuntimeError:
        root = os.getcwd()
    return os.path.join(root, "logs", "app.log")


def _aktueller_account_name() -> str:
    try:
        if current_user and getattr(current_user, "is_authenticated", False):
            uname = getattr(current_user, "uname", None) or getattr(current_user, "username", None)
            anzeigename = getattr(current_user, "anzeigename", None)
            if anzeigename and uname and anzeigename != uname:
                return f"{anzeigename} ({uname})"
            return uname or anzeigename or f"ID {getattr(current_user, 'id', '?')}"
    except Exception:
        pass
    return "System"


def log_aenderung(aktion: str, details: str = "", account_name: str | None = None) -> None:
    log_pfad = _get_app_log_pfad()
    os.makedirs(os.path.dirname(log_pfad), exist_ok=True)
    zeitstempel = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    acc = (account_name or _aktueller_account_name()).strip()
    nachricht = f"{aktion}: {details}" if details else aktion
    zeile = f"[{zeitstempel}] AUDIT | Account: {acc} | {nachricht}\n"
    with open(log_pfad, "a", encoding="utf-8") as f:
        f.write(zeile)


def hole_aenderungs_log(limit: int | None = None, nur_audit: bool = True) -> list[dict]:
    log_pfad = _get_app_log_pfad()
    if not os.path.exists(log_pfad):
        return []

    audit_pattern = re.compile(
        r"^\[(?P<zeit>[^\]]+)\]\s+AUDIT\s+\|\s+Account:\s+(?P<acc>[^|]+)\|\s+(?P<msg>.+)$"
    )
    std_pattern = re.compile(
        r"^\[?(?P<zeit>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:,\d+)?\]?\s+(?P<rest>.+)$"
    )

    eintraege = []
    with open(log_pfad, "r", encoding="utf-8", errors="replace") as f:
        zeilen = f.readlines()

    for raw in reversed(zeilen):
        zeile = raw.strip()
        if not zeile:
            continue

        m_audit = audit_pattern.match(zeile)
        if m_audit:
            eintraege.append(
                {
                    "zeitpunkt": m_audit.group("zeit").strip(),
                    "account": m_audit.group("acc").strip(),
                    "beschreibung": m_audit.group("msg").strip(),
                    "ist_audit": True,
                }
            )
        elif not nur_audit:
            m_std = std_pattern.match(zeile)
            if m_std:
                eintraege.append(
                    {
                        "zeitpunkt": m_std.group("zeit").strip(),
                        "account": "System",
                        "beschreibung": m_std.group("rest").strip(),
                        "ist_audit": False,
                    }
                )
            else:
                eintraege.append(
                    {
                        "zeitpunkt": "-",
                        "account": "System",
                        "beschreibung": zeile,
                        "ist_audit": False,
                    }
                )

        if limit and len(eintraege) >= limit:
            break

    return eintraege


def save_gruppenleiter(data, gruppen_leiter_id):
    with get_kompass() as conn:
        cursor = conn.cursor()
        if gruppen_leiter_id:
            cursor.execute(
                """
                UPDATE gruppenleiter
                SET
                    vorname = ?,
                    nachname = ?,
                    geburtsdatum = ?,
                    iban = ?,
                    bic = ?,
                    bank = ?,
                    telefon = ?,
                    gruppenrolle = ?,
                    vereinsrolle = ?
                WHERE
                    id = ?
                """,
                (
                    data["vorname"],
                    data["nachname"],
                    data["geburtsdatum"],
                    data["iban"],
                    data["bic"],
                    data["bank"],
                    data["telefon"],
                    data["gruppenrolle"],
                    data["vereinsrolle"],
                    gruppen_leiter_id,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO
                    gruppenleiter (
                    vorname,
                    nachname,
                    geburtsdatum,
                    iban,
                    bic,
                    bank,
                    telefon,
                    gruppenrolle,
                    vereinsrolle
                    )
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["vorname"],
                    data["nachname"],
                    data["geburtsdatum"],
                    data["iban"],
                    data["bic"],
                    data["bank"],
                    data["telefon"],
                    data["gruppenrolle"],
                    data["vereinsrolle"],
                ),
            )


def update_account_status(account_id, status):
    with get_accounts() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE accounts
            SET
                status = ?
            WHERE
                id = ?
            AND role != '4'
            """,
            (status, account_id),
        )


def delete_account(account_id):
    with get_accounts() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM accounts
            WHERE
                id = ?
                AND role != '4'
            """,
            (account_id,),
        )


def change_account_role(account_id, new_role):
    with get_accounts() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE accounts
            SET
                role = ?
            WHERE
                id = ?
                AND role != '4'
            """,
            (new_role, account_id),
        )


def get_all_accounts():
    with get_accounts() as conn:
        cursor = conn.cursor()
        accounts = cursor.execute(
            """
            SELECT
                id,
                uname,
                anzeigename,
                status,
                role
            FROM
                accounts
            ORDER BY
                id
            """
        ).fetchall()
        return accounts


def get_gruppenleiter(gruppenleiter_id):
    with get_kompass() as conn:
        cursor = conn.cursor()
        gruppenleiter = cursor.execute(
            """
            SELECT
                *
            FROM
                gruppenleiter
            WHERE
                id = ?
            """,
            (gruppenleiter_id,),
        ).fetchone()
        return gruppenleiter


def get_all_gruppenleiter():
    with get_kompass() as conn:
        cursor = conn.cursor()

        gruppenleiter = cursor.execute(
            """
            SELECT
                gl.id,
                gl.vorname,
                gl.nachname,
                gl.geburtsdatum,
                gl.iban,
                gl.bic,
                gl.bank,
                gl.telefon,
                gl.gruppenrolle,
                gl.vereinsrolle,
                GROUP_CONCAT (jg.name, ', ') AS jugendgruppen
            FROM
                gruppenleiter gl
                LEFT JOIN gruppenleiter_jugendgruppen gj ON gl.id = gj.gruppenleiter_id
                LEFT JOIN jugendgruppen jg ON gj.jugendgruppe_id = jg.id
            GROUP BY
                gl.id
            ORDER BY
                gl.nachname
            """
        ).fetchall()

        return gruppenleiter

def speichere_einstellungen(werte: dict[str, str]) -> None:
    with get_kompass() as conn:
        for schluessel, rohwert in werte.items():
            default_wert = DEFAULT_EINSTELLUNGEN.get(schluessel, "")
            bereinigt = (rohwert or "").replace("\r\n", "\n").strip()
            if not bereinigt and default_wert:
                bereinigt = default_wert
            conn.execute(
                """
                INSERT INTO app_einstellungen (schluessel, wert)
                VALUES (?, ?)
                ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert
                """,
                (schluessel, bereinigt),
            )

DEFAULT_EINSTELLUNGEN = {
    "mail_versand_aktiv": "0",
    "footer_text": (
        "Notfallkontakt: Lou Hannig\n"
        "lou.hannig@jdav-hildesheim.de\n"
        "© 2026 JDAV Hildesheim. Alle Rechte vorbehalten."
    ),
    "footer_extern_text": "© 2026 JDAV Hildesheim. Alle Rechte vorbehalten.",
    "anmeldung_intro_text": (
        "Bitte gebe hier alle Daten für das anzumeldene Mitglied ein. "
        "Sollten sich in Zukunft Daten ändern, bitte teile dies den Gruppenleitenden mit. "
        "Für eine Teilname an einer Jugendgruppe ist eine Mitgliedschaft im DAV erforderlich."
    ),
    "anmeldung_beitraege_intro": (
        "Neben der Mitgliedschaft im DAV Sektion Hildesheim erfordert die Teilnahme an einer "
        "Jugendgruppe eine monatliche Teilnahmegebühr, welche sich aus einem Betreuungsgeld und "
        "dem Jugendgruppeneintrittsgeld zusammen setzt und quartalsweise eingezogen wird. "
        "Es besteht die Möglichkeit bei den ersten zwei Termine zu schnuppern, ohne das eine "
        "Mitgliedschaft oder ein Beitrag fällig ist. Die Mitgliedschaft einer Jugendgruppe ist "
        "monatlich kündbar, dazu melden Sie sich bei jugend@jdav-hildesheim.de per Email."
    ),
    "anmeldung_beitraege_details": (
        "Betreuungsgeld: (gültig ab dem 1.3.2026)\n"
        "Jugendgruppe 1,5h: 19€\n"
        "Boudergruppe 1h: 16€\n"
        "Mini Gruppe 1h: 19€\n"
        "Inklusive Gruppe 14 tägig: 14€\n\n"
        "Jugendgruppeneintrittspreis: (gültig ab dem 1.02.2024)\n"
        "Kinder bis 14 Jahren: 13,50€\n"
        "Jugendliche ab 14 Jahren: 25€\n"
        "Für die Inklusive Gruppe wird kein Eintritt fällig"
    ),
    "anmeldung_betreuungsgeld_titel": "Betreuungsgeld: (gültig ab dem 1.3.2026)",
        "anmeldung_betreuungsgeld_zeilen": (
            "Jugendgruppe 1,5h: 19€\n"
            "Boudergruppe 1h: 16€\n"
            "Mini Gruppe 1h: 19€\n"
            "Inklusive Gruppe 14 tägig: 14€"
        ),
        "anmeldung_eintritt_titel": "Jugendgruppeneintrittspreis: (gültig ab dem 1.02.2024)",
        "anmeldung_eintritt_zeilen": (
            "Kinder bis 14 Jahren: 13,50€\n"
            "Jugendliche ab 14 Jahren: 25€\n"
            "Für die Inklusive Gruppe wird kein Eintritt fällig"
        ),
    "anmeldung_betreuungsgeld_gueltig_ab": "01.03.2026",
    "anmeldung_eintritt_gueltig_ab": "01.02.2024",
    "anmeldung_eintritt_unter_14": "13,50",
    "anmeldung_eintritt_ab_14": "25",
    "anmeldung_eintritt_hinweis": "Für die Inklusive Gruppe wird kein Eintritt fällig",
}


def hole_einstellungen() -> dict[str, str]:
    daten = dict(DEFAULT_EINSTELLUNGEN)
    try:
        with get_kompass() as conn:
            rows = conn.execute(
                "SELECT schluessel, wert FROM app_einstellungen"
            ).fetchall()
            for row in rows:
                if row["schluessel"] in daten and row["wert"] is not None:
                    daten[row["schluessel"]] = row["wert"]
    except Exception:
        pass
    return daten


def speichere_einstellungen(werte: dict[str, str]) -> None:
    with get_kompass() as conn:
        for schluessel, default_wert in DEFAULT_EINSTELLUNGEN.items():
            if schluessel not in werte:
                continue
            bereinigt = (werte[schluessel] or "").replace("\r\n", "\n").strip() or default_wert
            conn.execute(
                """
                INSERT INTO app_einstellungen (schluessel, wert)
                VALUES (?, ?)
                ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert
                """,
                (schluessel, bereinigt),
            )


BEITRAG_SCHLUESSEL = (
    "anmeldung_betreuungsgeld_titel",
    "anmeldung_betreuungsgeld_zeilen",
    "anmeldung_eintritt_titel",
    "anmeldung_eintritt_zeilen",
)


def speichere_gruppenarten_und_beitraege(werte: dict[str, str]) -> None:
    gefiltert = {k: v for k, v in werte.items() if k in BEITRAG_SCHLUESSEL}
    speichere_einstellungen(gefiltert)


def hole_footer_text() -> str:
    return hole_einstellungen()["footer_text"]


def speichere_footer_text(neuer_text: str) -> None:
    speichere_einstellungen({"footer_text": neuer_text})


def hole_gruppenarten() -> list[dict]:
    try:
        with get_kompass() as conn:
            rows = conn.execute(
                """
                SELECT id, name, dauer_minuten, betreuungsschluessel, betreuungsgeld_euro, sortierung
                FROM gruppenarten
                ORDER BY sortierung ASC, id ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def speichere_gruppenart(
    name: str,
    dauer_minuten: int,
    betreuungsschluessel: int,
    betreuungsgeld_euro: str,
    gruppenart_id: int | None = None,
) -> None:
    sauber_name = (name or "").strip()
    sauber_geld = (betreuungsgeld_euro or "").strip().replace("€", "").strip()
    if not sauber_name or not sauber_geld:
        raise ValueError("Bitte Gruppenart und Betreuungsgeld angeben.")
    if dauer_minuten <= 0 or betreuungsschluessel <= 0:
        raise ValueError("Dauer und Betreuungsschlüssel müssen größer als 0 sein.")

    with get_kompass() as conn:
        if gruppenart_id:
            conn.execute(
                """
                UPDATE gruppenarten
                SET name = ?, dauer_minuten = ?, betreuungsschluessel = ?, betreuungsgeld_euro = ?
                WHERE id = ?
                """,
                (sauber_name, dauer_minuten, betreuungsschluessel, sauber_geld, gruppenart_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO gruppenarten (name, dauer_minuten, betreuungsschluessel, betreuungsgeld_euro)
                VALUES (?, ?, ?, ?)
                """,
                (sauber_name, dauer_minuten, betreuungsschluessel, sauber_geld),
            )


def loesche_gruppenart(gruppenart_id: int) -> None:
    with get_kompass() as conn:
        conn.execute("DELETE FROM gruppenarten WHERE id = ?", (gruppenart_id,))


def speichere_beitrags_rahmen(
    betreuungsgeld_gueltig_ab: str,
    eintritt_gueltig_ab: str,
    eintritt_unter_14: str,
    eintritt_ab_14: str,
    eintritt_hinweis: str,
) -> None:
    speichere_einstellungen(
        {
            "anmeldung_betreuungsgeld_gueltig_ab": (betreuungsgeld_gueltig_ab or "").strip(),
            "anmeldung_eintritt_gueltig_ab": (eintritt_gueltig_ab or "").strip(),
            "anmeldung_eintritt_unter_14": (eintritt_unter_14 or "").replace("€", "").strip(),
            "anmeldung_eintritt_ab_14": (eintritt_ab_14 or "").replace("€", "").strip(),
            "anmeldung_eintritt_hinweis": (eintritt_hinweis or "").strip(),
        }
    )