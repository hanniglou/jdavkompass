import csv
import html
import io
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from app.models import get_kompass, get_warteliste
from app.services.gruppe_service import empfehle_gruppen_jahrgang
from app.services.mail_service import (
    _ersetze_platzhalter,
    _sende_mail,
    _text_zu_html_absaetzen,
    hole_mail_template,
)

FALLBACK_TIER_WOERTER = [
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


def _baue_kontakt_namen(
    kontakt_vorname: str | None,
    kontakt_nachname: str | None,
    fallback_gesamt: str | None = None,
) -> tuple[str, str, str]:
    v = _clean_text(kontakt_vorname)
    n = _clean_text(kontakt_nachname)
    if not v and not n and fallback_gesamt:
        teile = _clean_text(fallback_gesamt).split(maxsplit=1)
        v = teile[0] if len(teile) >= 1 else ""
        n = teile[1] if len(teile) >= 2 else ""
    gesamt = f"{v} {n}".strip() or _clean_text(fallback_gesamt)
    return v, n, gesamt


def hole_aktive_tierwoerter() -> list[str]:
    try:
        with get_kompass() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='warteliste_tierwoerter'"
            ).fetchone()
            if exists:
                rows = conn.execute(
                    "SELECT wort FROM warteliste_tierwoerter WHERE aktiv = 1 ORDER BY wort COLLATE NOCASE ASC"
                ).fetchall()
                woerter = [_clean_text(r["wort"]).lower() for r in rows if _clean_text(r["wort"])]
                if woerter:
                    return woerter
    except Exception:
        pass
    return list(FALLBACK_TIER_WOERTER)


def generiere_tier_passwort() -> str:
    """Erzeugt ein Merk-Passwort im Format 'tier1.tier2.tier3' aus kompass.db."""
    pool = hole_aktive_tierwoerter()
    auswahl = [secrets.choice(pool) for _ in range(3)]
    return ".".join(auswahl)


def verifiziere_portal_passwort(passwort_hash: str | None, passwort_eingabe: str) -> bool:
    if not passwort_hash or not passwort_eingabe:
        return False
    return check_password_hash(passwort_hash, passwort_eingabe.strip().lower())


def _clean_text(val) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if s.lower() in {"nan", "none", "null"}:
        return ""
    return s


def _norm_key(vorname: str, nachname: str, geburtsdatum: str | None) -> tuple:
    v = " ".join(_clean_text(vorname).lower().split())
    n = " ".join(_clean_text(nachname).lower().split())
    g = _clean_text(geburtsdatum)[:10]
    return (v, n, g)


def _parse_geburtsdatum(raw_val) -> date | None:
    text = _clean_text(raw_val)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _berechne_alter(geburtsdatum: date | None, stichtag: date) -> int | None:
    if geburtsdatum is None:
        return None
    return (
        stichtag.year
        - geburtsdatum.year
        - ((stichtag.month, stichtag.day) < (geburtsdatum.month, geburtsdatum.day))
    )


def _parse_erstellt_am(raw_val, fallback: datetime) -> str:
    text = _clean_text(raw_val)
    if not text:
        return fallback.strftime("%Y-%m-%d %H:%M:%S")
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
    ):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return fallback.strftime("%Y-%m-%d %H:%M:%S")


def _formatiere_datum_de(raw_val: str | None) -> str:
    text = _clean_text(raw_val)
    if not text:
        return "unbekanntem Datum"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return text[:10]


def _parse_datetime(raw_val: str | None) -> datetime | None:
    text = _clean_text(raw_val)
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _hat_warteliste_label(labels_raw) -> bool:
    text = _clean_text(labels_raw)
    if not text:
        return False
    teile = [p.strip().lower() for p in text.split(";") if p.strip()]
    return any("warteliste" in p for p in teile)


def _lade_bekannte_kompass_personen() -> tuple[set[tuple], set[tuple]]:
    mit_datum: set[tuple] = set()
    ohne_datum: set[tuple] = set()

    with get_kompass() as conn:
        for tabelle in ("mitglieder", "mitglieder_unapproved"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (tabelle,),
            ).fetchone()
            if not exists:
                continue
            rows = conn.execute(
                f"SELECT vorname, nachname, geburtsdatum FROM {tabelle}"
            ).fetchall()
            for r in rows:
                v = " ".join(_clean_text(r["vorname"]).lower().split())
                n = " ".join(_clean_text(r["nachname"]).lower().split())
                g = _clean_text(r["geburtsdatum"])[:10]
                if not v and not n:
                    continue
                if g:
                    mit_datum.add((v, n, g))
                ohne_datum.add((v, n))

    return mit_datum, ohne_datum


def _lade_bestehende_warteliste_keys() -> tuple[set[tuple], set[tuple]]:
    mit_datum: set[tuple] = set()
    ohne_datum: set[tuple] = set()

    with get_warteliste() as conn:
        rows = conn.execute(
            "SELECT vorname, nachname, geburtsdatum FROM warteliste_eintraege"
        ).fetchall()
        for r in rows:
            v = " ".join(_clean_text(r["vorname"]).lower().split())
            n = " ".join(_clean_text(r["nachname"]).lower().split())
            g = _clean_text(r["geburtsdatum"])[:10]
            if not v and not n:
                continue
            if g:
                mit_datum.add((v, n, g))
            ohne_datum.add((v, n))

    return mit_datum, ohne_datum


