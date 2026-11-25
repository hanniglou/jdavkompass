from datetime import date, datetime
from io import BytesIO

from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
import pandas as pd

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


def _parse_iso_datum(wert):
    if not wert:
        return None
    text = str(wert).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _formatiere_kuendigung_datum_de(wert):
    dt_val = _parse_iso_datum(wert)
    if dt_val:
        return dt_val.strftime("%d.%m.%Y")
    return str(wert).strip()[:10] if wert else None


def _quartal_index(d: date) -> int:
    return d.year * 4 + ((d.month - 1) // 3)


def _quartalsende_datum(d: date) -> date:
    quartal_ende_monat = ((d.month - 1) // 3 + 1) * 3
    tag = 31 if quartal_ende_monat in (3, 12) else 30
    return date(d.year, quartal_ende_monat, tag)


def _ensure_hinweis_tabellen(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS kuendigung_folgequartal_hinweise (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gruppe_id INTEGER NOT NULL,
            mitglied_id INTEGER NOT NULL,
            mitglied_name TEXT NOT NULL,
            kuendigung_datum TEXT NOT NULL,
            UNIQUE(gruppe_id, mitglied_id, kuendigung_datum)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS gruppenwechsel_hinweise (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quell_gruppe_id INTEGER NOT NULL,
            ziel_gruppe_name TEXT NOT NULL,
            mitglied_id INTEGER NOT NULL,
            mitglied_name TEXT NOT NULL,
            wechsel_datum TEXT NOT NULL,
            erstellt_am TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def gruppenwechsel_hinweis_erfassen(
    quell_gruppe_id: int,
    ziel_gruppe_name: str,
    mitglied_id: int,
    mitglied_name: str,
    wechsel_datum: str | date,
):
    datum_obj = wechsel_datum if isinstance(wechsel_datum, date) else _parse_iso_datum(wechsel_datum)
    datum_iso = (datum_obj or date.today()).isoformat()
    with get_kompass() as conn:
        cursor = conn.cursor()
        _ensure_hinweis_tabellen(cursor)
        cursor.execute(
            """
            INSERT INTO gruppenwechsel_hinweise (
                quell_gruppe_id, ziel_gruppe_name, mitglied_id, mitglied_name, wechsel_datum
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (quell_gruppe_id, ziel_gruppe_name, mitglied_id, mitglied_name, datum_iso),
        )
        conn.commit()


def bereinige_und_hole_gruppen_hinweise(gruppe_id: int, referenz_datum: date | None = None):
    """
    - Entfernt Mitglieder aus der Gruppe, deren Kündigungsquartal bereits vorbei ist.
    - Speichert und liefert Amber-Hinweise für das gesamte Folgequartal nach dem Kündigungsquartal (ref_q == k_q + 1).
    - Liefert Hellblau-Hinweise für Gruppenwechsel im laufenden Quartal der Quellgruppe.
    """
    ref = referenz_datum or date.today()
    ref_q = _quartal_index(ref)
    amber_hinweise = []
    blau_hinweise = []
    entfernte_namen = []
    geaendert = False

    with get_kompass() as conn:
        cursor = conn.cursor()
        _ensure_hinweis_tabellen(cursor)

        cursor.execute(
            """
            SELECT
                m.id,
                m.vorname,
                m.nachname,
                m.kuendigung_datum
            FROM
                mitglieder m
                JOIN mitglied_jugendgruppen mj ON m.id = mj.mitglied_id
            WHERE
                mj.jugendgruppe_id = ?
                AND m.kuendigung_datum IS NOT NULL
                AND TRIM(m.kuendigung_datum) != ''
            """,
            (gruppe_id,),
        )
        rows = cursor.fetchall()

        zu_entfernen = []
        for row in rows:
            k_datum = _parse_iso_datum(row["kuendigung_datum"])
            if not k_datum:
                continue
            k_q = _quartal_index(k_datum)
            if ref_q > k_q:
                zu_entfernen.append((row, k_datum, k_q))

        for row, k_datum, k_q in zu_entfernen:
            voller_name = f"{row['vorname']} {row['nachname']}".strip()
            entfernte_namen.append(voller_name)
            cursor.execute(
                """
                DELETE FROM mitglied_jugendgruppen
                WHERE jugendgruppe_id = ? AND mitglied_id = ?
                """,
                (gruppe_id, row["id"]),
            )
            geaendert = True
            if ref_q == k_q + 1:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO kuendigung_folgequartal_hinweise (
                        gruppe_id, mitglied_id, mitglied_name, kuendigung_datum
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (gruppe_id, row["id"], voller_name, k_datum.isoformat()),
                )

        k_hinweis_rows = cursor.execute(
            """
            SELECT id, mitglied_id, mitglied_name, kuendigung_datum
            FROM kuendigung_folgequartal_hinweise
            WHERE gruppe_id = ?
            ORDER BY kuendigung_datum ASC, id ASC
            """,
            (gruppe_id,),
        ).fetchall()

        abgelaufene_k_ids = []
        for kh in k_hinweis_rows:
            k_datum = _parse_iso_datum(kh["kuendigung_datum"])
            if not k_datum:
                continue
            k_q = _quartal_index(k_datum)
            if ref_q == k_q + 1:
                amber_hinweise.append(
                    {
                        "mitglied_id": kh["mitglied_id"],
                        "name": kh["mitglied_name"],
                        "kuendigung_datum_iso": k_datum.isoformat(),
                        "kuendigung_datum_de": k_datum.strftime("%d.%m.%Y"),
                    }
                )
            elif ref_q > k_q + 1:
                abgelaufene_k_ids.append(kh["id"])

        if abgelaufene_k_ids:
            ph = ",".join("?" for _ in abgelaufene_k_ids)
            cursor.execute(
                f"DELETE FROM kuendigung_folgequartal_hinweise WHERE id IN ({ph})",
                abgelaufene_k_ids,
            )
            geaendert = True

        wechsel_rows = cursor.execute(
            """
            SELECT id, ziel_gruppe_name, mitglied_id, mitglied_name, wechsel_datum
            FROM gruppenwechsel_hinweise
            WHERE quell_gruppe_id = ?
            ORDER BY wechsel_datum ASC, id ASC
            """,
            (gruppe_id,),
        ).fetchall()

        abgelaufene_wechsel_ids = []
        for w in wechsel_rows:
            w_datum = _parse_iso_datum(w["wechsel_datum"])
            if not w_datum:
                continue
            if _quartal_index(w_datum) == ref_q:
                blau_hinweise.append(
                    {
                        "mitglied_id": w["mitglied_id"],
                        "name": w["mitglied_name"],
                        "ziel_gruppe": w["ziel_gruppe_name"],
                        "datum_iso": w_datum.isoformat(),
                        "datum_de": w_datum.strftime("%d.%m.%Y"),
                    }
                )
            elif _quartal_index(w_datum) < ref_q:
                abgelaufene_wechsel_ids.append(w["id"])

        if abgelaufene_wechsel_ids:
            ph = ",".join("?" for _ in abgelaufene_wechsel_ids)
            cursor.execute(
                f"DELETE FROM gruppenwechsel_hinweise WHERE id IN ({ph})",
                abgelaufene_wechsel_ids,
            )
            geaendert = True

        if geaendert:
            conn.commit()

    return entfernte_namen, amber_hinweise, blau_hinweise


def hole_teilnehmende_und_quartalsende(gruppe_id: int, referenz_datum: date | None = None):
    ref = referenz_datum or date.today()
    ref_q = _quartal_index(ref)
    quartalsende = _quartalsende_datum(ref)

    mitglieder = get_mitglieder(gruppe_id)
    for m in mitglieder:
        m["verschoben_ab_de"] = None

    vorhandene_ids = {m["id"] for m in mitglieder}

    with get_kompass() as conn:
        cursor = conn.cursor()
        _ensure_hinweis_tabellen(cursor)

        wechsel_rows = cursor.execute(
            """
            SELECT id, mitglied_id, wechsel_datum
            FROM gruppenwechsel_hinweise
            WHERE quell_gruppe_id = ?
            ORDER BY wechsel_datum ASC, id ASC
            """,
            (gruppe_id,),
        ).fetchall()

        zukunft_wechsel_nach_id = {}
        for w in wechsel_rows:
            w_datum = _parse_iso_datum(w["wechsel_datum"])
            if not w_datum or _quartal_index(w_datum) != ref_q:
                continue
            if w_datum > ref:
                zukunft_wechsel_nach_id[w["mitglied_id"]] = w_datum.strftime("%d.%m.%Y")

        if zukunft_wechsel_nach_id:
            fehlend_ids = [mid for mid in zukunft_wechsel_nach_id if mid not in vorhandene_ids]
            if fehlend_ids:
                ph = ",".join("?" for _ in fehlend_ids)
                extra_rows = cursor.execute(
                    f"""
                    SELECT
                        m.id,
                        m.vorname,
                        m.nachname,
                        m.geburtsdatum,
                        m.kuendigung_datum,
                        COALESCE(m.archiviert, 0) AS archiviert,
                        e.telefon
                    FROM
                        mitglieder m
                        LEFT JOIN mitglied_erziehungsberechtigte me ON m.id = me.mitglied_id
                        LEFT JOIN erziehungsberechtigte e ON me.erziehungsberechtigter_id = e.id
                    WHERE
                        m.id IN ({ph})
                    """,
                    fehlend_ids,
                ).fetchall()
                for row in extra_rows:
                    m = dict(row)
                    m["kuendigung_datum_de"] = _formatiere_kuendigung_datum_de(m.get("kuendigung_datum"))
                    m["verschoben_ab_de"] = zukunft_wechsel_nach_id.get(m["id"])
                    mitglieder.append(m)

            for m in mitglieder:
                if m["id"] in zukunft_wechsel_nach_id:
                    m["verschoben_ab_de"] = zukunft_wechsel_nach_id[m["id"]]

    anzahl_aktuell = len(mitglieder)
    anzahl_quartalsende = 0
    for m in mitglieder:
        k_datum = _parse_iso_datum(m.get("kuendigung_datum"))
        if k_datum is not None and k_datum <= quartalsende:
            continue

        wechsel_ab_de = m.get("verschoben_ab_de")
        if wechsel_ab_de:
            try:
                w_datum = datetime.strptime(wechsel_ab_de, "%d.%m.%Y").date()
                if w_datum <= quartalsende:
                    continue
            except ValueError:
                pass

        anzahl_quartalsende += 1

    return mitglieder, anzahl_aktuell, anzahl_quartalsende

def empfehle_gruppen_jahrgang(gruppe_id: int, gruppenart: str | None = None) -> dict:
    """Berechnet anhand aktiver (nicht archivierter) Mitglieder eine Jahrgangs-Empfehlung."""
    art_norm = (gruppenart or "").strip().lower()
    if "inklusion" in art_norm:
        return {
            "empfohlen_jahrgang": None,
            "hinweis": "Inklusionsgruppe: Kein Ziel-Jahrgang erforderlich (eigene Warteliste).",
        }

    with get_kompass() as conn:
        rows = conn.execute(
            """
            SELECT m.geburtsdatum
            FROM mitglieder m
            JOIN mitglied_jugendgruppen mg ON m.id = mg.mitglied_id
            WHERE mg.jugendgruppe_id = ?
              AND COALESCE(m.archiviert, 0) = 0
              AND m.geburtsdatum IS NOT NULL
              AND TRIM(m.geburtsdatum) != ''
            """,
            (gruppe_id,),
        ).fetchall()

    jahre: list[int] = []
    for r in rows:
        raw = str(r["geburtsdatum"] or "").strip()
        if len(raw) >= 4 and raw[:4].isdigit():
            jahre.append(int(raw[:4]))

    if not jahre:
        return {
            "empfohlen_jahrgang": None,
            "hinweis": "Noch keine aktiven Mitglieder mit Geburtsdatum in dieser Gruppe – Jahrgang manuell festlegen.",
        }

    sortiert = sorted(jahre)
    n = len(sortiert)
    if n % 2 == 1:
        median_jahr = sortiert[n // 2]
    else:
        median_jahr = round((sortiert[n // 2 - 1] + sortiert[n // 2]) / 2)

    return {
        "empfohlen_jahrgang": median_jahr,
        "hinweis": (
            f"Empfehlung aus {n} aktiven TN: Jahrgang {median_jahr} "
            f"(Spanne in Gruppe: {min(sortiert)}–{max(sortiert)})."
        ),
    }


def mitglied_zu_gruppe_hinzufuegen(gruppe_id, mitglied_id):
    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT
            OR IGNORE INTO mitglied_jugendgruppen (mitglied_id, jugendgruppe_id)
            VALUES
                (?, ?)
            """,
            (mitglied_id, gruppe_id),
        )


def gruppenleiter_zu_gruppe_hinzufuegen(gruppe_id, leiter_id):
    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT
            OR IGNORE INTO gruppenleiter_jugendgruppen (gruppenleiter_id, jugendgruppe_id)
            VALUES
                (?, ?)
            """,
            (leiter_id, gruppe_id),
        )


def mitglied_aus_gruppe_entfernen(gruppe_id, mitglied_id):
    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM mitglied_jugendgruppen
            WHERE
                jugendgruppe_id = ?
                AND mitglied_id = ?
            """,
            (gruppe_id, mitglied_id),
        )


def gruppenleiter_aus_gruppe_entfernen(gruppe_id, leiter_id):
    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            DELETE FROM gruppenleiter_jugendgruppen
            WHERE
                jugendgruppe_id = ?
                AND gruppenleiter_id = ?
            """,
            (gruppe_id, leiter_id),
        )


def search_person(search_in, search_query):
    with get_kompass() as conn:
        cursor = conn.cursor()
        query = f"""
            SELECT
                id,
                vorname,
                nachname
            FROM
                {search_in}
            WHERE
                vorname LIKE ?
                OR nachname LIKE ?
            """
        cursor.execute(query, ("%" + search_query + "%", "%" + search_query + "%"))
        search_results = cursor.fetchall()

    return search_results


def get_gruppe(gruppe_id):
    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                id,
                name,
                wochentag,
                startzeit,
                endzeit
            FROM
                jugendgruppen
            WHERE
                id = ?
            """,
            (gruppe_id,),
        )
        gruppe = cursor.fetchone()

    return gruppe


def get_mitglieder(gruppe_id):
    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                m.id,
                m.vorname,
                m.nachname,
                m.geburtsdatum,
                m.kuendigung_datum,
                COALESCE(m.archiviert, 0) AS archiviert,
                e.telefon
            FROM
                mitglieder m
                LEFT JOIN mitglied_erziehungsberechtigte me ON m.id = me.mitglied_id
                LEFT JOIN erziehungsberechtigte e ON me.erziehungsberechtigter_id = e.id
            WHERE
                m.id IN (
                    SELECT
                        mitglied_id
                    FROM
                        mitglied_jugendgruppen
                    WHERE
                        jugendgruppe_id = ?
                )
            """,
            (gruppe_id,),
        )
        rows = cursor.fetchall()

    mitglieder = []
    for row in rows:
        m = dict(row)
        m["kuendigung_datum_de"] = _formatiere_kuendigung_datum_de(m.get("kuendigung_datum"))
        mitglieder.append(m)

    return mitglieder


def get_gruppenleiter(gruppe_id):
    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                g.id,
                g.vorname,
                g.nachname,
                g.telefon
            FROM
                gruppenleiter g
                JOIN gruppenleiter_jugendgruppen gj ON g.id = gj.gruppenleiter_id
            WHERE
                gj.jugendgruppe_id = ?
            """,
            (gruppe_id,),
        )
        gruppenleiter = cursor.fetchall()

    return gruppenleiter


def get_query_vars(person_typ):
    if person_typ == "gruppenleiter":
        table = "anwesenheit_leiter"
        query_id = "gruppenleiter_id"
    elif person_typ == "mitglied":
        table = "anwesenheit"
        query_id = "mitglied_id"
    return table, query_id


def get_anwesenheit(gruppe_id, person_typ):
    anwesenheitshistorie = {}
    alle_daten = []
    table, query_id = get_query_vars(person_typ)

    query = f"""
            SELECT
                {query_id},
                datum,
                anwesend
            FROM
                {table}
            WHERE
                gruppe_id = ?
            ORDER BY
                datum
            """

    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            query,
            (gruppe_id,),
        )
        for row in cursor.fetchall():
            person_id, datum, anwesend = (
                row[query_id],
                row["datum"],
                row["anwesend"],
            )
            if person_id not in anwesenheitshistorie:
                anwesenheitshistorie[person_id] = {}
            anwesenheitshistorie[person_id][datum] = anwesend
            alle_daten.append(datum)
    return alle_daten, anwesenheitshistorie


def update_anwesenheit(gruppe_id, person_typ, person_list, request, daten):
    table, query_id = get_query_vars(person_typ)

    query = f"""
            INSERT
            OR REPLACE INTO {table} ({query_id}, gruppe_id, datum, anwesend)
            VALUES
                (?, ?, ?, ?)
            """
    with get_kompass() as conn:
        cursor = conn.cursor()

        for person in person_list:
            for datum in daten:
                field = f"{person_typ}_{person['id']}_{datum}"
                if person_typ == "mitglied":
                    raw_val = request.form.get(field, "0")
                    anwesend = int(raw_val) if raw_val in {"0", "1", "2"} else 0
                else:
                    anwesend = 1 if field in request.form else 0
                cursor.execute(query, (person["id"], gruppe_id, datum, anwesend))


def new_day(gruppe_id, datum):
    with get_kompass() as conn:
        cursor = conn.cursor()
        mitglieder = get_mitglieder(gruppe_id=gruppe_id)
        gruppenleiter = get_gruppenleiter(gruppe_id=gruppe_id)

        for mitglied in mitglieder:
            cursor.execute(
                """
                INSERT OR IGNORE
                INTO 
                    anwesenheit
                    (mitglied_id, gruppe_id, datum, anwesend)
                VALUES 
                    (?, ?, ?, 0)
                """,
                (mitglied["id"], gruppe_id, datum),
            )

        for leiter in gruppenleiter:
            cursor.execute(
                """
                INSERT OR IGNORE
                INTO 
                    anwesenheit_leiter
                    (gruppenleiter_id, gruppe_id, datum, anwesend)
                VALUES 
                    (?, ?, ?, 0)
                """,
                (leiter["id"], gruppe_id, datum),
            )


def get_vertretungs_kandidaten(feste_leiter):
    """Gibt alle Jugendleitenden zurück, die NICHT fest dieser Gruppe zugeordnet sind."""
    feste_ids = {l["id"] for l in feste_leiter}
    query = """
        SELECT id, vorname, nachname
        FROM gruppenleiter
        ORDER BY vorname, nachname
    """
    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        return [
            {"id": r[0], "vorname": r[1], "nachname": r[2]}
            for r in rows
            if r[0] not in feste_ids
        ]


def get_vertretungen_by_date(gruppe_id, feste_leiter):
    """Gibt ein Dict {datum: [leiter_id_1, leiter_id_2]} für anwesende Vertretungen zurück."""
    feste_ids = {l["id"] for l in feste_leiter}
    query = """
        SELECT datum, gruppenleiter_id
        FROM anwesenheit_leiter
        WHERE gruppe_id = ? AND anwesend = 1
        ORDER BY gruppenleiter_id
    """
    vertretungen = {}
    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(query, (gruppe_id,))
        for datum, gl_id in cursor.fetchall():
            if gl_id not in feste_ids:
                vertretungen.setdefault(datum, []).append(gl_id)
    return vertretungen


def update_vertretungen(gruppe_id, feste_leiter, request, daten):
    """Speichert die ausgewählten Vertretungen (Slot 1 & 2) pro Datum in anwesenheit_leiter."""
    feste_ids = {l["id"] for l in feste_leiter}
    with get_kompass() as conn:
        cursor = conn.cursor()
        for datum in daten:
            if feste_ids:
                placeholders = ",".join("?" for _ in feste_ids)
                cursor.execute(
                    f"""
                    DELETE FROM anwesenheit_leiter
                    WHERE gruppe_id = ? AND datum = ?
                      AND gruppenleiter_id NOT IN ({placeholders})
                    """,
                    (gruppe_id, datum, *feste_ids),
                )
            else:
                cursor.execute(
                    "DELETE FROM anwesenheit_leiter WHERE gruppe_id = ? AND datum = ?",
                    (gruppe_id, datum),
                )

            chosen_ids = set()
            for slot in (1, 2):
                val = request.form.get(f"vertretung_{slot}_{datum}", "").strip()
                if val.isdigit():
                    gl_id = int(val)
                    if gl_id not in feste_ids:
                        chosen_ids.add(gl_id)

            for gl_id in chosen_ids:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO anwesenheit_leiter
                        (gruppenleiter_id, gruppe_id, datum, anwesend)
                    VALUES (?, ?, ?, 1)
                    """,
                    (gl_id, gruppe_id, datum),
                )


def baue_anwesenheits_excel(gruppe_id: int) -> tuple[BytesIO, str]:
    with get_kompass() as conn:
        cursor = conn.cursor()

        gruppe_row = cursor.execute(
            "SELECT id, name FROM jugendgruppen WHERE id = ?",
            (gruppe_id,),
        ).fetchone()

        cursor.execute(
            """
            SELECT
                vorname,
                nachname,
                datum,
                anwesend
            FROM
                anwesenheit
                JOIN mitglieder m ON mitglied_id = m.id
            WHERE
                gruppe_id = ?
            """,
            (gruppe_id,),
        )
        anwesenheit_mitglieder = cursor.fetchall()

        cursor.execute(
            """
            SELECT
                vorname,
                nachname,
                datum,
                anwesend
            FROM
                anwesenheit_leiter
                JOIN gruppenleiter gl ON gruppenleiter_id = gl.id
            WHERE
                gruppe_id = ?
            """,
            (gruppe_id,),
        )
        anwesenheit_gruppenleiter = cursor.fetchall()

    df_m = pd.DataFrame(
        anwesenheit_mitglieder, columns=["vorname", "nachname", "datum", "anwesend"]
    )
    df_l = pd.DataFrame(
        anwesenheit_gruppenleiter, columns=["vorname", "nachname", "datum", "anwesend"]
    )

    df_m["Rolle"] = "Mitglied"
    df_l["Rolle"] = "Jugendleiter:in"

    df = pd.concat([df_m, df_l], ignore_index=True)
    output = BytesIO()

    if df.empty:
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            pd.DataFrame({"Hinweis": ["Keine Anwesenheitsdaten vorhanden."]}).to_excel(
                writer, sheet_name="Anwesenheit", index=False
            )
    else:
        df["Person"] = df["vorname"] + " " + df["nachname"]
        df["datum"] = pd.to_datetime(df["datum"])

        table = pd.pivot_table(
            df,
            index=["Rolle", "Person"],
            columns="datum",
            values="anwesend",
            aggfunc="max",
        )

        table = table.fillna(0).replace({1: "X", 2: "E", 0: ""})
        table = table.sort_index(axis=1)
        table.columns = table.columns.strftime("%d.%m.%Y")

        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            table.to_excel(writer, sheet_name="Anwesenheit")
            sheet = writer.sheets["Anwesenheit"]

            for cell in sheet[1]:
                cell.font = Font(bold=True)

            for cell in sheet["A"]:
                if cell.row != 1 and cell.value in ["Mitglied", "Jugendleiter:in"]:
                    cell.font = Font(bold=True)

            for row in sheet.iter_rows(min_row=2, min_col=3):
                for cell in row:
                    cell.alignment = Alignment(horizontal="center")

            for col in sheet.columns:
                max_length = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    try:
                        if cell.value:
                            max_length = max(max_length, len(str(cell.value)))
                    except Exception:
                        pass
                sheet.column_dimensions[col_letter].width = max_length + 3

            sheet.freeze_panes = "C2"
            sheet.auto_filter.ref = sheet.dimensions

    output.seek(0)
    gruppen_name = ""
    if gruppe_row and gruppe_row["name"]:
        gruppen_name = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "_"
            for ch in str(gruppe_row["name"]).strip()
        ).strip("_")

    dateiname = (
        f"anwesenheit_{gruppen_name}_{gruppe_id}.xlsx"
        if gruppen_name
        else f"anwesenheit_gruppe_{gruppe_id}.xlsx"
    )
    return output, dateiname


def termin_loeschen(gruppe_id: int, datum: str) -> None:
    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM anwesenheit WHERE gruppe_id = ? AND datum = ?",
            (gruppe_id, datum),
        )
        cursor.execute(
            "DELETE FROM anwesenheit_leiter WHERE gruppe_id = ? AND datum = ?",
            (gruppe_id, datum),
        )
        try:
            cursor.execute(
                "DELETE FROM vertretungen WHERE gruppe_id = ? AND datum = ?",
                (gruppe_id, datum),
            )
        except Exception:
            pass
        conn.commit()