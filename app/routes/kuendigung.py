import calendar
from datetime import date, datetime, timedelta

from flask import Blueprint, flash, render_template, request

from app.models import get_kompass
from app.services.kuendigung_service import (
    _sicherstelle_dav_spalte,
    berechne_naechstmoegliche_dav_kuendigung,
)
from app.services.mail_service import sende_kuendigungs_eingangsbestaetigung
from extensions import limiter

kuendigung_bp = Blueprint("kuendigung", __name__)


def monatsende(d: date) -> date:
    letzter_tag = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, letzter_tag)


def generiere_vorgangsnummer(conn, zeitpunkt: datetime) -> str:
    jahr_kurz = zeitpunkt.strftime("%y")
    quartal = (zeitpunkt.month - 1) // 3 + 1
    prefix = f"{jahr_kurz}-{quartal}-"

    rows = conn.execute(
        "SELECT vorgangsnummer FROM kuendigungen WHERE vorgangsnummer LIKE ?",
        (f"{prefix}%",),
    ).fetchall()

    max_nr = 0
    for row in rows:
        vnr = row["vorgangsnummer"] or ""
        if vnr.startswith(prefix):
            teil = vnr[len(prefix):]
            if teil.isdigit():
                max_nr = max(max_nr, int(teil))

    return f"{prefix}{max_nr + 1}"


def berechne_kuendigungsoptionen(heute: date | None = None) -> dict:
    if heute is None:
        heute = date.today()

    frist_stichtag = heute + timedelta(days=28)
    fruehestmoeglich = monatsende(frist_stichtag)

    quartals_monate = [3, 6, 9, 12]
    quartalsende = None
    for jahr in (fruehestmoeglich.year, fruehestmoeglich.year + 1):
        for m in quartals_monate:
            kandidat = date(jahr, m, calendar.monthrange(jahr, m)[1])
            if kandidat >= fruehestmoeglich:
                quartalsende = kandidat
                break
        if quartalsende:
            break

    monatsenden = []
    cur_year, cur_month = fruehestmoeglich.year, fruehestmoeglich.month
    for _ in range(12):
        me = monatsende(date(cur_year, cur_month, 1))
        monatsenden.append({
            "iso": me.isoformat(),
            "de": me.strftime("%d.%m.%Y"),
        })
        if cur_month == 12:
            cur_year += 1
            cur_month = 1
        else:
            cur_month += 1

    dav_datum, dav_datum_de = berechne_naechstmoegliche_dav_kuendigung(heute)

    return {
        "heute": heute,
        "fruehestmoeglich": fruehestmoeglich,
        "fruehestmoeglich_iso": fruehestmoeglich.isoformat(),
        "fruehestmoeglich_de": fruehestmoeglich.strftime("%d.%m.%Y"),
        "quartalsende": quartalsende,
        "quartalsende_iso": quartalsende.isoformat(),
        "quartalsende_de": quartalsende.strftime("%d.%m.%Y"),
        "monatsenden": monatsenden,
        "dav_naechste_kuendigung": dav_datum,
        "dav_naechste_kuendigung_iso": dav_datum.isoformat(),
        "dav_naechste_kuendigung_de": dav_datum_de,
    }


