from datetime import date, datetime
from flask_login import current_user

from app.models import get_accounts, get_kompass
from app.services.mail_service import (
    hole_adressbuch_eintraege,
    sende_interne_kuendigungs_info,
    sende_kuendigungsbestaetigung,
)


def _lade_uname_zu_anzeigename() -> dict[str, str]:
    mapping: dict[str, str] = {}
    try:
        with get_accounts() as acc_conn:
            for uname, anzeigename in acc_conn.execute(
                "SELECT uname, anzeigename FROM accounts"
            ).fetchall():
                if uname and anzeigename and str(anzeigename).strip():
                    mapping[str(uname).strip()] = str(anzeigename).strip()
    except Exception:
        pass
    return mapping


def _aktueller_anzeigename(override: str | None = None) -> str:
    mapping = _lade_uname_zu_anzeigename()

    if override and override.strip():
        cleaned = override.strip()
        return mapping.get(cleaned, cleaned)

    try:
        if current_user and getattr(current_user, "is_authenticated", False):
            anzeige = getattr(current_user, "anzeigename", None)
            if isinstance(anzeige, str) and anzeige.strip():
                return anzeige.strip()

            uname = getattr(current_user, "uname", None) or getattr(
                current_user, "username", None
            )
            if isinstance(uname, str) and uname.strip():
                cleaned_uname = uname.strip()
                return mapping.get(cleaned_uname, cleaned_uname)
    except Exception:
        pass

    return "System"


