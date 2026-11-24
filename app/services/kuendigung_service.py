from datetime import date, datetime, timedelta

from app.models import get_kompass
from app.services.mail_service import (
    hole_adressbuch_eintraege,
    sende_dav_kuendigungs_info_an_geschaeftsstelle,
    sende_kuendigungsbestaetigung,
)

KUENDIGUNGSART_LABELS = {
    "fruehestmoeglich": "Frühestmöglich",
    "quartalsende": "Zum Quartalsende",
    "monatsende": "Zum Monatsende",
}

KUENDIGUNG_FILTER_LABELS = {
    "ausstehende": "Nur ausstehende",
    "6_monate": "Letzte 6 Monate",
    "alle": "Alle Kündigungen",
}


def _sicherstelle_dav_spalte(conn) -> None:
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(kuendigungen);").fetchall()
    }
    if "dav_kuendigungsdatum" not in cols:
        conn.execute("ALTER TABLE kuendigungen ADD COLUMN dav_kuendigungsdatum TEXT;")


def berechne_naechstmoegliche_dav_kuendigung(stichtag: date | None = None) -> tuple[date, str]:
    heute = stichtag or date.today()
    stichtag_oktober = date(heute.year, 10, 1)
    zieljahr = heute.year + 1 if heute >= stichtag_oktober else heute.year
    ziel_datum = date(zieljahr, 12, 31)
    return ziel_datum, ziel_datum.strftime("%d.%m.%Y")


def _formatiere_datum_de(wert) -> str:
    if not wert:
        return ""
    text = str(wert).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[:19], fmt)
            return dt.strftime("%d.%m.%Y %H:%M") if "H" in fmt else dt.strftime("%d.%m.%Y")
        except ValueError:
            continue
    return text