def importiere_warteliste_aus_csv_bytes(
    csv_bytes: bytes,
    max_alter: int = 15,
    stichtag: date | None = None,
) -> dict:
    stichtag_eff = stichtag or date.today()
    jetzt_dt = datetime.now()

    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            text = csv_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("Die CSV-Datei konnte nicht decodiert werden.")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Die CSV-Datei enthält keine Kopfzeile.")

    kompass_mit_datum, kompass_ohne_datum = _lade_bekannte_kompass_personen()
    wl_mit_datum, wl_ohne_datum = _lade_bestehende_warteliste_keys()

    stats = {
        "gesamt_zeilen": 0,
        "importiert": 0,
        "uebersprungen_label": 0,
        "uebersprungen_alter": 0,
        "bereits_im_kompass": 0,
        "duplikate_warteliste": 0,
        "fehler": [],
        "importierte_namen": [],
    }

    with get_warteliste() as wl_conn:
        for zeilen_nr, row in enumerate(reader, start=2):
            stats["gesamt_zeilen"] += 1

            labels_val = row.get("Labels")
            if not _hat_warteliste_label(labels_val):
                stats["uebersprungen_label"] += 1
                continue

            vorname = _clean_text(row.get("First Name"))
            nachname = _clean_text(row.get("Last Name"))
            email = _clean_text(row.get("Email 1")) or _clean_text(row.get("Email 2"))

            if not vorname and not nachname:
                stats["fehler"].append(f"Zeile {zeilen_nr}: Vor- und Nachname fehlen.")
                continue
            if not email:
                stats["fehler"].append(
                    f"Zeile {zeilen_nr} ({vorname} {nachname}): Keine E-Mail-Adresse vorhanden."
                )
                continue

            geb_date = _parse_geburtsdatum(row.get("Birthdate"))
            geb_iso = geb_date.isoformat() if geb_date else None
            alter = _berechne_alter(geb_date, stichtag_eff)

            if alter is not None and alter > max_alter:
                stats["uebersprungen_alter"] += 1
                continue

            key_mit_datum = _norm_key(vorname, nachname, geb_iso)
            key_ohne_datum = (key_mit_datum[0], key_mit_datum[1])

            ist_im_kompass = (
                key_mit_datum in kompass_mit_datum
                if geb_iso
                else key_ohne_datum in kompass_ohne_datum
            )
            if ist_im_kompass:
                stats["bereits_im_kompass"] += 1
                continue

            ist_bereits_in_wl = (
                key_mit_datum in wl_mit_datum
                if geb_iso
                else key_ohne_datum in wl_ohne_datum
            )
            if ist_bereits_in_wl:
                stats["duplikate_warteliste"] += 1
                continue

            kontakt_name = _clean_text(row.get("Kontaktperson")) or None
            telefon = (
                _clean_text(row.get("Phone 1"))
                or _clean_text(row.get("Phone 2"))
                or _clean_text(row.get("Phone 3"))
                or None
            )
            interesse = _clean_text(row.get("Interesse")) or None
            erfahrung = _clean_text(row.get("Erfahrung")) or None
            erstellt_am = _parse_erstellt_am(row.get("Created At (UTC+0)"), jetzt_dt)

            wl_conn.execute(
                """
                INSERT INTO warteliste_eintraege (
                    vorname,
                    nachname,
                    geburtsdatum,
                    kontakt_name,
                    email,
                    telefon,
                    interesse,
                    erfahrung,
                    inklusionsgruppe,
                    quelle,
                    erstellt_am,
                    bestaetigungs_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'csv_import', ?, 'ausstehend')
                """,
                (
                    vorname,
                    nachname,
                    geb_iso,
                    kontakt_name,
                    email,
                    telefon,
                    interesse,
                    erfahrung,
                    erstellt_am,
                ),
            )

            if geb_iso:
                wl_mit_datum.add(key_mit_datum)
            wl_ohne_datum.add(key_ohne_datum)

            stats["importiert"] += 1
            stats["importierte_namen"].append(f"{vorname} {nachname}".strip())

        wl_conn.commit()

    return stats


def importiere_warteliste_aus_csv_datei(
    csv_pfad: str | Path,
    max_alter: int = 15,
    stichtag: date | None = None,
) -> dict:
    pfad = Path(csv_pfad)
    if not pfad.exists():
        raise FileNotFoundError(f"CSV-Datei nicht gefunden: {pfad}")
    return importiere_warteliste_aus_csv_bytes(
        pfad.read_bytes(),
        max_alter=max_alter,
        stichtag=stichtag,
    )


def hole_warteliste_eintrag_nach_token(token: str) -> dict | None:
    token_clean = _clean_text(token)
    if not token_clean:
        return None
    with get_warteliste() as conn:
        row = conn.execute(
            "SELECT * FROM warteliste_eintraege WHERE bestaetigung_token = ?",
            (token_clean,),
        ).fetchone()
    return dict(row) if row else None


def bereite_vier_wochen_einladung(eintrag_id: int, frist_tage: int = 28) -> dict:
    """Setzt Token und 4-Wochen-Frist für einen Wartelisten-Eintrag (ohne Passwort)."""
    jetzt = datetime.now()
    frist_bis = jetzt + timedelta(days=frist_tage)
    token = secrets.token_urlsafe(24)

    with get_warteliste() as conn:
        row = conn.execute(
            "SELECT * FROM warteliste_eintraege WHERE id = ?",
            (int(eintrag_id),),
        ).fetchone()
        if not row:
            raise ValueError(f"Wartelisten-Eintrag {eintrag_id} nicht gefunden.")

        vorhandenes_token = _clean_text(row["bestaetigung_token"])
        effektives_token = vorhandenes_token or token

        conn.execute(
            """
            UPDATE warteliste_eintraege
            SET bestaetigung_token = ?,
                bestaetigung_gesendet_am = ?,
                bestaetigungsfrist_bis = ?,
                bestaetigungs_status = CASE
                    WHEN bestaetigungs_status = 'bestaetigt' THEN 'bestaetigt'
                    ELSE 'ausstehend'
                END
            WHERE id = ?
            """,
            (
                effektives_token,
                jetzt.strftime("%Y-%m-%d %H:%M:%S"),
                frist_bis.strftime("%Y-%m-%d %H:%M:%S"),
                int(eintrag_id),
            ),
        )
        aktualisiert = conn.execute(
            "SELECT * FROM warteliste_eintraege WHERE id = ?",
            (int(eintrag_id),),
        ).fetchone()

    return dict(aktualisiert)


def sende_vier_wochen_bestaetigungs_mail(
    eintrag_id: int,
    bestaetigung_url: str,
    frist_tage: int = 28,
) -> dict:
    """Versendet die erste E-Mail ohne Passwort mit 4-Wochen-Frist und Aktualisierungslink."""
    eintrag = bereite_vier_wochen_einladung(eintrag_id=eintrag_id, frist_tage=frist_tage)
    empfaenger = _clean_text(eintrag.get("email"))
    if not empfaenger:
        raise ValueError(f"Eintrag {eintrag_id} hat keine E-Mail-Adresse.")

    kontakt = _clean_text(eintrag.get("kontakt_name")) or "Liebe Eltern"
    kind_name = f"{_clean_text(eintrag.get('vorname'))} {_clean_text(eintrag.get('nachname'))}".strip()
    datum_einreichung = _formatiere_datum_de(eintrag.get("erstellt_am"))
    frist_datum_de = _formatiere_datum_de(eintrag.get("bestaetigungsfrist_bis"))

    platzhalter_plain = {
        "eb_name": kontakt,
        "kind_name": kind_name,
        "datum_einreichung": datum_einreichung,
        "frist_datum": frist_datum_de,
        "bestaetigungs_url": bestaetigung_url,
    }
    platzhalter_html = {
        "eb_name": html.escape(kontakt),
        "kind_name": html.escape(kind_name),
        "datum_einreichung": html.escape(datum_einreichung),
        "frist_datum": html.escape(frist_datum_de),
        "bestaetigungs_url": html.escape(bestaetigung_url),
    }

    tpl = hole_mail_template("warteliste_alt_bestaetigung")
    if tpl:
        subject = _ersetze_platzhalter(tpl["betreff"], platzhalter_plain)
        plain_body = _ersetze_platzhalter(tpl["text_vorlage"], platzhalter_plain)
        custom_html = _clean_text(tpl.get("html_vorlage"))
        if custom_html:
            html_body = _ersetze_platzhalter(custom_html, platzhalter_html)
        else:
            html_body = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"></head>
