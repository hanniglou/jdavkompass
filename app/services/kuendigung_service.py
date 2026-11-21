from datetime import datetime, timedelta

from app.models import get_kompass
from app.services.mail_service import (
    hole_adressbuch_eintraege,
    sende_interne_kuendigungs_info,
    sende_kuendigungsbestaetigung,
)

KUENDIGUNGSART_LABELS = {
    "fruehestmoeglich": "Frühestmöglich",
    "quartalsende": "Zum Quartalsende",
    "monatsende": "Zum Monatsende",
}


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
            ORDER BY nachname ASC, vorname ASC, id ASC
            """
        ).fetchall()
    return [dict(r) for r in rows]
    with get_kompass() as conn:
        k_rows = conn.execute(
            """
            SELECT *
            FROM kuendigungen
            WHERE status = 'eingegangen'
            ORDER BY erstellt_am ASC, id ASC
            """
        ).fetchall()
        m_rows = conn.execute(
            "SELECT id, vorname, nachname, geburtsdatum, mitgliedsnummer FROM mitglieder"
        ).fetchall()

    mitglieder_liste = [dict(m) for m in m_rows]
    ergebnis = []

    for row in k_rows:
        item = dict(row)
        item["kuendigungsdatum_de"] = _formatiere_datum_de(item.get("kuendigungsdatum"))
        item["erstellt_am_de"] = _formatiere_datum_de(item.get("erstellt_am"))
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


def bestaetige_kuendigung(
    kuendigung_id: int,
    mitglied_id_raw: str | None = None,
    adressbuch_ids_raw: list[str] | None = None,
    freitext: str = "",
    bearbeiter_name: str = "",
) -> dict:
    with get_kompass() as conn:
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
        vorgangsnummer = daten.get("vorgangsnummer") or str(daten["id"])
        freitext_clean = (freitext or "").strip()

        # 1. Vermerk in Tabelle 'mitglieder' setzen (falls Mitglied ausgewählt wurde)
        mitglied_vermerkt = False
        if mitglied_id_raw:
            try:
                mitglied_id = int(mitglied_id_raw)
                cur = conn.execute(
                    """
                    UPDATE mitglieder
                    SET kuendigung_datum = ?, kuendigung_vorgangsnummer = ?
                    WHERE id = ?;
                    """,
                    (daten.get("kuendigungsdatum"), vorgangsnummer, mitglied_id),
                )
                mitglied_vermerkt = cur.rowcount > 0
            except (TypeError, ValueError):
                mitglied_vermerkt = False

        # 2. Ausgewählte Empfänger aus dem Mini-Adressbuch ermitteln
        selected_ids = set()
        for raw_id in adressbuch_ids_raw or []:
            try:
                selected_ids.add(int(raw_id))
            except (TypeError, ValueError):
                continue

        alle_adressen = hole_adressbuch_eintraege()
        ausgewaehlte_empfaenger = [
            e for e in alle_adressen if e.get("aktiv") and e.get("id") in selected_ids
        ]

        # 3. Kündigungsbestätigung an Erziehungsberechtigte senden
        sende_kuendigungsbestaetigung(daten, freitext=freitext_clean)

        # 4. Interne Info-Mails an ausgewählte Adressen senden
        interne_mails_gesendet = sende_interne_kuendigungs_info(
            ausgewaehlte_empfaenger,
            daten,
            freitext=freitext_clean,
        )

        # 5. Status in 'kuendigungen' auf 'bestaetigt' setzen
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
        "interne_mails_gesendet": interne_mails_gesendet,
    }

KUENDIGUNG_FILTER_LABELS = {
    "ausstehende": "Nur ausstehende",
    "6_monate": "Letzte 6 Monate",
    "alle": "Alle Kündigungen",
}

def get_kuendigungen(filter_modus: str = "ausstehende") -> list[dict]:
    modus = filter_modus if filter_modus in KUENDIGUNG_FILTER_LABELS else "ausstehende"

    with get_kompass() as conn:
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
        item["ist_ausstehend"] = (item.get("status") == "eingegangen")
        item["kuendigungsdatum_de"] = _formatiere_datum_de(item.get("kuendigungsdatum"))
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