def log_mitglied_historie(
    mitglied_id: int,
    ereignis_typ: str,
    bearbeiter_uname: str | None = None,
    detail_text: str | None = None,
    vorgangsnummer: str | None = None,
    zeitpunkt: str | None = None,
    conn=None,
):
    if not mitglied_id:
        return
    ts = zeitpunkt or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    akteur = None if ereignis_typ == "anmeldung" else _aktueller_anzeigename(bearbeiter_uname)

    def _insert(c):
        c.execute(
            """
            INSERT INTO mitglied_historie (
                mitglied_id, ereignis_typ, zeitpunkt, bearbeiter_uname, detail_text, vorgangsnummer
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (mitglied_id, ereignis_typ, ts, akteur, detail_text, vorgangsnummer),
        )

    if conn is not None:
        _insert(conn)
    else:
        with get_kompass() as own_conn:
            _insert(own_conn)
            own_conn.commit()


def log_gruppenaufnahme_historie(
    mitglied_id: int, gruppen_name: str, bearbeiter_uname: str | None = None
):
    log_mitglied_historie(
        mitglied_id=mitglied_id,
        ereignis_typ="gruppe_aufnahme",
        bearbeiter_uname=bearbeiter_uname,
        detail_text=f"Zu Gruppe hinzugefügt: {gruppen_name}",
    )


def log_gruppenentfernung_historie(
    mitglied_id: int, gruppen_name: str, bearbeiter_uname: str | None = None
):
    log_mitglied_historie(
        mitglied_id=mitglied_id,
        ereignis_typ="gruppe_entfernt",
        bearbeiter_uname=bearbeiter_uname,
        detail_text=f"Aus Gruppe entfernt: {gruppen_name}",
    )


def get_mitglied_historie(mitglied_id: int) -> tuple[list[dict], dict | None]:
    uname_zu_anzeigename = _lade_uname_zu_anzeigename()

    with get_kompass() as conn:
        rows = conn.execute(
            """
            SELECT id, mitglied_id, ereignis_typ, zeitpunkt, bearbeiter_uname, detail_text, vorgangsnummer
            FROM mitglied_historie
            WHERE mitglied_id = ?
            ORDER BY datetime(zeitpunkt) ASC, id ASC
            """,
            (mitglied_id,),
        ).fetchall()

        kuendigung_row = conn.execute(
            """
            SELECT kuendigung_datum, kuendigung_vorgangsnummer
            FROM mitglieder
            WHERE id = ?
            LIMIT 1
            """,
            (mitglied_id,),
        ).fetchone()

        letzte_kuendigung = conn.execute(
            """
            SELECT kuendigungsart, kuendigungsdatum, vorgangsnummer, erstellt_am, bestaetigt_am, bestaetigt_von
            FROM kuendigungen
            WHERE vorgangsnummer = ?
               OR (mitglied_vorname = (SELECT vorname FROM mitglieder WHERE id = ?)
                   AND mitglied_nachname = (SELECT nachname FROM mitglieder WHERE id = ?))
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                kuendigung_row["kuendigung_vorgangsnummer"] if kuendigung_row else "",
                mitglied_id,
                mitglied_id,
            ),
        ).fetchone()

    eintraege = []
    for r in rows:
        item = dict(r)
        try:
            dt = datetime.strptime(item["zeitpunkt"][:19], "%Y-%m-%d %H:%M:%S")
            datum_kurz = dt.strftime("%d.%m.%y")
            uhrzeit = dt.strftime("%H:%M")
        except Exception:
            datum_kurz = item["zeitpunkt"]
            uhrzeit = ""

        typ = item.get("ereignis_typ")
        raw_detail = (item.get("detail_text") or "").strip()

        if typ == "anmeldung":
            event_text = "Online Angemeldet"
        elif typ == "bestaetigung":
            event_text = "Bestätigt"
        elif typ == "gruppe_aufnahme" and raw_detail.startswith("Aufnahme in Gruppe: "):
            event_text = "Zu Gruppe hinzugefügt: " + raw_detail.removeprefix("Aufnahme in Gruppe: ")
        elif typ == "kuendigung" and raw_detail.startswith("Kündigung (manuell) zum "):
            event_text = "Kündigung manuell erfasst zum " + raw_detail.removeprefix("Kündigung (manuell) zum ")
        else:
            event_text = raw_detail or typ or ""

        raw_akteur = (item.get("bearbeiter_uname") or "").strip() if typ != "anmeldung" else ""
        if raw_akteur:
            akteur = uname_zu_anzeigename.get(raw_akteur, raw_akteur)
        else:
            akteur = ""

        item["datum_kurz"] = datum_kurz
        item["uhrzeit"] = uhrzeit
        item["event_text"] = event_text
        item["akteur"] = akteur
        item["zeile"] = (
            f"{datum_kurz} {uhrzeit}: {event_text} von {akteur}"
            if akteur
            else f"{datum_kurz} {uhrzeit}: {event_text}"
        ).strip()

        eintraege.append(item)

    kuendigung_info = None
    if kuendigung_row and kuendigung_row["kuendigung_datum"]:
        k_raw = str(kuendigung_row["kuendigung_datum"])[:10]
        try:
            k_kurz = datetime.strptime(k_raw, "%Y-%m-%d").strftime("%d.%m.%y")
            k_de = datetime.strptime(k_raw, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            k_kurz = k_raw
            k_de = k_raw

        bestaetigt_am_kurz = None
        bestaetigt_von = None
        if letzte_kuendigung:
            raw_best_von = (
                letzte_kuendigung["bestaetigt_von"]
                if "bestaetigt_von" in letzte_kuendigung.keys()
                else None
            )
            if raw_best_von:
                raw_best_von_str = str(raw_best_von).strip()
                bestaetigt_von = uname_zu_anzeigename.get(
                    raw_best_von_str, raw_best_von_str
                )

            b_raw = (
                letzte_kuendigung["bestaetigt_am"]
                if "bestaetigt_am" in letzte_kuendigung.keys()
                else None
            )
            if b_raw:
                try:
                    bestaetigt_am_kurz = datetime.strptime(
                        str(b_raw)[:16], "%Y-%m-%d %H:%M"
                    ).strftime("%d.%m.%y %H:%M")
                except Exception:
                    bestaetigt_am_kurz = str(b_raw)

        kuendigung_info = {
            "datum_kurz": k_kurz,
            "datum_de": k_de,
            "vorgangsnummer": kuendigung_row["kuendigung_vorgangsnummer"],
            "art": (letzte_kuendigung["kuendigungsart"] if letzte_kuendigung else None),
            "bestaetigt_am_kurz": bestaetigt_am_kurz,
            "bestaetigt_von": bestaetigt_von,
        }

    return eintraege, kuendigung_info


def get_unapproved_mitglieder_daten():
    with get_kompass() as conn:
        conn.row_factory = lambda cursor, row: {
            col[0]: row[idx] for idx, col in enumerate(cursor.description)
        }
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                m.id,
                m.vorname,
                m.nachname,
                m.geburtsdatum,
                m.geschlecht,
                m.unterschrift,
                m.mitgliedschaft,
                m.beitraege,
                m.datenschutz,
                m.bilder,
                m.richtigkeit,
                a.strasse,
                a.hausnummer,
                a.plz,
                a.ort,
                e.id AS eb_id,
                e.vorname AS eb_vorname,
                e.nachname AS eb_nachname,
                e.email AS eb_email,
                e.telefon AS eb_telefon
            FROM
                mitglieder_unapproved m
                LEFT JOIN adressen_unapproved a ON m.adresse_id = a.id
                LEFT JOIN mitglied_erziehungsberechtigte_unapproved me ON m.id = me.mitglied_id
                LEFT JOIN erziehungsberechtigte_unapproved e ON me.erziehungsberechtigter_id = e.id
            ORDER BY
                m.nachname
            """
        )

        rows = cursor.fetchall()

        mitglieder_unapproved_daten = []
        for row in rows:
            erziehungsberechtigte = None
            if row.get("eb_id") is not None:
                erziehungsberechtigte = {
                    "id": row.pop("eb_id"),
                    "vorname": row.pop("eb_vorname"),
                    "nachname": row.pop("eb_nachname"),
                    "email": row.pop("eb_email"),
                    "telefon": row.pop("eb_telefon"),
                }
            mitglieder_unapproved_daten.append(
                {**row, "erziehungsberechtigte": erziehungsberechtigte}
            )

    return mitglieder_unapproved_daten


def delete_unapproved(mitglied_id):
    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT adresse_id FROM mitglieder_unapproved WHERE id = ?", (mitglied_id,)
        )
        adresse_id = cursor.fetchone()["adresse_id"]

        cursor.execute(
            """
            DELETE FROM mitglied_erziehungsberechtigte_unapproved
            WHERE
                mitglied_id = ?
            """,
            (mitglied_id,),
        )

        cursor.execute(
            """
            DELETE FROM erziehungsberechtigte_unapproved
            WHERE
                id IN (
                    SELECT
                        erziehungsberechtigter_id
                    FROM
                        mitglied_erziehungsberechtigte_unapproved
                    WHERE
                        mitglied_id = ?
                    )
            """,
            (mitglied_id,),
        )

        cursor.execute(
            """
            DELETE FROM adressen_unapproved
            WHERE
                id = ?
            """,
            (adresse_id,),
        )
        cursor.execute(
            """
            DELETE FROM mitglieder_unapproved
            WHERE
                id = ?
            """,
            (mitglied_id,),
        )


def add_mitglied_to_unapproved(anmeldung_form_data):
    mitglied_vorname = anmeldung_form_data.get("mitglied_vorname")
    mitglied_nachname = anmeldung_form_data.get("mitglied_nachname")
    mitglied_geburtsdatum = anmeldung_form_data.get("mitglied_geburtsdatum")
    mitglied_geschlecht = anmeldung_form_data.get("mitglied_geschlecht")
    strasse = anmeldung_form_data.get("strasse")
    hausnummer = anmeldung_form_data.get("hausnummer")
    plz = anmeldung_form_data.get("plz")
    ort = anmeldung_form_data.get("ort")
    eb_vorname = anmeldung_form_data.get("eb_vorname")
    eb_nachname = anmeldung_form_data.get("eb_nachname")
    eb_email = anmeldung_form_data.get("eb_email")
    eb_telefon = anmeldung_form_data.get("eb_telefon")
    mitgliedschaft = anmeldung_form_data.get("mitgliedschaft_bestaetigt")
    beitraege = anmeldung_form_data.get("beitraege_bestaetigt")
    datenschutz = anmeldung_form_data.get("datenschutz_bestaetigt")
    bilder = anmeldung_form_data.get("bilder_bestaetigt")
    richtigkeit = anmeldung_form_data.get("richtigkeit_bestaetigt")
    unterschrift = anmeldung_form_data.get("unterschrift")

    with get_kompass() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO
                adressen_unapproved (strasse, hausnummer, plz, ort)
            VALUES
                (?, ?, ?, ?)
            """,
            (strasse, hausnummer, plz, ort),
        )
        adresse_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO
                erziehungsberechtigte_unapproved (vorname, nachname, email, telefon, adresse_id)
            VALUES
                (?, ?, ?, ?, ?)
            """,
            (eb_vorname, eb_nachname, eb_email, eb_telefon, adresse_id),
        )
        eb_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO
                mitglieder_unapproved (
                    vorname,
                    nachname,
                    geburtsdatum,
                    geschlecht,
                    adresse_id,
                    unterschrift,
                    mitgliedschaft,
                    beitraege,
                    datenschutz,
                    bilder,
                    richtigkeit
                )
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mitglied_vorname,
                mitglied_nachname,
                mitglied_geburtsdatum,
                mitglied_geschlecht,
                adresse_id,
                unterschrift,
                mitgliedschaft,
                beitraege,
                datenschutz,
                bilder,
                richtigkeit,
            ),
        )
        mitglied_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO
                mitglied_erziehungsberechtigte_unapproved (mitglied_id, erziehungsberechtigter_id)
            VALUES
                (?, ?)
            """,
            (mitglied_id, eb_id),
        )

        log_mitglied_historie(
            mitglied_id=mitglied_id,
            ereignis_typ="anmeldung",
            detail_text="Online Angemeldet",
            conn=conn,
        )


