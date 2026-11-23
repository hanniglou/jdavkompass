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


def _sende_mail(
    to_addr: str,
    subject: str,
    plain_body: str,
    html_body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
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

    for filename, content_bytes, mime_type in attachments or []:
        maintype, subtype = mime_type.split("/", 1)
        msg.add_attachment(content_bytes, maintype=maintype, subtype=subtype, filename=filename)

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
            "\nHinweis zur DAV-Sektionsmitgliedschaft:\n"
            "Diese Kündigung gilt ausschließlich für die Jugendgruppe und nicht für die Mitgliedschaft in der DAV-Sektion Hildesheim.\n"
        ),
        "freitext": "Ergänzender Hinweis aus der Kündigungsbearbeitung.\n\n",
    }

    beispieldaten_html = {
        **{k: html.escape(v) for k, v in beispieldaten_plain.items()},
        "dav_hinweis": (
            "<div style='margin: 16px 0; padding: 12px 14px; border-left: 4px solid #d97706; background: #fffbeb; color: #78350f;'>"
            "<strong>Hinweis zur DAV-Sektionsmitgliedschaft:</strong><br>"
            "Diese Kündigung gilt ausschließlich für die Jugendgruppe und nicht für die Mitgliedschaft in der DAV-Sektion Hildesheim."
            "</div>"
        ),
        "freitext": (
            "<div style='margin: 0 0 16px 0;'>"
            "<p>Ergänzender Hinweis aus der Kündigungsbearbeitung.</p>"
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


def _finde_anmelde_template() -> dict | None:
    kandidaten_schluessel = ("anmeldung_eingang", "anmeldung_bestaetigung", "anmeldung")
    for key in kandidaten_schluessel:
        tpl = hole_mail_template(key)
        if tpl:
            return tpl

    for tpl in hole_alle_mail_templates():
        schluessel = (tpl.get("schluessel") or "").lower()
        bezeichnung = (tpl.get("bezeichnung") or "").lower()
        if "anmeldung" in schluessel or "anmeldung" in bezeichnung or "eingangsbestätigung" in bezeichnung:
            return tpl

    return None


def sende_anmelde_bestaetigung(
    daten: dict,
    pdf_bytes: bytes | None = None,
    pdf_filename: str | None = None,
) -> bool:
    eb_email = (daten.get("eb_email") or "").strip()
    if not eb_email:
        return False

    kind_name = (daten.get("kind_name") or "").strip()
    eb_name = (daten.get("eb_name") or "").strip()
    geburtsdatum = (daten.get("geburtsdatum") or "").strip()
    adresse = (daten.get("adresse") or "").strip()
    eb_telefon = (daten.get("eb_telefon") or "").strip()
    bilder_erlaubnis = (daten.get("bilder_erlaubnis") or "Nicht erteilt").strip()
    erstellt_am = (daten.get("erstellt_am") or datetime.now().strftime("%d.%m.%Y %H:%M")).strip()

    platzhalter_plain = {
        "kind_name": kind_name,
        "geburtsdatum": geburtsdatum,
        "adresse": adresse,
        "eb_name": eb_name,
        "eb_email": eb_email,
        "eb_telefon": eb_telefon,
        "bilder_erlaubnis": bilder_erlaubnis,
        "erstellt_am": erstellt_am,
    }

    platzhalter_html = {k: html.escape(v) for k, v in platzhalter_plain.items()}

    tpl = _finde_anmelde_template()
    if tpl:
        subject = _ersetze_platzhalter(tpl["betreff"], platzhalter_plain)
        plain_body = _ersetze_platzhalter(tpl["text_vorlage"], platzhalter_plain)
        custom_html = (tpl.get("html_vorlage") or "").strip()
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
    else:
        subject = f"JDAV Hildesheim – Eingangsbestätigung Deiner Anmeldung für {kind_name}".strip()
        plain_body = (
            f"Hallo {eb_name},\n\n"
            f"vielen Dank für die Anmeldung bei der Jugend des Deutschen Alpenvereins (JDAV) Sektion Hildesheim! "
            f"Wir haben Eure Daten erfolgreich erhalten.\n\n"
            f"Teilnehmende Person: {kind_name}\n"
            f"Geburtsdatum: {geburtsdatum}\n"
            f"Adresse: {adresse}\n"
            f"Erziehungsberechtigte:r: {eb_name}\n"
            f"E-Mail & Notfallkontakt: {eb_email} · {eb_telefon}\n"
            f"Foto-/Video-Einwilligung: {bilder_erlaubnis}\n"
            f"Eingegangen am: {erstellt_am}\n\n"
            f"Viele Grüße\nDein Team der JDAV Hildesheim"
        )
        html_vorlage = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&display=swap" rel="stylesheet">
</head>
<body style="margin: 0; padding: 24px; background-color: #f3f4f6; font-family: 'Source Sans 3', 'Source Sans Pro', Arial, Helvetica, sans-serif; color: #1f2937;">
  <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden;">
    <div style="background: #14532d; color: #ffffff; padding: 16px 22px;">
      <h2 style="margin: 0; font-size: 18px; font-weight: 600; font-family: 'Source Sans 3', 'Source Sans Pro', Arial, Helvetica, sans-serif;">
        JDAV Hildesheim – Eingangsbestätigung Deiner Anmeldung
      </h2>
    </div>
    <div style="padding: 22px; line-height: 1.55; font-size: 15px;">
      <p style="margin-top: 0;">Hallo {{eb_name}},</p>
      <p>vielen Dank für die Anmeldung bei der Jugend des Deutschen Alpenvereins (JDAV) Sektion Hildesheim! Wir haben Eure Daten erfolgreich erhalten.</p>
      <table style="width: 100%; border-collapse: collapse; margin: 16px 0; background-color: #f9fafb; border: 1px solid #e5e7eb; font-size: 14px;">
        <tr><td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb; font-weight: 600; width: 42%;">Teilnehmende Person</td><td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb;"><strong>{{kind_name}}</strong></td></tr>
        <tr><td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">Geburtsdatum</td><td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb;">{{geburtsdatum}}</td></tr>
        <tr><td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">Adresse</td><td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb;">{{adresse}}</td></tr>
        <tr><td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">Erziehungsberechtigte:r</td><td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb;">{{eb_name}}</td></tr>
        <tr><td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">E-Mail &amp; Notfallkontakt</td><td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb;">{{eb_email}} · {{eb_telefon}}</td></tr>
        <tr><td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb; font-weight: 600;">Foto-/Video-Einwilligung</td><td style="padding: 8px 12px; border-bottom: 1px solid #e5e7eb;">{{bilder_erlaubnis}}</td></tr>
        <tr><td style="padding: 8px 12px; font-weight: 600;">Eingegangen am</td><td style="padding: 8px 12px;">{{erstellt_am}}</td></tr>
      </table>
      <p style="font-size: 13.5px; color: #4b5563; margin-top: 22px; margin-bottom: 0; border-top: 1px solid #e5e7eb; padding-top: 12px;">
        Viele Grüße<br><strong style="color: #1f2937;">Dein Team der JDAV Hildesheim</strong>
      </p>
    </div>
  </div>
</body>
</html>"""
        html_body = _ersetze_platzhalter(html_vorlage, platzhalter_html)

    attachments = []
    if pdf_bytes:
        attachments.append((
            pdf_filename or "Aufnahmeantrag_DAV_Hildesheim.pdf",
            pdf_bytes,
            "application/pdf",
        ))

    _sende_mail(eb_email, subject, plain_body, html_body, attachments=attachments)
    return True


def sende_kuendigungs_eingangsbestaetigung(daten: dict) -> bool:
    kind_name = f"{daten['mitglied_vorname']} {daten['mitglied_nachname']}".strip()
    eb_name = f"{daten['eb_vorname']} {daten['eb_nachname']}".strip()
    kuend_datum_de = daten["kuendigungsdatum_de"]
    vorgangsnummer = daten.get("vorgangsnummer") or str(daten.get("id", ""))
    grund_zeile = daten["grund"].strip() if daten.get("grund") else "Keine Angabe"

    dav_hinweis_text = ""
    if daten["dav_sektion_kuendigen"]:
        dav_hinweis_text = (
            "\nHinweis zur DAV-Sektionsmitgliedschaft:\n"
            "Diese Kündigung gilt ausschließlich für die Jugendgruppe der JDAV Hildesheim und nicht "
            "für die Mitgliedschaft in der DAV-Sektion Hildesheim. Bitte richte eine Kündigung der "
            "Sektionsmitgliedschaft bei Bedarf separat schriftlich (z. B. per E-Mail an "
            "info@dav-hildesheim.de) an die Sektion.\n"
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
        subject = f"Eingang der Kündigung [#{vorgangsnummer}] für {kind_name} zum {kuend_datum_de}"
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
            "\nHinweis zur DAV-Sektionsmitgliedschaft:\n"
            "Diese Kündigung gilt ausschließlich für die Jugendgruppe der JDAV Hildesheim und nicht "
            "für die Mitgliedschaft in der DAV-Sektion Hildesheim.\n"
        )
        dav_hinweis_html = (
            "<div style='margin: 16px 0; padding: 12px 14px; border-left: 4px solid #d97706; background: #fffbeb; color: #78350f;'>"
            "<strong>Hinweis zur DAV-Sektionsmitgliedschaft:</strong><br>"
            "Diese Kündigung gilt <strong>ausschließlich für die Jugendgruppe</strong> der JDAV Hildesheim "
            "und nicht für die Mitgliedschaft in der DAV-Sektion Hildesheim."
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
            f"wir bestätigen die Kündigung (#{vorgangsnummer}) für {kind_name} zum {kuend_datum_de}.\n"
            f"{freitext_plain}{dav_hinweis_plain}\nViele Grüße\nDein Team der JDAV Hildesheim"
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
        f"- Gesonderte Kündigung DAV-Sektion angegeben: {'Ja' if daten.get('dav_sektion_kuendigen') else 'Nein'}\n"
        f"- Grund: {daten.get('grund') or 'Keine Angabe'}\n"
        f"{f'- Ergänzender Hinweis an Eltern: {freitext_clean}\n' if freitext_clean else ''}\n"
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