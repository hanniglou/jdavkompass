import html
import os
import re
import smtplib
from datetime import datetime
from email.message import EmailMessage

from app.models import get_kompass

SYSTEM_TEMPLATE_SCHLUESSEL = {"kuendigung_eingang", "kuendigung_bestaetigt"}


def hole_mail_konfiguration() -> dict:
    env_server = os.getenv("MAIL_SERVER", "smtp.ionos.de")
    env_port = int(os.getenv("MAIL_PORT", "465"))
    env_user = os.getenv("MAIL_USERNAME", "kompass@jdav-hildesheim.de")
    env_pass = os.getenv("MAIL_PASSWORD") or ""
    env_sender = os.getenv("MAIL_SENDER_NAME", "JDAV Kompass Hildesheim")

    with get_kompass() as conn:
        row = conn.execute(
            "SELECT smtp_server, smtp_port, smtp_username, smtp_password, sender_name FROM mail_einstellungen WHERE id = 1"
        ).fetchone()

    if not row:
        return {
            "smtp_server": env_server,
            "smtp_port": env_port,
            "smtp_username": env_user,
            "smtp_password": env_pass,
            "sender_name": env_sender,
            "password_from_db": False,
        }

    db_pass = (row["smtp_password"] or "").strip()
    return {
        "smtp_server": (row["smtp_server"] or "").strip() or env_server,
        "smtp_port": int(row["smtp_port"] or env_port),
        "smtp_username": (row["smtp_username"] or "").strip() or env_user,
        "smtp_password": db_pass if db_pass else env_pass,
        "sender_name": (row["sender_name"] or "").strip() or env_sender,
        "password_from_db": bool(db_pass),
    }


def speichere_mail_konfiguration(
    smtp_server: str,
    smtp_port: int,
    smtp_username: str,
    sender_name: str,
    smtp_password: str | None = None,
) -> None:
    with get_kompass() as conn:
        existing = conn.execute("SELECT smtp_password FROM mail_einstellungen WHERE id = 1").fetchone()
        neues_passwort = existing["smtp_password"] if existing else None

        # Nur überschreiben, wenn im Formular ein neues Passwort eingegeben wurde
        if smtp_password is not None and smtp_password.strip() != "":
            neues_passwort = smtp_password.strip()

        conn.execute(
            """
            INSERT INTO mail_einstellungen (
                id, smtp_server, smtp_port, smtp_username, smtp_password, sender_name
            ) VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                smtp_server = excluded.smtp_server,
                smtp_port = excluded.smtp_port,
                smtp_username = excluded.smtp_username,
                smtp_password = excluded.smtp_password,
                sender_name = excluded.sender_name;
            """,
            (
                smtp_server.strip(),
                int(smtp_port),
                smtp_username.strip(),
                neues_passwort,
                sender_name.strip(),
            ),
        )


def hole_alle_mail_templates() -> list:
    with get_kompass() as conn:
        rows = conn.execute(
            "SELECT schluessel, bezeichnung, betreff, text_vorlage, html_vorlage, aktualisiert_am FROM mail_templates ORDER BY rowid ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def hole_mail_template(schluessel: str) -> dict | None:
    with get_kompass() as conn:
        row = conn.execute(
            "SELECT schluessel, bezeichnung, betreff, text_vorlage, html_vorlage, aktualisiert_am FROM mail_templates WHERE schluessel = ?",
            (schluessel,),
        ).fetchone()
    return dict(row) if row else None


def _generiere_template_schluessel(bezeichnung: str) -> str:
    basis = bezeichnung.strip().lower()
    ersetzungen = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}
    for alt, neu in ersetzungen.items():
        basis = basis.replace(alt, neu)
    basis = re.sub(r"[^a-z0-9]+", "_", basis).strip("_")
    if not basis:
        basis = "vorlage"

    with get_kompass() as conn:
        kandidat = basis
        zaehler = 2
        while conn.execute(
            "SELECT 1 FROM mail_templates WHERE schluessel = ?", (kandidat,)
        ).fetchone():
            kandidat = f"{basis}_{zaehler}"
            zaehler += 1
    return kandidat