<body style="font-family: Inter, Arial, Helvetica, sans-serif; color: #1f2937; line-height: 1.55; max-width: 620px; margin: 0 auto; padding: 20px;">
    {_text_zu_html_absaetzen(plain_body)}
</body>
</html>
"""
    else:
        subject = f"JDAV Hildesheim – Warteliste Jugendgruppe: Bitte Interesse bestätigen ({kind_name})"
        plain_body = (
            f"Hey {kontakt},\n\n"
            f"wir sind gerade dabei, unsere Warteliste in unser neues digitales Jugendgruppentool umzuziehen.\n\n"
            f"Du hast dich am {datum_einreichung} bei uns auf die Warteliste gesetzt (für {kind_name}). "
            f"Wenn du weiterhin Interesse hast, bestätige das bitte kurz hier:\n"
            f"{bestaetigung_url}\n\n"
            f"Wenn wir in den nächsten vier Wochen (bis zum {frist_datum_de}) nichts mehr von dir hören, "
            f"gehen wir davon aus, dass du kein Interesse mehr an der Jugendgruppe hast und streichen dich von der Warteliste.\n\n"
            f"Liebe Grüße, dein Team der JDAV Hildesheim"
        )
        html_body = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"></head>
<body style="font-family: Inter, Arial, Helvetica, sans-serif; color: #1f2937; line-height: 1.55; max-width: 620px; margin: 0 auto; padding: 20px;">
    {_text_zu_html_absaetzen(plain_body)}
</body>
</html>
"""

    _sende_mail(
        empfaenger,
        subject,
        plain_body,
        html_body,
        reply_to="warteliste@jdav-hildesheim.de",
    )
    return eintrag


def sende_anmeldebestaetigung_mit_portal_zugang(
    eintrag: dict,
    tier_passwort: str,
    portal_url: str,
    aus_alter_warteliste: bool = False,
) -> None:
    """Versendet die reguläre Anmeldebestätigung inklusive Zusammenfassung, Tier-Passwort und Portal-Link."""
    empfaenger = _clean_text(eintrag.get("email"))
    if not empfaenger:
        raise ValueError("Keine Empfänger-E-Mail-Adresse vorhanden.")

    kontakt = _clean_text(eintrag.get("kontakt_name")) or "Liebe Eltern"
    kind_name = f"{_clean_text(eintrag.get('vorname'))} {_clean_text(eintrag.get('nachname'))}".strip()
    geb_de = _formatiere_datum_de(eintrag.get("geburtsdatum")) if eintrag.get("geburtsdatum") else "Nicht angegeben"
    erfahrung = _clean_text(eintrag.get("erfahrung")) or "Nicht angegeben"
    interesse = _clean_text(eintrag.get("interesse")) or "Nicht angegeben"
    telefon = _clean_text(eintrag.get("telefon")) or "Nicht angegeben"
    erstellt_de = _formatiere_datum_de(eintrag.get("erstellt_am"))

    ist_uebernommen = bool(aus_alter_warteliste or eintrag.get("aus_alter_warteliste_uebernommen"))
    uebernahme_plain = (
        "Hinweis: Dein Wartelisteneintrag wurde aus unserer alten Warteliste übernommen.\n\n"
        if ist_uebernommen
        else ""
    )
    uebernahme_html = (
        '<div style="margin: 0 0 16px 0; padding: 12px 14px; background-color: #f0fdf4; border: 1px solid #bbf7d0; border-left: 4px solid #15803d; border-radius: 8px; color: #14532d; font-size: 14.5px; font-style: normal;">'
        '<strong style="font-style: normal;">Hinweis:</strong> Dein Wartelisteneintrag wurde aus unserer alten Warteliste übernommen.'
        "</div>"
        if ist_uebernommen
        else ""
    )

    platzhalter_plain = {
        "eb_name": kontakt,
        "kind_name": kind_name,
        "geburtsdatum": geb_de,
        "erfahrung": erfahrung,
        "interesse": interesse,
        "eb_email": empfaenger,
        "eb_telefon": telefon,
        "erstellt_am": erstellt_de,
        "portal_url": portal_url,
        "tier_passwort": tier_passwort,
        "uebernahme_hinweis": uebernahme_plain,
    }
    platzhalter_html = {
        "eb_name": html.escape(kontakt),
        "kind_name": html.escape(kind_name),
        "geburtsdatum": html.escape(geb_de),
        "erfahrung": html.escape(erfahrung),
        "interesse": html.escape(interesse),
        "eb_email": html.escape(empfaenger),
        "eb_telefon": html.escape(telefon),
        "erstellt_am": html.escape(erstellt_de),
        "portal_url": html.escape(portal_url),
        "tier_passwort": html.escape(tier_passwort),
        "uebernahme_hinweis": uebernahme_html,
    }

    tpl = hole_mail_template("warteliste_anmeldebestaetigung")
    if tpl:
        subject = _ersetze_platzhalter(tpl["betreff"], platzhalter_plain)
        plain_body = _ersetze_platzhalter(tpl["text_vorlage"], platzhalter_plain)
        custom_html = _clean_text(tpl.get("html_vorlage"))
        if custom_html:
            html_body = _ersetze_platzhalter(custom_html, platzhalter_html)
        else:
            html_body = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, Helvetica, sans-serif; font-style: normal; color: #1f2937; line-height: 1.55; max-width: 620px; margin: 0 auto; padding: 20px;">
    {_text_zu_html_absaetzen(plain_body)}
