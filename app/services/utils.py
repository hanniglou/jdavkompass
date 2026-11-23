from app.models import get_kompass
import datetime


def heute():
    today = [
        "Montag",
        "Dienstag",
        "Mittwoch",
        "Donnerstag",
        "Freitag",
        "Samstag",
        "Sonntag",
    ][datetime.datetime.today().weekday()]
    return today


def jugendgruppen_preview(account_id=None):
    result = []
    parsed_account_id = None
    if account_id is not None and str(account_id).strip() != "":
        try:
            parsed_account_id = int(account_id)
        except (TypeError, ValueError):
            parsed_account_id = None

    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                id,
                name,
                wochentag,
                startzeit,
                endzeit,
                gruppenart
            FROM
                jugendgruppen
            ORDER BY
                CASE wochentag
                    WHEN 'Montag' THEN 1
                    WHEN 'Dienstag' THEN 2
                    WHEN 'Mittwoch' THEN 3
                    WHEN 'Donnerstag' THEN 4
                    WHEN 'Freitag' THEN 5
                    WHEN 'Samstag' THEN 6
                    WHEN 'Sonntag' THEN 7
                END,
                startzeit ASC,
                name COLLATE NOCASE ASC
            """
        )
        gruppen = cursor.fetchall()
        for gruppe in gruppen:
            gruppe_id, name, wochentag, startzeit, endzeit, gruppenart = gruppe

            cursor.execute(
                """
                SELECT
                    g.vorname || ' ' || g.nachname,
                    g.vorname,
                    g.account_id
                FROM
                    gruppenleiter g
                    JOIN gruppenleiter_jugendgruppen gj ON g.id = gj.gruppenleiter_id
                WHERE
                    gj.jugendgruppe_id = ?
                ORDER BY
                    g.nachname COLLATE NOCASE ASC,
                    g.vorname COLLATE NOCASE ASC
                """,
                (gruppe_id,),
            )
            leiter_rows = cursor.fetchall()
            gruppenleiter = [row[0] for row in leiter_rows]
            gruppenleiter_vornamen = [row[1] for row in leiter_rows if row[1]]
            ist_meine_gruppe = (
                parsed_account_id is not None
                and any(row[2] == parsed_account_id for row in leiter_rows)
            )

            cursor.execute(
                """
                SELECT
                    m.vorname || ' ' || m.nachname,
                    m.vorname
                FROM
                    mitglieder m
                    JOIN mitglied_jugendgruppen mj ON m.id = mj.mitglied_id
                WHERE
                    mj.jugendgruppe_id = ?
                ORDER BY
                    m.nachname COLLATE NOCASE ASC,
                    m.vorname COLLATE NOCASE ASC
                """,
                (gruppe_id,),
            )
            mitglieder_rows = cursor.fetchall()
            mitglieder = [row[0] for row in mitglieder_rows]
            mitglieder_vornamen = [row[1] for row in mitglieder_rows if row[1]]

            gruppe_dict = {
                "id": gruppe_id,
                "name": name,
                "wochentag": wochentag,
                "startzeit": startzeit,
                "endzeit": endzeit,
                "gruppenart": gruppenart,
                "gruppenleiter": gruppenleiter,
                "gruppenleiter_vornamen": gruppenleiter_vornamen,
                "mitglieder": mitglieder,
                "mitglieder_vornamen": mitglieder_vornamen,
                "ist_meine_gruppe": ist_meine_gruppe,
            }

            result.append(gruppe_dict)

    return result