def erstelle_mail_template(
    bezeichnung: str,
    betreff: str,
    text_vorlage: str,
    html_vorlage: str | None = None,
    schluessel: str | None = None,
) -> str:
    ziel_schluessel = (schluessel or "").strip() or _generiere_template_schluessel(bezeichnung)
    jetzt = datetime.now().isoformat(sep=" ", timespec="seconds")
    bereinigt_html = (html_vorlage or "").strip() or None

    with get_kompass() as conn:
        conn.execute(
            """
            INSERT INTO mail_templates (
                schluessel, bezeichnung, betreff, text_vorlage, html_vorlage, aktualisiert_am
            ) VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                ziel_schluessel,
                bezeichnung.strip(),
                betreff.strip(),
                text_vorlage.strip(),
                bereinigt_html,
                jetzt,
            ),
        )
    return ziel_schluessel


def speichere_mail_template(
    schluessel: str,
    betreff: str,
    text_vorlage: str,
    html_vorlage: str | None = None,
    bezeichnung: str | None = None,
) -> None:
    jetzt = datetime.now().isoformat(sep=" ", timespec="seconds")
    bereinigt_html = (html_vorlage or "").strip() or None
    with get_kompass() as conn:
        if bezeichnung and bezeichnung.strip():
            conn.execute(
                """
                UPDATE mail_templates
                SET bezeichnung = ?, betreff = ?, text_vorlage = ?, html_vorlage = ?, aktualisiert_am = ?
                WHERE schluessel = ?;
                """,
                (
                    bezeichnung.strip(),
                    betreff.strip(),
                    text_vorlage.strip(),
                    bereinigt_html,
                    jetzt,
                    schluessel,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE mail_templates
                SET betreff = ?, text_vorlage = ?, html_vorlage = ?, aktualisiert_am = ?
                WHERE schluessel = ?;
                """,
                (betreff.strip(), text_vorlage.strip(), bereinigt_html, jetzt, schluessel),
            )


def loesche_mail_template(schluessel: str) -> bool:
    if schluessel in SYSTEM_TEMPLATE_SCHLUESSEL:
        return False
    with get_kompass() as conn:
        cur = conn.execute("DELETE FROM mail_templates WHERE schluessel = ?;", (schluessel,))
    return cur.rowcount > 0


def _ersetze_platzhalter(vorlage: str, werte: dict) -> str:
    ergebnis = vorlage
    for key, val in werte.items():
        ergebnis = ergebnis.replace(f"{{{{{key}}}}}", str(val))
    return ergebnis


def _text_zu_html_absaetzen(text: str) -> str:
    bloecke = [b.strip() for b in text.strip().split("\n\n") if b.strip()]
    html_teile = []
    for block in bloecke:
        zeilen = [html.escape(z) for z in block.splitlines()]
        html_teile.append(f"<p>{'<br>'.join(zeilen)}</p>")
    return "\n".join(html_teile)


def _sende_mail(to_addr: str, subject: str, plain_body: str, html_body: str) -> None:
    cfg = hole_mail_konfiguration()
    smtp_server = cfg["smtp_server"]
    smtp_port = cfg["smtp_port"]
    username = cfg["smtp_username"]
    password = cfg["smtp_password"]
    sender_name = cfg["sender_name"]

    if not password:
        raise RuntimeError("Kein SMTP-Passwort hinterlegt (weder in der E-Mail-Verwaltung noch in .env).")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{username}>"
    msg["To"] = to_addr
    msg["Reply-To"] = username
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=20) as server:
        server.login(username, password)
        server.send_message(msg)