</body>
</html>
"""
    else:
        subject = f"JDAV Hildesheim – Anmeldebestätigung & Zugang zum Wartelistenportal ({kind_name})"
        plain_body = (
            f"Hallo {kontakt},\n\n"
            f"{uebernahme_plain}"
            f"vielen Dank – der Wartelisteneintrag für {kind_name} ist nun bestätigt.\n\n"
            f"Zusammenfassung der Anmeldung:\n"
            f"- Kind: {kind_name}\n"
            f"- Geburtsdatum: {geb_de}\n"
            f"- Erfahrung: {erfahrung}\n"
            f"- Interesse an: {interesse}\n"
            f"- Kontaktperson: {kontakt}\n"
            f"- E-Mail: {empfaenger}\n"
            f"- Telefon: {telefon}\n"
            f"- Eingetragen am: {erstellt_de}\n\n"
            f"Zugang zum Wartelistenportal:\n"
            f"Portal-Link: {portal_url}\n"
            f"Login mit Name des Kindes: {kind_name}\n"
            f"Dein Passwort: {tier_passwort}\n\n"
            f"Viele Grüße\nDein Team der JDAV Hildesheim"
        )
        html_body = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, Helvetica, sans-serif; font-style: normal; color: #1f2937; line-height: 1.55; max-width: 620px; margin: 0 auto; padding: 20px;">
    {_text_zu_html_absaetzen(plain_body)}
</body>
</html>
"""

    _sende_mail(
        empfaenger,
        subject,
        plain_body,
        html_body,
        reply_to="warteliste@jdav-hildesheim.de",
    )


def verarbeite_alt_warteliste_antwort(
    token: str,
    aktion: str,
    aktualisierte_daten: dict | None = None,
    portal_url: str = "",
    referenz_zeitpunkt: datetime | None = None,
) -> dict:
    """Verarbeitet Bestätigung ('bestaetigen') oder Austragung ('austragen') per 4-Wochen-Token."""
    eintrag = hole_warteliste_eintrag_nach_token(token)
    if not eintrag:
        return {"ok": False, "grund": "ungueltig", "nachricht": "Ungültiger oder unbekannter Link."}

    jetzt = referenz_zeitpunkt or datetime.now()
    frist_dt = _parse_datetime(eintrag.get("bestaetigungsfrist_bis"))

    if eintrag.get("bestaetigungs_status") == "abgemeldet":
        return {"ok": False, "grund": "abgemeldet", "nachricht": "Dieser Eintrag wurde bereits von der Warteliste abgemeldet."}

    if frist_dt is not None and jetzt > frist_dt and eintrag.get("bestaetigungs_status") != "bestaetigt":
        with get_warteliste() as conn:
            conn.execute(
                "UPDATE warteliste_eintraege SET bestaetigungs_status = 'abgelaufen' WHERE id = ?",
                (eintrag["id"],),
            )
        return {
            "ok": False,
            "grund": "abgelaufen",
            "nachricht": f"Die vierwöchige Bestätigungsfrist (bis {_formatiere_datum_de(eintrag.get('bestaetigungsfrist_bis'))}) ist leider abgelaufen.",
        }

    if aktion == "austragen":
        with get_warteliste() as conn:
            conn.execute(
                """
                UPDATE warteliste_eintraege
                SET bestaetigungs_status = 'abgemeldet',
                    aktualisiert_am = ?
                WHERE id = ?
                """,
                (jetzt.strftime("%Y-%m-%d %H:%M:%S"), eintrag["id"]),
            )
        return {
            "ok": True,
            "grund": "abgemeldet",
            "nachricht": "Der Eintrag wurde wie gewünscht von der Warteliste entfernt.",
        }

    if aktion != "bestaetigen":
        return {"ok": False, "grund": "aktion_ungueltig", "nachricht": "Ungültige Aktion."}

    payload = aktualisierte_daten or {}
    geb_raw = _clean_text(payload.get("geburtsdatum"))
    if geb_raw:
        geb_parsed = _parse_geburtsdatum(geb_raw)
        if not geb_parsed:
            return {
                "ok": False,
                "grund": "geburtsdatum_ungueltig",
                "nachricht": "Bitte ein gültiges Geburtsdatum angeben.",
            }
        geb_iso = geb_parsed.isoformat()
    else:
        geb_iso = _clean_text(eintrag.get("geburtsdatum")) or None

    kontakt_name = _clean_text(payload.get("kontakt_name")) or _clean_text(eintrag.get("kontakt_name"))
    email = _clean_text(payload.get("email")) or _clean_text(eintrag.get("email"))
    telefon = _clean_text(payload.get("telefon")) if "telefon" in payload else _clean_text(eintrag.get("telefon"))
    interesse = _clean_text(payload.get("interesse")) or _clean_text(eintrag.get("interesse"))
    erfahrung = _clean_text(payload.get("erfahrung")) or _clean_text(eintrag.get("erfahrung"))
    inklusion = 1 if (interesse == "Inklusive Klettergruppe" or payload.get("inklusionsgruppe")) else 0
    notizen = _clean_text(payload.get("notizen")) if "notizen" in payload else _clean_text(eintrag.get("notizen"))

    if not email:
        return {"ok": False, "grund": "email_fehlt", "nachricht": "Bitte eine gültige E-Mail-Adresse angeben."}

    tier_passwort = generiere_tier_passwort()
    passwort_hash = generate_password_hash(tier_passwort)
    ist_csv = _clean_text(eintrag.get("quelle")) == "csv_import"

    with get_warteliste() as conn:
        conn.execute(
            """
            UPDATE warteliste_eintraege
            SET geburtsdatum = ?,
                kontakt_name = ?,
                email = ?,
                telefon = ?,
                interesse = ?,
                erfahrung = ?,
                inklusionsgruppe = ?,
                notizen = ?,
                bestaetigungs_status = 'bestaetigt',
                bestaetigt_am = COALESCE(bestaetigt_am, ?),
                aktualisiert_am = ?,
                portal_passwort_hash = COALESCE(portal_passwort_hash, ?),
                aus_alter_warteliste_uebernommen = CASE
                    WHEN ? = 1 THEN 1
                    ELSE aus_alter_warteliste_uebernommen
                END
            WHERE id = ?
            """,
            (
                geb_iso,
                kontakt_name or None,
                email,
                telefon or None,
                interesse or None,
                erfahrung or None,
                inklusion,
                notizen or None,
                jetzt.strftime("%Y-%m-%d %H:%M:%S"),
                jetzt.strftime("%Y-%m-%d %H:%M:%S"),
                passwort_hash,
                1 if ist_csv else 0,
                eintrag["id"],
            ),
        )
        aktualisierter_row = conn.execute(
            "SELECT * FROM warteliste_eintraege WHERE id = ?",
            (eintrag["id"],),
        ).fetchone()

    aktualisiert_dict = dict(aktualisierter_row)
    sende_anmeldebestaetigung_mit_portal_zugang(
        eintrag=aktualisiert_dict,
        tier_passwort=tier_passwort,
        portal_url=portal_url,
        aus_alter_warteliste=bool(aktualisiert_dict.get("aus_alter_warteliste_uebernommen")),
    )

    return {
        "ok": True,
        "grund": "bestaetigt",
        "eintrag": aktualisiert_dict,
        "tier_passwort": tier_passwort,
        "nachricht": "Vielen Dank! Dein Interesse wurde bestätigt und die Anmeldebestätigung mit Portal-Zugang verschickt.",
    }