def get_alle_mitglieder_kurz() -> list[dict]:
    with get_kompass() as conn:
        rows = conn.execute(
            """
            SELECT id, vorname, nachname, geburtsdatum, mitgliedsnummer,
                   kuendigung_datum, kuendigung_vorgangsnummer
            FROM mitglieder
            ORDER BY nachname COLLATE NOCASE ASC, vorname COLLATE NOCASE ASC, id ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def bestaetige_kuendigung(
    kuendigung_id: int,
    mitglied_id_raw: str | None = None,
    adressbuch_ids_raw: list[str] | None = None,
    freitext: str = "",
    bearbeiter_name: str = "",
) -> dict:
    mail_fehler: list[str] = []

    with get_kompass() as conn:
        _sicherstelle_dav_spalte(conn)
        row = conn.execute(
            "SELECT * FROM kuendigungen WHERE id = ?;",
            (int(kuendigung_id),),
        ).fetchone()
        if not row:
            raise ValueError("Die ausgewählte Kündigung wurde nicht gefunden.")

        daten = dict(row)
        daten["kuendigungsdatum_de"] = _formatiere_datum_de(daten.get("kuendigungsdatum"))
        daten["erstellt_am_de"] = _formatiere_datum_de(daten.get("erstellt_am"))
        daten["kuendigungsart_label"] = KUENDIGUNGSART_LABELS.get(
            daten.get("kuendigungsart", ""),
            daten.get("kuendigungsart", ""),
        )
        daten["bearbeiter_name"] = (bearbeiter_name or "").strip()
        daten["mitglied_geburtsdatum_de"] = ""
        daten["mitgliedsnummer"] = ""
        daten["mitglied_anschrift"] = ""

        vorgangsnummer = daten.get("vorgangsnummer") or str(daten["id"])
        freitext_clean = (freitext or "").strip()

        dav_aktiv = bool(daten.get("dav_sektion_kuendigen"))
        if dav_aktiv:
            dav_iso_gespeichert = (daten.get("dav_kuendigungsdatum") or "").strip()
            if dav_iso_gespeichert:
                daten["dav_kuendigungsdatum_de"] = _formatiere_datum_de(dav_iso_gespeichert)
            else:
                _, dav_de = berechne_naechstmoegliche_dav_kuendigung()
                daten["dav_kuendigungsdatum_de"] = dav_de
        else:
            daten["dav_kuendigungsdatum_de"] = ""

        mitglied_vermerkt = False
        if mitglied_id_raw:
            try:
                mitglied_id = int(mitglied_id_raw)
                m_row = conn.execute(
                    "SELECT * FROM mitglieder WHERE id = ?;",
                    (mitglied_id,),
                ).fetchone()
                if m_row:
                    m_dict = dict(m_row)
                    conn.execute(
                        """
                        UPDATE mitglieder
                        SET kuendigung_datum = ?, kuendigung_vorgangsnummer = ?
                        WHERE id = ?;
                        """,
                        (daten.get("kuendigungsdatum"), vorgangsnummer, mitglied_id),
                    )
                    mitglied_vermerkt = True
                    daten["mitglied_geburtsdatum_de"] = _formatiere_datum_de(m_dict.get("geburtsdatum"))
                    daten["mitgliedsnummer"] = str(m_dict.get("mitgliedsnummer") or "").strip()

                    adresse_id = m_dict.get("adresse_id")
                    if adresse_id:
                        a_row = conn.execute(
                            "SELECT * FROM adressen WHERE id = ?;",
                            (adresse_id,),
                        ).fetchone()
                        if a_row:
                            a_dict = dict(a_row)
                            strasse_nr = " ".join(
                                p for p in [
                                    str(a_dict.get("strasse") or "").strip(),
                                    str(a_dict.get("hausnummer") or "").strip(),
                                ] if p
                            )
                            plz_ort = " ".join(
                                p for p in [
                                    str(a_dict.get("plz") or "").strip(),
                                    str(a_dict.get("ort") or "").strip(),
                                ] if p
                            )
                            daten["mitglied_anschrift"] = ", ".join(p for p in [strasse_nr, plz_ort] if p)
            except (TypeError, ValueError):
                mitglied_vermerkt = False

        selected_ids = set()
        for raw_id in adressbuch_ids_raw or []:
            try:
                selected_ids.add(int(raw_id))
            except (TypeError, ValueError):
                continue

        alle_adressen = hole_adressbuch_eintraege()
        bcc_adressen = []
        for e in alle_adressen:
            if e.get("aktiv") and e.get("id") in selected_ids:
                addr = (e.get("email") or "").strip()
                if addr and addr.lower() not in {a.lower() for a in bcc_adressen}:
                    bcc_adressen.append(addr)

        sende_kuendigungsbestaetigung(
            daten,
            freitext=freitext_clean,
            bcc_empfaenger=bcc_adressen,
        )

        dav_gs_mails_gesendet = 0
        if dav_aktiv:
            try:
                dav_gs_mails_gesendet = sende_dav_kuendigungs_info_an_geschaeftsstelle(daten)
            except Exception as exc:
                mail_fehler.append(f"E-Mail an Geschäftsstelle (DAV-Kündigung) fehlgeschlagen: {exc}")

        jetzt = datetime.now().isoformat(sep=" ", timespec="seconds")
        conn.execute(
            """
            UPDATE kuendigungen
            SET status = 'bestaetigt',
                bestaetigt_am = ?,
                bestaetigt_von = ?,
                freitext_bestaetigung = ?,
                bestaetigung_gesendet = 1
            WHERE id = ?;
            """,
            (jetzt, bearbeiter_name or None, freitext_clean or None, int(kuendigung_id)),
        )

    return {
        "vorgangsnummer": vorgangsnummer,
        "mitglied_vermerkt": mitglied_vermerkt,
        "interne_mails_gesendet": len(bcc_adressen),
        "dav_gs_mails_gesendet": dav_gs_mails_gesendet,
        "mail_fehler": mail_fehler,
    }


def get_kuendigungen(filter_modus: str = "ausstehende") -> list[dict]:
    modus = filter_modus if filter_modus in KUENDIGUNG_FILTER_LABELS else "ausstehende"

    with get_kompass() as conn:
        _sicherstelle_dav_spalte(conn)
        if modus == "ausstehende":
            k_rows = conn.execute(
                """
                SELECT *
                FROM kuendigungen
                WHERE status = 'eingegangen'
                ORDER BY erstellt_am ASC, id ASC
                """
            ).fetchall()
        elif modus == "6_monate":
            stichtag = (datetime.now() - timedelta(days=183)).strftime("%Y-%m-%d %H:%M:%S")
            k_rows = conn.execute(
                """
                SELECT *
                FROM kuendigungen
                WHERE erstellt_am >= ?
                  AND status != 'verworfen'
                ORDER BY erstellt_am DESC, id DESC
                """,
                (stichtag,),
            ).fetchall()
        else:
            k_rows = conn.execute(
                """
                SELECT *
                FROM kuendigungen
                ORDER BY erstellt_am DESC, id DESC
                """
            ).fetchall()

        m_rows = conn.execute(
            "SELECT id, vorname, nachname, geburtsdatum, mitgliedsnummer FROM mitglieder"
        ).fetchall()

    mitglieder_liste = [dict(m) for m in m_rows]
    ergebnis = []

    for row in k_rows:
        item = dict(row)
        item["ist_ausstehend"] = item.get("status") == "eingegangen"
        item["kuendigungsdatum_de"] = _formatiere_datum_de(item.get("kuendigungsdatum"))
        item["dav_kuendigungsdatum_de"] = _formatiere_datum_de(item.get("dav_kuendigungsdatum"))
        item["erstellt_am_de"] = _formatiere_datum_de(item.get("erstellt_am"))
        item["bestaetigt_am_de"] = _formatiere_datum_de(item.get("bestaetigt_am"))
        item["kuendigungsart_label"] = KUENDIGUNGSART_LABELS.get(
            item.get("kuendigungsart", ""),
            item.get("kuendigungsart", ""),
        )

        v_norm = (item.get("mitglied_vorname") or "").strip().lower()
        n_norm = (item.get("mitglied_nachname") or "").strip().lower()
        match = next(
            (
                m["id"]
                for m in mitglieder_liste
                if (m["vorname"] or "").strip().lower() == v_norm
                and (m["nachname"] or "").strip().lower() == n_norm
            ),
            None,
        )
        item["vorgeschlagene_mitglied_id"] = match
        ergebnis.append(item)

    return ergebnis


def get_ausstehende_kuendigungen() -> list[dict]:
    return get_kuendigungen("ausstehende")


def verwerfe_kuendigung(kuendigung_id: int, bearbeiter_name: str = "") -> str:
    jetzt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_kompass() as conn:
        row = conn.execute(
            "SELECT id, vorgangsnummer, status FROM kuendigungen WHERE id = ?",
            (kuendigung_id,),
        ).fetchone()
        if not row:
            raise ValueError("Kündigung wurde nicht gefunden.")
        if row["status"] != "eingegangen":
            raise ValueError("Nur offene Kündigungen können verworfen werden.")

        vorgangsnummer = row["vorgangsnummer"] or str(kuendigung_id)
        conn.execute(
            """
            UPDATE kuendigungen
            SET status = 'verworfen',
                bestaetigt_am = ?,
                bestaetigt_von = ?
            WHERE id = ?
            """,
            (jetzt_str, (bearbeiter_name or "").strip() or None, kuendigung_id),
        )
        conn.commit()

    return vorgangsnummer