def sende_test_mail(schluessel: str, empfaenger_email: str) -> str:
    tpl = hole_mail_template(schluessel)
    if not tpl:
        raise ValueError("Die ausgewählte E-Mail-Vorlage wurde nicht gefunden.")

    beispieldaten_plain = {
        "vorgangsnummer": "TEST-2026-001",
        "kind_name": "Mia Musterkind",
        "eb_name": "Alex Musterperson",
        "eb_email": empfaenger_email,
        "kuendigungsdatum": "31.12.2026",
        "kuendigungsart": "Zum Quartalsende",
        "grund": "Umzug / Testlauf aus dem Admin-Panel",
        "erstellt_am": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "dav_hinweis": (
            "\nWICHTIGER HINWEIS ZUR DAV-SEKTIONSMITGLIEDSCHAFT:\n"
            "Dies ist der automatische Hinweisblock für die DAV-Sektionsmitgliedschaft.\n"
        ),
        "freitext": "Dies ist ein individueller Zusatztext aus dem Freitext-Feld.\n\n",
    }

    beispieldaten_html = {
        **{k: html.escape(v) for k, v in beispieldaten_plain.items()},
        "dav_hinweis": (
            "<div style='margin: 16px 0; padding: 12px 14px; border-left: 4px solid #d97706; background: #fffbeb; color: #78350f;'>"
            "<strong>Wichtiger Hinweis zur DAV-Sektionsmitgliedschaft:</strong><br>"
            "Dies ist der automatische Hinweisblock für die DAV-Sektionsmitgliedschaft."
            "</div>"
        ),
        "freitext": (
            "<div style='margin: 0 0 16px 0;'>"
            "<p>Dies ist ein individueller Zusatztext aus dem Freitext-Feld.</p>"
            "</div>"
        ),
    }

    subject = f"[TEST] {_ersetze_platzhalter(tpl['betreff'], beispieldaten_plain)}"
    plain_body = _ersetze_platzhalter(tpl["text_vorlage"], beispieldaten_plain)
    custom_html = (tpl.get("html_vorlage") or "").strip()

    if custom_html:
        html_body = _ersetze_platzhalter(custom_html, beispieldaten_html)
    else:
        html_body = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, Helvetica, sans-serif; color: #1f2937; line-height: 1.5; max-width: 620px; margin: 0 auto; padding: 20px;">
    {_text_zu_html_absaetzen(plain_body)}
</body>
</html>
"""

    _sende_mail(empfaenger_email.strip(), subject, plain_body, html_body)
    return subject


def sende_kuendigungs_eingangsbestaetigung(daten: dict) -> bool:
    kind_name = f"{daten['mitglied_vorname']} {daten['mitglied_nachname']}".strip()
    eb_name = f"{daten['eb_vorname']} {daten['eb_nachname']}".strip()
    kuend_datum_de = daten["kuendigungsdatum_de"]
    vorgangsnummer = daten.get("vorgangsnummer") or str(daten.get("id", ""))
    grund_zeile = daten["grund"].strip() if daten.get("grund") else "Keine Angabe"

    dav_hinweis_text = ""
    if daten["dav_sektion_kuendigen"]:
        dav_hinweis_text = (
            "\nWICHTIGER HINWEIS ZUR DAV-SEKTIONSMITGLIEDSCHAFT:\n"
            "Du hast angegeben, ebenfalls die Mitgliedschaft in der DAV-Sektion kündigen zu wollen. "
            "Diese Kündigung über den JDAV-Kompass gilt EXPLIZIT NUR für die Jugendgruppe und NICHT als "
            "Kündigung der Mitgliedschaft in der DAV-Sektion Hildesheim. Bitte richte eine Kündigung der "
            "Sektionsmitgliedschaft separat schriftlich (postalisch oder per E-Mail an info@dav-hildesheim.de) "
            "an die Sektion. Dort können abweichende Fristen und Bedingungen gelten.\n"
        )

    platzhalter = {
        "vorgangsnummer": vorgangsnummer,
        "kind_name": kind_name,
        "eb_name": eb_name,
        "eb_email": daten["eb_email"],
        "kuendigungsdatum": kuend_datum_de,
        "kuendigungsart": daten["kuendigungsart_label"],
        "grund": grund_zeile,
        "erstellt_am": daten["erstellt_am_de"],
        "dav_hinweis": dav_hinweis_text,
    }

    tpl = hole_mail_template("kuendigung_eingang")
    if tpl:
        subject = _ersetze_platzhalter(tpl["betreff"], platzhalter)
        plain_body = _ersetze_platzhalter(tpl["text_vorlage"], platzhalter)
    else:
        subject = f"Eingangsbestätigung [#{vorgangsnummer}]: Kündigung Jugendgruppe für {kind_name} ({kuend_datum_de})"
        plain_body = (
            f"Hallo {eb_name},\n\n"
            f"wir haben Deine Kündigung (#{vorgangsnummer}) für {kind_name} zum {kuend_datum_de} erhalten.\n"
            f"{dav_hinweis_text}\nViele Grüße\nDein Team der JDAV Hildesheim"
        )

    html_body = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, Helvetica, sans-serif; color: #1f2937; line-height: 1.5; max-width: 620px; margin: 0 auto; padding: 20px;">
    {_text_zu_html_absaetzen(plain_body)}
</body>
</html>
"""

    _sende_mail(daten["eb_email"], subject, plain_body, html_body)
    return True