def erstelle_neuen_warteliste_eintrag(daten: dict, portal_url: str) -> dict:
    """Legt einen neuen Wartelisten-Eintrag über /warteliste an und sendet die Bestätigungsmail."""
    vorname = _clean_text(daten.get("vorname"))
    nachname = _clean_text(daten.get("nachname"))
    geb_raw = _clean_text(daten.get("geburtsdatum"))
    erfahrung = _clean_text(daten.get("erfahrung"))
    interesse = _clean_text(daten.get("interesse"))
    inklusionsgruppe = 1 if (interesse == "Inklusive Klettergruppe" or daten.get("inklusionsgruppe")) else 0
    email = _clean_text(daten.get("email"))
    telefon = _clean_text(daten.get("telefon"))
    neuigkeiten_optin = 1 if daten.get("neuigkeiten_optin") else 0
    datenschutz_ok = bool(daten.get("datenschutz_bestaetigt"))

    kontakt_vorname, kontakt_nachname, kontakt_name = _baue_kontakt_namen(
        daten.get("kontakt_vorname"),
        daten.get("kontakt_nachname"),
        fallback_gesamt=daten.get("kontakt_name"),
    )

    if not vorname or not nachname:
        raise ValueError("Bitte Vor- und Nachname des Kindes angeben.")
    geb_date = _parse_geburtsdatum(geb_raw)
    if not geb_date:
        raise ValueError("Bitte ein gültiges Geburtsdatum angeben.")
    if not kontakt_vorname or not kontakt_nachname:
        raise ValueError("Bitte Vor- und Nachname der Kontaktperson angeben.")
    if not email or "@" not in email:
        raise ValueError("Bitte eine gültige E-Mail-Adresse angeben.")
    if not telefon:
        raise ValueError("Bitte eine Telefonnummer angeben.")
    if not datenschutz_ok:
        raise ValueError("Bitte bestätige die Datenschutzerklärung.")

    geb_iso = geb_date.isoformat()
    key_mit_datum = _norm_key(vorname, nachname, geb_iso)

    kompass_mit_datum, _ = _lade_bekannte_kompass_personen()
    if key_mit_datum in kompass_mit_datum:
        raise ValueError(
            f"{vorname} {nachname} ist bereits im JDAV Kompass registriert oder angemeldet."
        )

    wl_mit_datum, _ = _lade_bestehende_warteliste_keys()
    if key_mit_datum in wl_mit_datum:
        raise ValueError(
            f"Für {vorname} {nachname} (geb. {geb_date.strftime('%d.%m.%Y')}) liegt bereits ein Eintrag auf der Warteliste vor."
        )

    jetzt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tier_passwort = generiere_tier_passwort()
    passwort_hash = generate_password_hash(tier_passwort)

    with get_warteliste() as conn:
        cur = conn.execute(
            """
            INSERT INTO warteliste_eintraege (
                vorname,
                nachname,
                geburtsdatum,
                kontakt_vorname,
                kontakt_nachname,
                kontakt_name,
                email,
                telefon,
                interesse,
                erfahrung,
                inklusionsgruppe,
                neuigkeiten_optin,
                quelle,
                erstellt_am,
                bestaetigungs_status,
                bestaetigt_am,
                aktualisiert_am,
                portal_passwort_hash,
                aus_alter_warteliste_uebernommen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'formular', ?, 'bestaetigt', ?, ?, ?, 0)
            """,
            (
                vorname,
                nachname,
                geb_iso,
                kontakt_vorname,
                kontakt_nachname,
                kontakt_name,
                email,
                telefon,
                interesse or "Klettern & Bouldern",
                erfahrung or "Keine",
                inklusionsgruppe,
                neuigkeiten_optin,
                jetzt_str,
                jetzt_str,
                jetzt_str,
                passwort_hash,
            ),
        )
        eintrag_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM warteliste_eintraege WHERE id = ?",
            (eintrag_id,),
        ).fetchone()

    eintrag_dict = dict(row)
    mail_gesendet = False
    mail_hinweis = None
    try:
        sende_anmeldebestaetigung_mit_portal_zugang(
            eintrag=eintrag_dict,
            tier_passwort=tier_passwort,
            portal_url=portal_url,
            aus_alter_warteliste=False,
        )
        mail_gesendet = True
    except Exception as exc:
        mail_hinweis = str(exc)

    return {
        "eintrag": eintrag_dict,
        "tier_passwort": tier_passwort,
        "mail_gesendet": mail_gesendet,
        "mail_hinweis": mail_hinweis,
    }


def hole_warteliste_eintraege(
    status_filter: str = "",
    quelle_filter: str = "",
    nur_inklusion: bool = False,
    suchbegriff: str = "",
) -> list[dict]:
    sql = "SELECT * FROM warteliste_eintraege WHERE 1=1"
    params: list = []

    st = _clean_text(status_filter).lower()
    if st in {"ausstehend", "bestaetigt", "abgemeldet", "abgelaufen"}:
        sql += " AND bestaetigungs_status = ?"
        params.append(st)

    qu = _clean_text(quelle_filter).lower()
    if qu in {"csv_import", "formular"}:
        sql += " AND quelle = ?"
        params.append(qu)

    if nur_inklusion:
        sql += " AND COALESCE(inklusionsgruppe, 0) = 1"

    q = _clean_text(suchbegriff).lower()
    if q:
        sql += """
            AND (
                LOWER(COALESCE(vorname, '')) LIKE ?
                OR LOWER(COALESCE(nachname, '')) LIKE ?
                OR LOWER(COALESCE(kontakt_name, '')) LIKE ?
                OR LOWER(COALESCE(email, '')) LIKE ?
            )
        """
        muster = f"%{q}%"
        params.extend([muster, muster, muster, muster])

    sql += " ORDER BY datetime(erstellt_am) ASC, id ASC"

    with get_warteliste() as conn:
        rows = conn.execute(sql, params).fetchall()

    ergebnis = []
    for r in rows:
        item = dict(r)
        item["geburtsdatum_de"] = _formatiere_datum_de(item.get("geburtsdatum")) if item.get("geburtsdatum") else "-"
        item["erstellt_am_de"] = _formatiere_datum_de(item.get("erstellt_am"))
        item["frist_bis_de"] = _formatiere_datum_de(item.get("bestaetigungsfrist_bis")) if item.get("bestaetigungsfrist_bis") else "-"
        item["schnupper_eingeladen_am_de"] = (
            _formatiere_datum_de(item.get("schnupper_eingeladen_am"))
            if item.get("schnupper_eingeladen_am")
            else None
        )
        ergebnis.append(item)
    return ergebnis


