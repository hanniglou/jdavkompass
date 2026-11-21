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
            (gruppenleiter_id,)
        ).fetchone()
        return gruppenleiter


def get_all_gruppenleiter():
    with get_kompass() as conn:
        cursor = conn.cursor()

        gruppenleiter = cursor.execute("""
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
            """).fetchall()

        return gruppenleiter