def hole_adressbuch_eintraege() -> list:
    with get_kompass() as conn:
        rows = conn.execute(
            "SELECT id, bezeichnung, email, aktiv FROM mail_adressbuch ORDER BY bezeichnung ASC, id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def speichere_adressbuch_eintrag(bezeichnung: str, email: str) -> None:
    with get_kompass() as conn:
        conn.execute(
            "INSERT INTO mail_adressbuch (bezeichnung, email, aktiv) VALUES (?, ?, 1);",
            (bezeichnung.strip(), email.strip()),
        )


def loesche_adressbuch_eintrag(eintrag_id: int) -> None:
    with get_kompass() as conn:
        conn.execute("DELETE FROM mail_adressbuch WHERE id = ?;", (int(eintrag_id),))


def sende_kuendigungsbestaetigung(daten: dict, freitext: str = "") -> bool:
    kind_name = f"{daten['mitglied_vorname']} {daten['mitglied_nachname']}".strip()
    eb_name = f"{daten['eb_vorname']} {daten['eb_nachname']}".strip()
    kuend_datum_de = daten["kuendigungsdatum_de"]
    vorgangsnummer = daten.get("vorgangsnummer") or str(daten.get("id", ""))
    grund_zeile = daten["grund"].strip() if daten.get("grund") else "Keine Angabe"
    freitext_clean = (freitext or "").strip()

    freitext_plain = f"{freitext_clean}\n\n" if freitext_clean else ""
    freitext_html = (
        f"<div style='margin: 0 0 16px 0;'>{_text_zu_html_absaetzen(freitext_clean)}</div>"
        if freitext_clean
        else ""
    )

    dav_hinweis_plain = ""
    dav_hinweis_html = ""
    if daten.get("dav_sektion_kuendigen"):
        dav_hinweis_plain = (
            "\nWICHTIGER HINWEIS ZUR DAV-SEKTIONSMITGLIEDSCHAFT:\n"
            "Diese Kündigung über den JDAV-Kompass gilt EXPLIZIT NUR für die Jugendgruppe und NICHT als "
            "Kündigung der Mitgliedschaft in der DAV-Sektion Hildesheim.\n"
        )
        dav_hinweis_html = (
            "<div style='margin: 16px 0; padding: 12px 14px; border-left: 4px solid #d97706; background: #fffbeb; color: #78350f;'>"
            "<strong>Wichtiger Hinweis zur DAV-Sektionsmitgliedschaft:</strong><br>"
            "Diese Kündigung über den JDAV-Kompass gilt <strong>explizit nur für die Jugendgruppe</strong> "
            "und nicht als Kündigung der Mitgliedschaft in der DAV-Sektion Hildesheim."
            "</div>"
        )

    platzhalter_plain = {
        "vorgangsnummer": vorgangsnummer,
        "kind_name": kind_name,
        "eb_name": eb_name,
        "eb_email": daten["eb_email"],
        "kuendigungsdatum": kuend_datum_de,
        "kuendigungsart": daten.get("kuendigungsart_label") or daten.get("kuendigungsart", ""),
        "grund": grund_zeile,
        "erstellt_am": daten.get("erstellt_am_de") or daten.get("erstellt_am", ""),
        "dav_hinweis": dav_hinweis_plain,
        "freitext": freitext_plain,
    }

    platzhalter_html = {
        "vorgangsnummer": html.escape(vorgangsnummer),
        "kind_name": html.escape(kind_name),
        "eb_name": html.escape(eb_name),
        "eb_email": html.escape(daten["eb_email"]),
        "kuendigungsdatum": html.escape(kuend_datum_de),
        "kuendigungsart": html.escape(daten.get("kuendigungsart_label") or daten.get("kuendigungsart", "")),
        "grund": html.escape(grund_zeile),
        "erstellt_am": html.escape(daten.get("erstellt_am_de") or daten.get("erstellt_am", "")),
        "dav_hinweis": dav_hinweis_html,
        "freitext": freitext_html,
    }

    tpl = hole_mail_template("kuendigung_bestaetigt")
    if tpl:
        subject = _ersetze_platzhalter(tpl["betreff"], platzhalter_plain)
        plain_body = _ersetze_platzhalter(tpl["text_vorlage"], platzhalter_plain)
        custom_html = (tpl.get("html_vorlage") or "").strip()
    else:
        subject = f"Bestätigung der Kündigung [#{vorgangsnummer}] für {kind_name}"
        plain_body = (
            f"Hallo {eb_name},\n\n"
            f"wir bestätigen hiermit die Kündigung (#{vorgangsnummer}) für {kind_name} zum {kuend_datum_de}.\n"
            f"{freitext_plain}{dav_hinweis_plain}\nViele Grüße"
        )
        custom_html = None

    if custom_html:
        html_body = _ersetze_platzhalter(custom_html, platzhalter_html)
    else:
        html_body = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, Helvetica, sans-serif; color: #1f2937; line-height: 1.5; max-width: 620px; margin: 0 auto; padding: 20px;">
    {_text_zu_html_absaetzen(plain_body)}
</body>
</html>
"""

    _sende_mail(daten["eb_email"], subject, plain_body, html_body)
    return True


def sende_interne_kuendigungs_info(empfaenger_liste: list[dict], daten: dict, freitext: str = "") -> int:
    if not empfaenger_liste:
        return 0

    kind_name = f"{daten['mitglied_vorname']} {daten['mitglied_nachname']}".strip()
    eb_name = f"{daten['eb_vorname']} {daten['eb_nachname']}".strip()
    vorgangsnummer = daten.get("vorgangsnummer") or str(daten.get("id", ""))
    kuend_datum_de = daten["kuendigungsdatum_de"]
    freitext_clean = (freitext or "").strip()

    subject = f"[JDAV Kompass] Kündigung bestätigt (#{vorgangsnummer}): {kind_name} zum {kuend_datum_de}"
    plain_body = (
        f"Hallo,\n\n"
        f"folgende Kündigung wurde im JDAV Kompass bestätigt und am Mitglied vermerkt:\n\n"
        f"- Vorgangsnummer: #{vorgangsnummer}\n"
        f"- Mitglied: {kind_name}\n"
        f"- Erziehungsberechtigte:r: {eb_name} ({daten['eb_email']})\n"
        f"- Kündigungsdatum: {kuend_datum_de} ({daten.get('kuendigungsart_label') or daten.get('kuendigungsart', '')})\n"
        f"- DAV-Sektion ebenfalls kündigen angegeben: {'Ja' if daten.get('dav_sektion_kuendigen') else 'Nein'}\n"
        f"- Grund: {daten.get('grund') or 'Keine Angabe'}\n"
        f"{f'- Freitext an Eltern: {freitext_clean}\n' if freitext_clean else ''}\n"
        f"Viele Grüße\nJDAV Kompass"
    )
    html_body = f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, Helvetica, sans-serif; color: #1f2937; line-height: 1.5; max-width: 620px; margin: 0 auto; padding: 20px;">
    {_text_zu_html_absaetzen(plain_body)}
</body>
</html>
"""

    gesendet = 0
    for eintrag in empfaenger_liste:
        addr = (eintrag.get("email") or "").strip()
        if addr:
            _sende_mail(addr, subject, plain_body, html_body)
            gesendet += 1
    return gesendet