def hole_passende_warteliste_kinder_fuer_gruppe(
    gruppe: dict,
    limit: int | None = None,
) -> list[dict]:
    """Liefert bestätigte Kinder chronologisch nach erstellt_am für eine Gruppe (Jahrgang ±1 oder Inklusionsgruppe)."""
    art_norm = _clean_text(gruppe.get("gruppenart")).lower()
    ist_inklusion = "inklusion" in art_norm
    ziel_jahrgang_raw = gruppe.get("jahrgang")

    with get_warteliste() as conn:
        if ist_inklusion:
            rows = conn.execute(
                """
                SELECT *
                FROM warteliste_eintraege
                WHERE bestaetigungs_status = 'bestaetigt'
                  AND COALESCE(inklusionsgruppe, 0) = 1
                ORDER BY datetime(erstellt_am) ASC, id ASC
                """
            ).fetchall()
        else:
            try:
                ziel_jahr = int(ziel_jahrgang_raw)
            except (TypeError, ValueError):
                return []
            min_jahr = ziel_jahr - 1
            max_jahr = ziel_jahr + 1
            rows = conn.execute(
                """
                SELECT *
                FROM warteliste_eintraege
                WHERE bestaetigungs_status = 'bestaetigt'
                  AND COALESCE(inklusionsgruppe, 0) = 0
                  AND geburtsdatum IS NOT NULL
                  AND CAST(substr(geburtsdatum, 1, 4) AS INTEGER) BETWEEN ? AND ?
                ORDER BY datetime(erstellt_am) ASC, id ASC
                """,
                (min_jahr, max_jahr),
            ).fetchall()

    eintraege = [dict(r) for r in rows]
    if limit is not None and limit > 0:
        eintraege = eintraege[:limit]

    for item in eintraege:
        item["geburtsdatum_de"] = _formatiere_datum_de(item.get("geburtsdatum")) if item.get("geburtsdatum") else "-"
        item["erstellt_am_de"] = _formatiere_datum_de(item.get("erstellt_am"))
        item["schnupper_eingeladen_am_de"] = (
            _formatiere_datum_de(item.get("schnupper_eingeladen_am"))
            if item.get("schnupper_eingeladen_am")
            else None
        )
    return eintraege


def lade_schnupper_kandidaten_fuer_gruppe(gruppe: dict, anzahl: int = 3) -> list[dict]:
    """Lädt die obersten N passenden bestätigten Kinder (ohne bereits versendete Schnupper-Einladung oder in Reihenfolge)."""
    alle = hole_passende_warteliste_kinder_fuer_gruppe(gruppe, limit=None)
    offene = [k for k in alle if not k.get("schnupper_eingeladen_am")]
    auswahl = offene[:anzahl] if len(offene) >= anzahl else (offene + [k for k in alle if k not in offene])[:anzahl]
    return auswahl


def sende_schnupper_einladung(eintrag_id: int, gruppe: dict) -> dict:
    """Versendet die Schnupper-Einladung an ein Wartelistenkind und vermerkt Datum & Gruppe."""
    with get_warteliste() as conn:
        row = conn.execute(
            "SELECT * FROM warteliste_eintraege WHERE id = ?",
            (int(eintrag_id),),
        ).fetchone()
    if not row:
        raise ValueError(f"Wartelisten-Eintrag {eintrag_id} nicht gefunden.")

    eintrag = dict(row)
    empfaenger = _clean_text(eintrag.get("email"))
    if not empfaenger:
        raise ValueError(f"Für {eintrag.get('vorname')} {eintrag.get('nachname')} ist keine E-Mail hinterlegt.")

    kind_name = f"{_clean_text(eintrag.get('vorname'))} {_clean_text(eintrag.get('nachname'))}".strip()
    eb_name = _clean_text(eintrag.get("kontakt_name")) or "Liebe Eltern"
    gruppe_name = _clean_text(gruppe.get("name")) or "Jugendgruppe"
    wochentag = _clean_text(gruppe.get("wochentag")) or "-"
    startzeit = _clean_text(gruppe.get("startzeit")) or "-"
    endzeit = _clean_text(gruppe.get("endzeit")) or "-"

    platzhalter = {
        "kind_name": kind_name,
        "eb_name": eb_name,
        "eb_email": empfaenger,
        "gruppe_name": gruppe_name,
        "wochentag": wochentag,
        "startzeit": startzeit,
        "endzeit": endzeit,
    }

    tpl = hole_mail_template("warteliste_schnupper_einladung")
    if tpl:
        subject = _ersetze_platzhalter(tpl["betreff"], platzhalter)
        plain_body = _ersetze_platzhalter(tpl["text_vorlage"], platzhalter)
        custom_html = _clean_text(tpl.get("html_vorlage"))
        if custom_html:
            html_body = _ersetze_platzhalter(custom_html, {k: html.escape(v) for k, v in platzhalter.items()})
        else:
            html_body = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"></head>
<body style="font-family: Inter, Arial, Helvetica, sans-serif; color: #1f2937; line-height: 1.55; max-width: 620px; margin: 0 auto; padding: 20px;">
    {_text_zu_html_absaetzen(plain_body)}
</body>
</html>
"""
    else:
        subject = f"Schnupper-Einladung Jugendgruppe JDAV Hildesheim ({kind_name})"
        plain_body = (
            f"Hallo {eb_name},\n\n"
            f"für {kind_name} ist in unserer Gruppe „{gruppe_name}“ ({wochentag}, {startzeit}–{endzeit}) "
            f"ein Schnupperplatz frei geworden!\n\n"
            f"Viele Grüße\nDein Team der JDAV Hildesheim"
        )
        html_body = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"></head>
<body style="font-family: Inter, Arial, Helvetica, sans-serif; color: #1f2937; line-height: 1.55; max-width: 620px; margin: 0 auto; padding: 20px;">
    {_text_zu_html_absaetzen(plain_body)}
</body>
</html>
"""

    _sende_mail(
        empfaenger,
        subject,
        plain_body,
        html_body,
        reply_to="warteliste@jdav-hildesheim.de",
    )

    jetzt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_warteliste() as conn:
        conn.execute(
            """
            UPDATE warteliste_eintraege
            SET schnupper_eingeladen_am = ?,
                schnupper_gruppe_id = ?
            WHERE id = ?
            """,
            (jetzt_str, int(gruppe["id"]), int(eintrag_id)),
        )
        aktualisiert = conn.execute(
            "SELECT * FROM warteliste_eintraege WHERE id = ?",
            (int(eintrag_id),),
        ).fetchone()
    return dict(aktualisiert)


