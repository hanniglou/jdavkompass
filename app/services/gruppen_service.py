from app.models import get_kompass


def _parse_int_or_none(raw_val):
    if raw_val is None:
        return None
    cleaned = str(raw_val).strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except (TypeError, ValueError):
        return None


def neue_gruppe_erstellen(data):
    name = (data.get("name") or "").strip()
    beschreibung = (data.get("beschreibung") or "").strip()
    wochentag = (data.get("wochentag") or "").strip()
    startzeit = (data.get("startzeit") or "").strip()
    endzeit = (data.get("endzeit") or "").strip()
    gruppenart = (data.get("gruppenart") or "").strip() or None
    dauer = _parse_int_or_none(data.get("dauer"))

    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO
                jugendgruppen (name, beschreibung, wochentag, startzeit, endzeit, dauer, gruppenart)
            VALUES
                (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                beschreibung,
                wochentag,
                startzeit,
                endzeit,
                dauer,
                gruppenart,
            ),
        )


def loesche_gruppe(gruppe_id):
    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM anwesenheit
            WHERE
                gruppe_id = ?
            """,
            (gruppe_id,),
        )
        cursor.execute(
            """
            DELETE FROM anwesenheit_leiter
            WHERE
                gruppe_id = ?
            """,
            (gruppe_id,),
        )
        cursor.execute(
            """
            DELETE FROM mitglied_jugendgruppen
            WHERE
                jugendgruppe_id = ?
            """,
            (gruppe_id,),
        )
        cursor.execute(
            """
            DELETE FROM gruppenleiter_jugendgruppen
            WHERE
                jugendgruppe_id = ?
            """,
            (gruppe_id,),
        )
        cursor.execute("DELETE FROM jugendgruppen WHERE id = ?", (gruppe_id,))