def approve_unapproved(mitglied_id):
    with get_kompass() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                adresse_id
            FROM
                mitglieder_unapproved
            WHERE
                id = ?
            """,
            (mitglied_id,),
        )
        adresse_id = cursor.fetchone()["adresse_id"]

        cursor.execute(
            """
            INSERT INTO
                adressen (strasse, hausnummer, plz, ort)
            SELECT
                strasse,
                hausnummer,
                plz,
                ort
            FROM
                adressen_unapproved
            WHERE
                id = ?
            """,
            (adresse_id,),
        )
        adress_id_neu = cursor.lastrowid

        cursor.execute(
            """
            INSERT INTO
                mitglieder (
                    vorname,
                    nachname,
                    geburtsdatum,
                    geschlecht,
                    adresse_id,
                    bilder,
                    unterschrift
                )
            SELECT
                vorname,
                nachname,
                geburtsdatum,
                geschlecht,
                ?,
                bilder,
                unterschrift
            FROM
                mitglieder_unapproved
            WHERE
                id = ?
            """,
            (adress_id_neu, mitglied_id),
        )
        mitglied_id_neu = cursor.lastrowid

        cursor.execute(
            """
            INSERT INTO
                erziehungsberechtigte (vorname, nachname, email, telefon, adresse_id)
            SELECT
                vorname,
                nachname,
                email,
                telefon,
                ?
            FROM
                erziehungsberechtigte_unapproved
            WHERE
                id IN (
                    SELECT
                        erziehungsberechtigter_id
                    FROM
                        mitglied_erziehungsberechtigte_unapproved
                    WHERE
                        mitglied_id = ?
                )
            """,
            (adress_id_neu, mitglied_id),
        )
        erziehungsberechtigter_id_neu = cursor.lastrowid

        cursor.execute(
            """
            INSERT INTO
                mitglied_erziehungsberechtigte (mitglied_id, erziehungsberechtigter_id, rolle)
            SELECT
                ?,
                ?,
                rolle
            FROM
                mitglied_erziehungsberechtigte_unapproved
            WHERE
                mitglied_id = ?
            """,
            (mitglied_id_neu, erziehungsberechtigter_id_neu, mitglied_id),
        )

        cursor.execute(
            """
            UPDATE mitglied_historie
            SET mitglied_id = ?
            WHERE mitglied_id = ? AND ereignis_typ = 'anmeldung'
            """,
            (mitglied_id_neu, mitglied_id),
        )
        log_mitglied_historie(
            mitglied_id=mitglied_id_neu,
            ereignis_typ="bestaetigung",
            detail_text="Bestätigt",
            conn=conn,
        )


def approve(mitglied_id):
    approve_unapproved(mitglied_id)
    delete_unapproved(mitglied_id)


def get_mitglieder_daten(mitglied_id=None, unterschrift=False):
    with get_kompass() as conn:
        cursor = conn.cursor()

        select_fields = (
            "m.id, m.vorname, m.nachname, m.geburtsdatum, m.geschlecht, "
            "m.adresse_id, m.mitgliedsnummer, m.kuendigung_datum, "
            "m.kuendigung_vorgangsnummer, COALESCE(m.archiviert, 0) AS archiviert"
        )
        if unterschrift:
            select_fields += ", m.unterschrift"
        sql = f"""
            SELECT
                {select_fields},
                a.strasse,
                a.hausnummer,
                a.plz,
                a.ort
            FROM
                mitglieder m
                LEFT JOIN adressen a ON m.adresse_id = a.id
            """
        params = ()
        if mitglied_id is not None:
            sql += " WHERE m.id = ?"
            params = (mitglied_id,)
        else:
            sql += " ORDER BY m.nachname COLLATE NOCASE ASC, m.vorname COLLATE NOCASE ASC"

        cursor.execute(sql, params)
        mitglieder_rows = cursor.fetchall()
        mitglieder_daten = []

        for m in mitglieder_rows:
            m_dict = dict(m)
            cursor.execute(
                """
                SELECT
                    j.*
                FROM
                    jugendgruppen j
                    JOIN mitglied_jugendgruppen mj ON j.id = mj.jugendgruppe_id
                WHERE
                    mj.mitglied_id = ?
                """,
                (m["id"],),
            )
            gruppen = [dict(row) for row in cursor.fetchall()]

            cursor.execute(
                """
                SELECT
                    e.*
                FROM
                    erziehungsberechtigte e
                    JOIN mitglied_erziehungsberechtigte me ON e.id = me.erziehungsberechtigter_id
                WHERE
                    me.mitglied_id = ?
                LIMIT
                    1
                """,
                (m["id"],),
            )
            erziehungsberechtigte_row = cursor.fetchone()
            erziehungsberechtigte = (
                dict(erziehungsberechtigte_row) if erziehungsberechtigte_row else None
            )

            mitglieder_daten.append(
                {
                    **m_dict,
                    "gruppen": gruppen,
                    "erziehungsberechtigte": erziehungsberechtigte,
                }
            )

    if mitglied_id is not None:
        return mitglieder_daten[0] if mitglieder_daten else None

    return mitglieder_daten


def _parse_iso_datum(wert):
    if not wert:
        return None
    text = str(wert).strip()[:10]
    try:
        teile = text.split("-")
        if len(teile) == 3:
            return date(int(teile[0]), int(teile[1]), int(teile[2]))
    except (ValueError, TypeError):
        return None
    return None


def _quartal_index(d: date) -> int:
    return d.year * 4 + (d.month - 1) // 3


def get_mitglieder_und_archiv():
    alle_mitglieder = get_mitglieder_daten()
    heute_datum = date.today()
    aktuelles_quartal = _quartal_index(heute_datum)

    aktive_mitglieder = []
    archiv_mitglieder = []

    for m in alle_mitglieder:
        ist_direkt_archiviert = bool(m.get("archiviert"))
        k_datum = _parse_iso_datum(m.get("kuendigung_datum"))

        m_angereichert = {
            **m,
            "kuendigung_datum_de": k_datum.strftime("%d.%m.%Y") if k_datum else None,
            "ist_direkt_archiviert": ist_direkt_archiviert,
        }

        if ist_direkt_archiviert:
            archiv_mitglieder.append(m_angereichert)
            continue

        if k_datum is not None:
            kuendigung_quartal = _quartal_index(k_datum)
            if (aktuelles_quartal - kuendigung_quartal) >= 2:
                archiv_mitglieder.append(m_angereichert)
                continue

        aktive_mitglieder.append(m_angereichert)

    return aktive_mitglieder, archiv_mitglieder


def update_mitglied(mitglied_id, bearbeiten_form_data):
    vorname = bearbeiten_form_data.get("vorname")
    nachname = bearbeiten_form_data.get("nachname")
    geburtsdatum = bearbeiten_form_data.get("geburtsdatum")
    geschlecht = bearbeiten_form_data.get("geschlecht")
    mitgliedsnummer = bearbeiten_form_data.get("mitgliedsnummer")
    strasse = bearbeiten_form_data.get("strasse")
    hausnummer = bearbeiten_form_data.get("hausnummer")
    plz = bearbeiten_form_data.get("plz")
    ort = bearbeiten_form_data.get("ort")
    eb_id = bearbeiten_form_data.get("eb_id")
    eb_vorname = bearbeiten_form_data.get("eb_vorname")
    eb_nachname = bearbeiten_form_data.get("eb_nachname")
    eb_email = bearbeiten_form_data.get("eb_email")
    eb_telefon = bearbeiten_form_data.get("eb_telefon")

    with get_kompass() as conn:
        cursor = conn.cursor()

        if geschlecht is not None:
            cursor.execute(
                """
                UPDATE mitglieder
                SET
                    vorname = ?,
                    nachname = ?,
                    geburtsdatum = ?,
                    geschlecht = ?,
                    mitgliedsnummer = ?
                WHERE
                    id = ?
                """,
                (vorname, nachname, geburtsdatum, geschlecht, mitgliedsnummer, mitglied_id),
            )
        else:
            cursor.execute(
                """
                UPDATE mitglieder
                SET
                    vorname = ?,
                    nachname = ?,
                    geburtsdatum = ?,
                    mitgliedsnummer = ?
                WHERE
                    id = ?
                """,
                (vorname, nachname, geburtsdatum, mitgliedsnummer, mitglied_id),
            )

        cursor.execute(
            """
            UPDATE adressen
            SET
                strasse = ?,
                hausnummer = ?,
                plz = ?,
                ort = ?
            WHERE
                id = (
                    SELECT
                        adresse_id
                    FROM
                        mitglieder
                    WHERE
                        id = ?
                )
            """,
            (strasse, hausnummer, plz, ort, mitglied_id),
        )

        if eb_id:
            cursor.execute(
                """
                UPDATE erziehungsberechtigte
                SET
                    vorname = ?,
                    nachname = ?,
                    email = ?,
                    telefon = ?
                WHERE
                    id = ?
                """,
                (
                    eb_vorname,
                    eb_nachname,
                    eb_email,
                    eb_telefon,
                    eb_id,
                ),
            )

        log_mitglied_historie(
            mitglied_id=mitglied_id,
            ereignis_typ="stammdaten",
            detail_text="Stammdaten bearbeitet",
            conn=conn,
        )


def manuelle_kuendigung_erfassen(
    mitglied_id: int,
    kuendigung_datum_raw: str,
    sende_bestaetigung: bool = False,
    adressbuch_ids_raw: list[str] | None = None,
    bearbeiter_name: str = "",
    freitext: str = "",
) -> dict:
    k_datum = _parse_iso_datum(kuendigung_datum_raw)
    if k_datum is None:
        raise ValueError("Bitte ein gültiges Kündigungsdatum angeben.")

    freitext_clean = (freitext or "").strip()
    grund_text = (
        f"Manuell erfasst: {freitext_clean}"
        if freitext_clean
        else "Manuell im Mitgliedermanagement erfasst"
    )

    k_datum_iso = k_datum.isoformat()
    k_datum_de = k_datum.strftime("%d.%m.%Y")
    jetzt_dt = datetime.now()
    jetzt_str = jetzt_dt.strftime("%Y-%m-%d %H:%M:%S")
    jetzt_de = jetzt_dt.strftime("%d.%m.%Y %H:%M")
    quartal_nr = (k_datum.month - 1) // 3 + 1
    vorgangsnummer = (
        f"{k_datum.strftime('%y')}-{quartal_nr}-M{mitglied_id}-{jetzt_dt.strftime('%H%M%S')}"
    )

    with get_kompass() as conn:
        row = conn.execute(
            """
            SELECT
                m.id, m.vorname, m.nachname, m.geburtsdatum, m.mitgliedsnummer,
                e.vorname AS eb_vorname, e.nachname AS eb_nachname, e.email AS eb_email
            FROM mitglieder m
            LEFT JOIN mitglied_erziehungsberechtigte me ON m.id = me.mitglied_id
            LEFT JOIN erziehungsberechtigte e ON me.erziehungsberechtigter_id = e.id
            WHERE m.id = ?
            LIMIT 1
            """,
            (mitglied_id,),
        ).fetchone()

        if not row:
            raise ValueError("Das ausgewählte Mitglied wurde nicht gefunden.")

        m_data = dict(row)

        conn.execute(
            """
            UPDATE mitglieder
            SET kuendigung_datum = ?,
                kuendigung_vorgangsnummer = ?,
                archiviert = 0
            WHERE id = ?
            """,
            (k_datum_iso, vorgangsnummer, mitglied_id),
        )

        tabellen = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='kuendigungen'"
            ).fetchall()
        ]
        if tabellen:
            conn.execute(
                """
                INSERT INTO kuendigungen (
                    erstellt_am, mitglied_vorname, mitglied_nachname,
                    eb_vorname, eb_nachname, eb_email,
                    kuendigungsart, kuendigungsdatum, dav_sektion_kuendigen,
                    grund, datenschutz_bestaetigt, richtigkeit_bestaetigt,
                    unterschrift, status, vorgangsnummer, bestaetigt_am,
                    bestaetigt_von, freitext_bestaetigung, bestaetigung_gesendet
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'bestaetigt', ?, ?, ?, ?, ?)
                """,
                (
                    jetzt_str,
                    m_data.get("vorname"),
                    m_data.get("nachname"),
                    m_data.get("eb_vorname") or "",
                    m_data.get("eb_nachname") or "",
                    m_data.get("eb_email") or "",
                    "manuell",
                    k_datum_iso,
                    1,
                    grund_text,
                    1,
                    1,
                    "",
                    vorgangsnummer,
                    jetzt_str,
                    bearbeiter_name or None,
                    freitext_clean or None,
                    1 if sende_bestaetigung else 0,
                ),
            )

        historie_detail = (
            f"Kündigung manuell erfasst zum {k_datum_de} ({freitext_clean})"
            if freitext_clean
            else f"Kündigung manuell erfasst zum {k_datum_de}"
        )
        log_mitglied_historie(
            mitglied_id=mitglied_id,
            ereignis_typ="kuendigung",
            bearbeiter_uname=bearbeiter_name,
            detail_text=historie_detail,
            vorgangsnummer=vorgangsnummer,
            zeitpunkt=jetzt_str,
            conn=conn,
        )

        conn.commit()

    mail_daten = {
        "vorgangsnummer": vorgangsnummer,
        "mitglied_vorname": m_data.get("vorname") or "",
        "mitglied_nachname": m_data.get("nachname") or "",
        "eb_vorname": m_data.get("eb_vorname") or "",
        "eb_nachname": m_data.get("eb_nachname") or "",
        "eb_email": (m_data.get("eb_email") or "").strip(),
        "kuendigungsart": "manuell",
        "kuendigungsart_label": "Manuell erfasst (außerordentlich / rückwirkend möglich)",
        "kuendigungsdatum": k_datum_iso,
        "kuendigungsdatum_de": k_datum_de,
        "erstellt_am": jetzt_str,
        "erstellt_am_de": jetzt_de,
        "dav_sektion_kuendigen": 1,
        "grund": grund_text,
    }

    bestaetigung_gesendet = False
    mail_fehler: list[str] = []

    if sende_bestaetigung:
        if mail_daten["eb_email"]:
            try:
                sende_kuendigungsbestaetigung(mail_daten, freitext=freitext_clean)
                bestaetigung_gesendet = True
            except Exception as exc:
                mail_fehler.append(f"Bestätigungs-Mail fehlgeschlagen: {exc}")
        else:
            mail_fehler.append("Keine E-Mail-Adresse bei den Erziehungsberechtigten hinterlegt.")

    selected_ids = set()
    for raw_id in adressbuch_ids_raw or []:
        try:
            selected_ids.add(int(raw_id))
        except (TypeError, ValueError):
            continue

    ausgewaehlte_empfaenger = [
        e for e in hole_adressbuch_eintraege() if e.get("aktiv") and e.get("id") in selected_ids
    ]
    interne_mails_gesendet = 0
    if ausgewaehlte_empfaenger:
        try:
            interne_mails_gesendet = sende_interne_kuendigungs_info(
                ausgewaehlte_empfaenger,
                mail_daten,
                freitext=freitext_clean,
            )
        except Exception as exc:
            mail_fehler.append(f"Interne Info-Mail fehlgeschlagen: {exc}")

    return {
        "vorgangsnummer": vorgangsnummer,
        "kuendigung_datum_de": k_datum_de,
        "bestaetigung_gesendet": bestaetigung_gesendet,
        "interne_mails_gesendet": interne_mails_gesendet,
        "mail_fehler": mail_fehler,
    }


def setze_mitglied_archiviert(mitglied_id: int, archiviert: bool) -> dict:
    zielwert = 1 if archiviert else 0
    with get_kompass() as conn:
        row = conn.execute(
            "SELECT id, vorname, nachname FROM mitglieder WHERE id = ?",
            (mitglied_id,),
        ).fetchone()
        if not row:
            raise ValueError("Das ausgewählte Mitglied wurde nicht gefunden.")

        conn.execute(
            "UPDATE mitglieder SET archiviert = ? WHERE id = ?",
            (zielwert, mitglied_id),
        )
        log_mitglied_historie(
            mitglied_id=mitglied_id,
            ereignis_typ="archiviert" if archiviert else "entarchiviert",
            detail_text="Mitglied archiviert" if archiviert else "Mitglied aus Archiv wiederhergestellt",
            conn=conn,
        )
        conn.commit()

    return {
        "id": row["id"],
        "vorname": row["vorname"],
        "nachname": row["nachname"],
        "archiviert": bool(zielwert),
    }