def setze_warteliste_status_admin(eintrag_id: int, neuer_status: str) -> None:
    if neuer_status not in {"ausstehend", "bestaetigt", "abgemeldet", "abgelaufen"}:
        raise ValueError("Ungültiger Status.")
    jetzt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_warteliste() as conn:
        conn.execute(
            """
            UPDATE warteliste_eintraege
            SET bestaetigungs_status = ?,
                aktualisiert_am = ?
            WHERE id = ?
            """,
            (neuer_status, jetzt_str, int(eintrag_id)),
        )


def hole_warteliste_eintrag_nach_id(eintrag_id: int) -> dict | None:
    try:
        eid = int(eintrag_id)
    except (TypeError, ValueError):
        return None

    with get_warteliste() as conn:
        row = conn.execute(
            "SELECT * FROM warteliste_eintraege WHERE id = ?",
            (eid,),
        ).fetchone()
    if not row:
        return None

    item = dict(row)
    item["kind_name"] = f"{_clean_text(item.get('vorname'))} {_clean_text(item.get('nachname'))}".strip()
    item["geburtsdatum_de"] = _formatiere_datum_de(item.get("geburtsdatum")) if item.get("geburtsdatum") else "-"
    item["erstellt_am_de"] = _formatiere_datum_de(item.get("erstellt_am"))
    return item