@kuendigung_bp.route("/kuendigung", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def kuendigung():
    optionen = berechne_kuendigungsoptionen()

    if request.method == "POST":
        mitglied_vorname = request.form.get("mitglied_vorname", "").strip()
        mitglied_nachname = request.form.get("mitglied_nachname", "").strip()
        eb_vorname = request.form.get("eb_vorname", "").strip()
        eb_nachname = request.form.get("eb_nachname", "").strip()
        eb_email = request.form.get("eb_email", "").strip()

        kuendigungsart = request.form.get("kuendigungsart", "fruehestmoeglich").strip()
        eigenes_datum_str = request.form.get("eigenes_datum", "").strip()

        dav_auswahl = (request.form.get("dav_sektion_kuendigen") or "nein").strip().lower()
        dav_sektion_kuendigen = dav_auswahl in ("ja", "on", "1", "true")
        grund = request.form.get("grund", "").strip()

        datenschutz_bestaetigt = request.form.get("datenschutz_bestaetigt") == "on"
        richtigkeit_bestaetigt = request.form.get("richtigkeit_bestaetigt") == "on"
        unterschrift = request.form.get("unterschrift", "").strip()

        fehler = []
        if not all([mitglied_vorname, mitglied_nachname, eb_vorname, eb_nachname, eb_email]):
            fehler.append("Bitte fülle alle Pflichtfelder zum Kind, zu den Erziehungsberechtigten und zur E-Mail aus.")
        if not datenschutz_bestaetigt or not richtigkeit_bestaetigt:
            fehler.append("Bitte bestätige den Datenschutz sowie die Richtigkeit der Angaben.")
        if not unterschrift or not unterschrift.startswith("data:image/"):
            fehler.append("Bitte leiste eine gültige Unterschrift im Unterschriftenfeld.")

        kuendigungsdatum = None
        kuendigungsart_label = ""

        if kuendigungsart == "fruehestmoeglich":
            kuendigungsdatum = optionen["fruehestmoeglich"]
            kuendigungsart_label = "Frühestmöglich (4 Wochen zum Monatsende)"
        elif kuendigungsart == "quartalsende":
            kuendigungsdatum = optionen["quartalsende"]
            kuendigungsart_label = "Zum Quartalsende"
        elif kuendigungsart == "monatsende":
            try:
                gewaehlt = datetime.strptime(eigenes_datum_str, "%Y-%m-%d").date()
                if gewaehlt != monatsende(gewaehlt):
                    fehler.append("Das gewählte Kündigungsdatum muss auf ein Monatsende fallen.")
                elif gewaehlt < optionen["fruehestmoeglich"]:
                    fehler.append(
                        f"Wegen der vierwöchigen Kündigungsfrist zum Monatsende ist frühestens der "
                        f"{optionen['fruehestmoeglich_de']} möglich."
                    )
                else:
                    kuendigungsdatum = gewaehlt
                    kuendigungsart_label = "Zum gewählten Monatsende"
            except ValueError:
                fehler.append("Bitte wähle ein gültiges Monatsende für das Kündigungsdatum aus.")
        else:
            fehler.append("Ungültige Auswahl für das Kündigungsdatum.")

        if fehler:
            for f in fehler:
                flash(f, "error")
            return render_template(
                "kuendigung.html",
                optionen=optionen,
                form_data=request.form,
            )

        erstellt_am = datetime.now()
        kuendigungsdatum_iso = kuendigungsdatum.isoformat()
        kuendigungsdatum_de = kuendigungsdatum.strftime("%d.%m.%Y")
        erstellt_am_de = erstellt_am.strftime("%d.%m.%Y %H:%M")
        dav_kuendigungsdatum_iso = (
            optionen["dav_naechste_kuendigung_iso"] if dav_sektion_kuendigen else None
        )
        dav_kuendigungsdatum_de = (
            optionen["dav_naechste_kuendigung_de"] if dav_sektion_kuendigen else ""
        )

        with get_kompass() as conn:
            _sicherstelle_dav_spalte(conn)
            vorgangsnummer = generiere_vorgangsnummer(conn, erstellt_am)
            cursor = conn.execute(
                """
                INSERT INTO kuendigungen (
                    vorgangsnummer,
                    erstellt_am,
                    mitglied_vorname,
                    mitglied_nachname,
                    eb_vorname,
                    eb_nachname,
                    eb_email,
                    kuendigungsart,
                    kuendigungsdatum,
                    dav_sektion_kuendigen,
                    dav_kuendigungsdatum,
                    grund,
                    datenschutz_bestaetigt,
                    richtigkeit_bestaetigt,
                    unterschrift,
                    status,
                    eingangsbestaetigung_gesendet
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'eingegangen', 0)
                """,
                (
                    vorgangsnummer,
                    erstellt_am.isoformat(sep=" ", timespec="seconds"),
                    mitglied_vorname,
                    mitglied_nachname,
                    eb_vorname,
                    eb_nachname,
                    eb_email,
                    kuendigungsart,
                    kuendigungsdatum_iso,
                    1 if dav_sektion_kuendigen else 0,
                    dav_kuendigungsdatum_iso,
                    grund or None,
                    1 if datenschutz_bestaetigt else 0,
                    1 if richtigkeit_bestaetigt else 0,
                    unterschrift,
                ),
            )
            kuendigung_id = cursor.lastrowid

            mail_payload = {
                "id": kuendigung_id,
                "vorgangsnummer": vorgangsnummer,
                "erstellt_am_de": erstellt_am_de,
                "mitglied_vorname": mitglied_vorname,
                "mitglied_nachname": mitglied_nachname,
                "eb_vorname": eb_vorname,
                "eb_nachname": eb_nachname,
                "eb_email": eb_email,
                "kuendigungsart": kuendigungsart,
                "kuendigungsart_label": kuendigungsart_label,
                "kuendigungsdatum_de": kuendigungsdatum_de,
                "dav_sektion_kuendigen": dav_sektion_kuendigen,
                "dav_kuendigungsdatum_de": dav_kuendigungsdatum_de,
                "grund": grund,
            }

            mail_ok = False
            try:
                mail_ok = sende_kuendigungs_eingangsbestaetigung(mail_payload)
                if mail_ok:
                    conn.execute(
                        "UPDATE kuendigungen SET eingangsbestaetigung_gesendet = 1 WHERE id = ?",
                        (kuendigung_id,),
                    )
            except Exception as exc:
                flash(
                    f"Deine Kündigung wurde gespeichert (#{vorgangsnummer}), aber die automatische "
                    f"E-Mail konnte aktuell nicht versendet werden ({exc}). Bitte melde Dich kurz unter "
                    f"jugend@jdav-hildesheim.de.",
                    "warning",
                )

        return render_template(
            "kuendigung_bestaetigt.html",
            daten=mail_payload,
            mail_ok=mail_ok,
        )

    return render_template("kuendigung.html", optionen=optionen, form_data={})