def authentifiziere_portal_login(kind_name_eingabe: str, passwort_eingabe: str) -> dict | None:
    """Prüft Name des Kindes ('Vorname Nachname') + Tier-Passwort für den Portal-Login."""
    ziel_name = " ".join(_clean_text(kind_name_eingabe).lower().split())
    if not ziel_name or not _clean_text(passwort_eingabe):
        return None

    with get_warteliste() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM warteliste_eintraege
            WHERE bestaetigungs_status != 'abgemeldet'
              AND portal_passwort_hash IS NOT NULL
            ORDER BY id ASC
            """
        ).fetchall()

    for row in rows:
        item = dict(row)
        kombiniert = " ".join(
            f"{_clean_text(item.get('vorname'))} {_clean_text(item.get('nachname'))}".lower().split()
        )
        if kombiniert == ziel_name and verifiziere_portal_passwort(
            item.get("portal_passwort_hash"),
            passwort_eingabe,
        ):
            return hole_warteliste_eintrag_nach_id(item["id"])

    return None


def _ist_gruppe_passend_fuer_kind(gruppe: dict, eintrag: dict) -> tuple[bool, str]:
    """Prüft, ob eine Gruppe zu Jahrgang/Alter und Interesse des Kindes passt, und liefert die Gruppenart."""
    gruppenart = _clean_text(gruppe.get("gruppenart")) or "Jugendgruppe"
    art_norm = gruppenart.lower()
    interesse = _clean_text(eintrag.get("interesse"))
    ist_inklusions_interesse = (interesse == "Inklusive Klettergruppe") or bool(
        eintrag.get("inklusionsgruppe")
    )

    if ist_inklusions_interesse:
        return ("inklusion" in art_norm), gruppenart

    if interesse == "Nur Bouldern":
        return ("boulder" in art_norm), gruppenart

    # Standard: "Klettern & Bouldern" -> Jugendgruppe oder Minis anhand Ziel-Jahrgang (± 1 Jahr)
    if "inklusion" in art_norm or "boulder" in art_norm:
        return False, gruppenart

    geb_date = _parse_geburtsdatum(eintrag.get("geburtsdatum"))
    if geb_date is None:
        return False, gruppenart

    try:
        ziel_jahr = int(gruppe.get("jahrgang"))
    except (TypeError, ValueError):
        return False, gruppenart

    return (abs(geb_date.year - ziel_jahr) <= 1), gruppenart


def hole_portal_gruppen_und_positionen(eintrag: dict) -> dict:
    """Ermittelt passende Gruppen, Positionen (vor/hinter) sowie ggf. Bouldergruppen-Hinweis."""
    if not eintrag:
        return {"gruppen": [], "boulder_hinweis": None}

    interesse = _clean_text(eintrag.get("interesse"))
    vorname = _clean_text(eintrag.get("vorname")) or "dein Kind"

    with get_kompass() as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jugendgruppen'"
        ).fetchone()
        if not exists:
            return {"gruppen": [], "boulder_hinweis": None}
        gruppen_rows = conn.execute(
            "SELECT * FROM jugendgruppen ORDER BY name COLLATE NOCASE ASC, id ASC"
        ).fetchall()

    eintrag_id = int(eintrag["id"])
    blockierte_gruppen_ids = hole_blockierte_gruppen_ids(eintrag_id)
    ist_bestaetigt = _clean_text(eintrag.get("bestaetigungs_status")).lower() == "bestaetigt"
    gruppen_liste: list[dict] = []

    for g_row in gruppen_rows:
        gruppe = dict(g_row)
        passt, gruppenart = _ist_gruppe_passend_fuer_kind(gruppe, eintrag)
        if not passt:
            continue

        wartende = hole_passende_warteliste_kinder_fuer_gruppe(gruppe, limit=None)
        ids_in_liste = [int(k["id"]) for k in wartende]
        gesamt_bestaetigt = len(wartende)

        kinder_vor: int | None = None
        kinder_hinter: int | None = None
        platz_nummer: int | None = None

        if ist_bestaetigt and eintrag_id in ids_in_liste:
            idx = ids_in_liste.index(eintrag_id)
            platz_nummer = idx + 1
            kinder_vor = idx
            kinder_hinter = gesamt_bestaetigt - idx - 1
        elif ist_bestaetigt:
            kinder_vor = gesamt_bestaetigt
            kinder_hinter = 0
            platz_nummer = gesamt_bestaetigt + 1

        empfehlung = empfehle_gruppen_jahrgang(
            gruppe_id=int(gruppe["id"]),
            gruppenart=gruppenart,
        )
        ziel_jahr_raw = gruppe.get("jahrgang")
        ca_alter_text: str | None = None
        if ziel_jahr_raw is not None and str(ziel_jahr_raw).strip() != "":
            try:
                ca_alter = date.today().year - int(ziel_jahr_raw)
                if ca_alter > 0:
                    ca_alter_text = f"ca. {max(1, ca_alter - 1)}–{ca_alter + 1} Jahre"
            except (TypeError, ValueError):
                ca_alter_text = None

        if not ca_alter_text:
            empfohlen_jahrgang = empfehlung.get("empfohlen_jahrgang")
            if empfohlen_jahrgang is not None:
                ca_alter = date.today().year - int(empfohlen_jahrgang)
                if ca_alter > 0:
                    ca_alter_text = f"ca. {max(1, ca_alter - 1)}–{ca_alter + 1} Jahre"

        wochentag = _clean_text(gruppe.get("wochentag"))
        zeit_spanne = f"{_clean_text(gruppe.get('startzeit'))}–{_clean_text(gruppe.get('endzeit'))}".strip("–")
        zeit_text = ", ".join([t for t in (wochentag, zeit_spanne) if t])

        gruppen_liste.append(
            {
                "id": gruppe.get("id"),
                "name": _clean_text(gruppe.get("name")) or "Jugendgruppe",
                "gruppenart": gruppenart,
                "ca_alter_text": ca_alter_text,
                "wochentag": wochentag,
                "zeit_text": zeit_text,
                "ist_bestaetigt": ist_bestaetigt,
                "platz_nummer": platz_nummer,
                "kinder_vor": kinder_vor,
                "kinder_hinter": kinder_hinter,
                "gesamt_bestaetigt": gesamt_bestaetigt,
                "blockiert": int(gruppe.get("id")) in blockierte_gruppen_ids,
            }
        )

    boulder_hinweis = None
    if interesse == "Nur Bouldern":
        boulder_hinweis = (
            f"Hinweis: Es gibt aktuell nur wenige Bouldergruppen und die Plätze sind begehrt. "
            f"Falls {vorname} auch offen für reguläre Jugendgruppen ist, könnt ihr das Interesse unten auf "
            f"„Klettern & Bouldern“ anpassen."
        )

    return {
        "gruppen": gruppen_liste,
        "boulder_hinweis": boulder_hinweis,
    }


def aktualisiere_portal_eintrag(eintrag_id: int, daten: dict) -> dict:
    """Aktualisiert Kontakt-, Interessens- und Optionsfelder eines Eintrags im Elternportal."""
    aktuell = hole_warteliste_eintrag_nach_id(eintrag_id)
    if not aktuell:
        raise ValueError("Wartelisteneintrag wurde nicht gefunden.")
    if _clean_text(aktuell.get("bestaetigungs_status")).lower() == "abgemeldet":
        raise ValueError("Dieser Eintrag ist bereits von der Warteliste abgemeldet.")

    kontakt_vorname, kontakt_nachname, kontakt_name = _baue_kontakt_namen(
        daten.get("kontakt_vorname"),
        daten.get("kontakt_nachname"),
        fallback_gesamt=daten.get("kontakt_name") or aktuell.get("kontakt_name"),
    )
    email = _clean_text(daten.get("email"))
    telefon = _clean_text(daten.get("telefon"))
    erfahrung = _clean_text(daten.get("erfahrung")) or _clean_text(aktuell.get("erfahrung")) or "Keine"
    interesse = (
        _clean_text(daten.get("interesse"))
        or _clean_text(aktuell.get("interesse"))
        or "Klettern & Bouldern"
    )
    inklusionsgruppe = 1 if (interesse == "Inklusive Klettergruppe" or daten.get("inklusionsgruppe")) else 0
    neuigkeiten_optin = 1 if daten.get("neuigkeiten_optin") else 0
    notizen = _clean_text(daten.get("notizen"))

    if not kontakt_name:
        raise ValueError("Bitte gib den Namen der Kontaktperson an.")
    if not email or "@" not in email:
        raise ValueError("Bitte gib eine gültige E-Mail-Adresse an.")
    if not telefon:
        raise ValueError("Bitte gib eine Telefonnummer an.")

    jetzt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_warteliste() as conn:
        conn.execute(
            """
            UPDATE warteliste_eintraege
            SET kontakt_vorname = ?,
                kontakt_nachname = ?,
                kontakt_name = ?,
                email = ?,
                telefon = ?,
                erfahrung = ?,
                interesse = ?,
                inklusionsgruppe = ?,
                neuigkeiten_optin = ?,
                notizen = ?,
                aktualisiert_am = ?
            WHERE id = ?
            """,
            (
                kontakt_vorname or None,
                kontakt_nachname or None,
                kontakt_name,
                email,
                telefon,
                erfahrung,
                interesse,
                inklusionsgruppe,
                neuigkeiten_optin,
                notizen or None,
                jetzt_str,
                int(eintrag_id),
            ),
        )

    aktualisiert = hole_warteliste_eintrag_nach_id(int(eintrag_id))
    if not aktualisiert:
        raise ValueError("Aktualisierter Eintrag konnte nicht geladen werden.")
    return aktualisiert


def melde_portal_eintrag_ab(eintrag_id: int) -> dict:
    """Setzt den Status eines Wartelisteneintrags im Elternportal auf 'abgemeldet'."""
    aktuell = hole_warteliste_eintrag_nach_id(eintrag_id)
    if not aktuell:
        raise ValueError("Wartelisteneintrag wurde nicht gefunden.")

    jetzt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_warteliste() as conn:
        conn.execute(
            """
            UPDATE warteliste_eintraege
            SET bestaetigungs_status = 'abgemeldet',
                aktualisiert_am = ?
            WHERE id = ?
            """,
            (jetzt_str, int(eintrag_id)),
        )

    aktualisiert = hole_warteliste_eintrag_nach_id(int(eintrag_id))
    return aktualisiert or aktuell


def hole_blockierte_gruppen_ids(eintrag_id: int) -> set[int]:
    with get_warteliste() as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='warteliste_blockierte_gruppen'"
        ).fetchone()
        if not exists:
            return set()
        rows = conn.execute(
            "SELECT gruppe_id FROM warteliste_blockierte_gruppen WHERE eintrag_id = ?",
            (int(eintrag_id),),
        ).fetchall()
    return {int(r["gruppe_id"]) for r in rows}


def setze_gruppe_blockiert_fuer_eintrag(eintrag_id: int, gruppe_id: int, blockiert: bool) -> None:
    jetzt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_warteliste() as conn:
        conn.execute(
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
        if blockiert:
            conn.execute(
                """
                INSERT OR IGNORE INTO warteliste_blockierte_gruppen (eintrag_id, gruppe_id, erstellt_am)
                VALUES (?, ?, ?)
                """,
                (int(eintrag_id), int(gruppe_id), jetzt_str),
            )
        else:
            conn.execute(
                """
                DELETE FROM warteliste_blockierte_gruppen
                WHERE eintrag_id = ? AND gruppe_id = ?
                """,
                (int(eintrag_id), int(gruppe_id)),
            )