from datetime import datetime
import io
import os
import re

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import current_user, login_required
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

from app.routes.auth import require_role
from app.services.admin_service import log_aenderung
from app.services.kuendigung_service import (
    KUENDIGUNG_FILTER_LABELS,
    bestaetige_kuendigung,
    get_alle_mitglieder_kurz,
    get_kuendigungen,
    verwerfe_kuendigung,
)
from app.services.mail_service import hole_adressbuch_eintraege, sende_anmelde_bestaetigung
from app.services.mitglieder_service import (
    add_mitglied_to_unapproved,
    get_mitglied_historie,
    get_mitglieder_daten,
    get_mitglieder_und_archiv,
    get_unapproved_mitglieder_daten,
    manuelle_kuendigung_erfassen,
    setze_mitglied_archiviert,
    update_mitglied,
)

mitglieder_bp = Blueprint("mitglieder", __name__)

AUFNAHMEANTRAG_DATEINAME_VORLAGE = "Aufnahmeantrag-JDAV Kompass.pdf"


def _aktueller_bearbeiter() -> str:
    return (
        getattr(current_user, "display_name", None)
        or getattr(current_user, "uname", None)
        or getattr(current_user, "username", "")
        or ""
    ).strip()


def _ermittle_aufnahmeantrag_pfad() -> str:
    kandidaten = [
        os.path.abspath(
            os.path.join(
                current_app.root_path,
                "..",
                "uploads",
                AUFNAHMEANTRAG_DATEINAME_VORLAGE,
            )
        ),
        os.path.abspath(
            os.path.join(
                os.getcwd(),
                "uploads",
                AUFNAHMEANTRAG_DATEINAME_VORLAGE,
            )
        ),
    ]
    for pfad in kandidaten:
        if os.path.exists(pfad):
            return pfad
    return kandidaten[0]


def _bereinige_dateiname_teil(text: str) -> str:
    sauber = re.sub(r"[^A-Za-z0-9äöüÄÖÜß_-]+", "_", (text or "").strip())
    return sauber.strip("_")


def baue_aufnahmeantrag_dateiname(vorname: str, nachname: str) -> str:
    teile = [p for p in [_bereinige_dateiname_teil(vorname), _bereinige_dateiname_teil(nachname)] if p]
    if teile:
        return f"Aufnahmeantrag_DAV_Hildesheim_{'_'.join(teile)}.pdf"
    return "Aufnahmeantrag_DAV_Hildesheim.pdf"


def erstelle_aufnahmeantrag_pdf_bytes(daten: dict) -> tuple[bytes, str]:
    m_vorname = (daten.get("mitglied_vorname") or "").strip()
    m_nachname = (daten.get("mitglied_nachname") or "").strip()
    geb_raw = (daten.get("mitglied_geburtsdatum") or "").strip()
    try:
        geb_formatiert = (
            datetime.strptime(geb_raw, "%Y-%m-%d").strftime("%d.%m.%Y")
            if geb_raw
            else ""
        )
    except ValueError:
        geb_formatiert = geb_raw

    strasse_nr = " ".join(
        part for part in [(daten.get("strasse") or "").strip(), (daten.get("hausnummer") or "").strip()] if part
    )
    plz_ort = " ".join(
        part for part in [(daten.get("plz") or "").strip(), (daten.get("ort") or "").strip()] if part
    )

    eb_vorname = (daten.get("eb_vorname") or "").strip()
    eb_nachname = (daten.get("eb_nachname") or "").strip()
    eb_voll = " ".join(part for part in [eb_vorname, eb_nachname] if part)
    eb_email = (daten.get("eb_email") or "").strip()
    eb_telefon = (daten.get("eb_telefon") or "").strip()

    sepa_kontoinhaber = ((daten.get("sepa_kontoinhaber") or "").strip() or eb_voll).strip()
    sepa_kreditinstitut = (daten.get("sepa_kreditinstitut") or "").strip()
    sepa_iban = re.sub(r"\s+", "", (daten.get("sepa_iban") or "")).upper()
    sepa_bic = re.sub(r"\s+", "", (daten.get("sepa_bic") or "")).upper()

    print_gewuenscht = str(daten.get("print_zeitschriften") or "").lower() in ("1", "true", "on", "yes")
    digital_zustand = NameObject("/Off") if print_gewuenscht else NameObject("/Ja")
    off_zustand = NameObject("/Off")

    pdf_path = _ermittle_aufnahmeantrag_pfad()
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    writer.append(reader)

    feld_werte = {
        "Name Sektion": "Hildesheim",
        "Straße Hausnummer Sektion": "Lerchenkamp 52",
        "PLZ Wohnort Sektion": "31137 Hildesheim",
        "Name Sitz Sektion Sepa": "Hildesheim",
        "Name Antragsteller": m_nachname,
        "Vorname Antragsteller": m_vorname,
        "Geburtsdatum Antragsteller": geb_formatiert,
        "Straße Hausnummer Antragsteller": strasse_nr,
        "PLZ Wohnort Antragsteller": plz_ort,
        "Mobil Antragsteller": eb_telefon,
        "E-Mail Antragsteller": eb_email,
        "Gesetzlicher Vertreter": eb_voll,
        "Vorname Name Kontoinhaber": sepa_kontoinhaber,
        "Name Kreditinstitut": sepa_kreditinstitut,
        "IBAN": sepa_iban,
        "BIC": sepa_bic,
    }

    for page in writer.pages:
        writer.update_page_form_field_values(page, feld_werte, auto_regenerate=False)
        annots = page.get("/Annots")
        if annots:
            for annot_ref in annots:
                annot = annot_ref.get_object()
                feld_name = annot.get("/T")
                if feld_name in ("Check Box Panorama", "Check Box Newsletter"):
                    annot.update({
                        NameObject("/V"): digital_zustand,
                        NameObject("/AS"): digital_zustand,
                    })
                elif feld_name == "Check Box Sektionsmitteilungen":
                    annot.update({
                        NameObject("/V"): off_zustand,
                        NameObject("/AS"): off_zustand,
                    })

    ausgabe = io.BytesIO()
    writer.write(ausgabe)
    dateiname = baue_aufnahmeantrag_dateiname(m_vorname, m_nachname)
    return ausgabe.getvalue(), dateiname


def _versende_bestaetigungsmail_aus_session(
    daten: dict,
    pdf_bytes: bytes | None = None,
    pdf_filename: str | None = None,
) -> None:
    if not daten or daten.get("bestaetigungsmail_gesendet"):
        return

    sende_anmelde_bestaetigung(
        {
            "kind_name": daten.get("kind_name", ""),
            "geburtsdatum": daten.get("geburtsdatum_formatiert", ""),
            "adresse": daten.get("adresse", ""),
            "eb_name": daten.get("eb_name", ""),
            "eb_email": daten.get("eb_email", ""),
            "eb_telefon": daten.get("eb_telefon", ""),
            "bilder_erlaubnis": daten.get("bilder_erlaubnis", "Nicht erteilt"),
            "erstellt_am": daten.get("erstellt_am", datetime.now().strftime("%d.%m.%Y %H:%M")),
        },
        pdf_bytes=pdf_bytes,
        pdf_filename=pdf_filename,
    )
    daten["bestaetigungsmail_gesendet"] = True
    session["anmeldung_daten"] = daten


@mitglieder_bp.route("/anmeldung", methods=["GET", "POST"])
def anmeldung():
    if request.method == "POST":
        form_payload = request.form.to_dict()
        if not (form_payload.get("mitglied_geschlecht") or "").strip():
            form_payload["mitglied_geschlecht"] = "Keine Angabe"

        mitglied_status = (form_payload.get("dav_mitglied_status") or "bereits_mitglied").strip()
        if "mitgliedschaft_bestaetigt" not in form_payload:
            form_payload["mitgliedschaft_bestaetigt"] = (
                "1" if mitglied_status == "bereits_mitglied" else "beantragt"
            )

        add_mitglied_to_unapproved(form_payload)

        m_vorname = (form_payload.get("mitglied_vorname") or "").strip()
        m_nachname = (form_payload.get("mitglied_nachname") or "").strip()
        kind_name = " ".join(part for part in [m_vorname, m_nachname] if part)

        geb_raw = (form_payload.get("mitglied_geburtsdatum") or "").strip()
        try:
            geburtsdatum = (
                datetime.strptime(geb_raw, "%Y-%m-%d").strftime("%d.%m.%Y")
                if geb_raw
                else ""
            )
        except ValueError:
            geburtsdatum = geb_raw

        strasse = (form_payload.get("strasse") or "").strip()
        hausnummer = (form_payload.get("hausnummer") or "").strip()
        plz = (form_payload.get("plz") or "").strip()
        ort = (form_payload.get("ort") or "").strip()

        strasse_nr = " ".join(part for part in [strasse, hausnummer] if part)
        plz_ort = " ".join(part for part in [plz, ort] if part)
        adresse = ", ".join(part for part in [strasse_nr, plz_ort] if part)

        eb_vorname = (form_payload.get("eb_vorname") or "").strip()
        eb_nachname = (form_payload.get("eb_nachname") or "").strip()
        eb_name = " ".join(part for part in [eb_vorname, eb_nachname] if part)
        eb_email = (form_payload.get("eb_email") or "").strip()
        eb_telefon = (form_payload.get("eb_telefon") or "").strip()

        bilder_erlaubnis = "Erteilt" if form_payload.get("bilder_bestaetigt") else "Nicht erteilt"
        erstellt_am = datetime.now().strftime("%d.%m.%Y %H:%M")

        anmeldung_daten = {
            "mitglied_vorname": m_vorname,
            "mitglied_nachname": m_nachname,
            "kind_name": kind_name,
            "mitglied_geburtsdatum": geb_raw,
            "geburtsdatum_formatiert": geburtsdatum,
            "strasse": strasse,
            "hausnummer": hausnummer,
            "plz": plz,
            "ort": ort,
            "adresse": adresse,
            "eb_vorname": eb_vorname,
            "eb_nachname": eb_nachname,
            "eb_name": eb_name,
            "eb_email": eb_email,
            "eb_telefon": eb_telefon,
            "bilder_erlaubnis": bilder_erlaubnis,
            "erstellt_am": erstellt_am,
            "dav_mitglied_status": mitglied_status,
            "bestaetigungsmail_gesendet": False,
        }
        session["anmeldung_daten"] = anmeldung_daten

        log_aenderung(
            "Online-Anmeldung eingegangen",
            kind_name
            or f"{form_payload.get('vorname', '').strip()} {form_payload.get('nachname', '').strip()}".strip(),
            account_name="Öffentliches Formular",
        )

        if mitglied_status == "noch_kein_mitglied":
            return redirect(url_for("mitglieder.aufnahmeantrag_seite"))

        try:
            _versende_bestaetigungsmail_aus_session(anmeldung_daten)
        except Exception as mail_err:
            current_app.logger.warning("Anmelde-Bestätigungsmail fehlgeschlagen: %s", mail_err)

        return redirect(url_for("mitglieder.erfolg"))

    return render_template("anmeldung.html")


@mitglieder_bp.route("/anmeldung/aufnahmeantrag", methods=["GET"])
def aufnahmeantrag_seite():
    daten = session.get("anmeldung_daten") or {}
    return render_template("aufnahmeantrag.html", anmeldung_daten=daten)


@mitglieder_bp.route("/anmeldung/aufnahmeantrag/abschliessen", methods=["POST"])
def aufnahmeantrag_abschliessen():
    gespeichert = session.get("anmeldung_daten") or {}

    def _wert(feld_name: str) -> str:
        val = (request.form.get(feld_name) or "").strip()
        if val:
            return val
        return str(gespeichert.get(feld_name) or "").strip()

    m_vorname = _wert("mitglied_vorname")
    m_nachname = _wert("mitglied_nachname")

    pdf_daten = {
        "mitglied_vorname": m_vorname,
        "mitglied_nachname": m_nachname,
        "mitglied_geburtsdatum": _wert("mitglied_geburtsdatum"),
        "strasse": _wert("strasse"),
        "hausnummer": _wert("hausnummer"),
        "plz": _wert("plz"),
        "ort": _wert("ort"),
        "eb_vorname": _wert("eb_vorname"),
        "eb_nachname": _wert("eb_nachname"),
        "eb_email": _wert("eb_email"),
        "eb_telefon": _wert("eb_telefon"),
        "sepa_kontoinhaber": (request.form.get("sepa_kontoinhaber") or "").strip(),
        "sepa_kreditinstitut": (request.form.get("sepa_kreditinstitut") or "").strip(),
        "sepa_iban": (request.form.get("sepa_iban") or "").strip(),
        "sepa_bic": (request.form.get("sepa_bic") or "").strip(),
        "print_zeitschriften": request.form.get("print_zeitschriften"),
    }

    pdf_bytes, dateiname = erstelle_aufnahmeantrag_pdf_bytes(pdf_daten)

    try:
        _versende_bestaetigungsmail_aus_session(
            gespeichert,
            pdf_bytes=pdf_bytes,
            pdf_filename=dateiname,
        )
    except Exception as mail_err:
        current_app.logger.warning("Anmelde-Bestätigungsmail mit PDF fehlgeschlagen: %s", mail_err)

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=dateiname,
    )


@mitglieder_bp.route("/anmeldung/aufnahmeantrag.pdf", methods=["GET", "POST"])
def aufnahmeantrag_pdf():
    src = request.form if request.method == "POST" else request.args
    gespeichert = session.get("anmeldung_daten") or {}

    def _wert(feld_name: str) -> str:
        val = (src.get(feld_name) or "").strip()
        if val:
            return val
        return str(gespeichert.get(feld_name) or "").strip()

    m_vorname = _wert("mitglied_vorname")
    m_nachname = _wert("mitglied_nachname")

    pdf_bytes, dateiname = erstelle_aufnahmeantrag_pdf_bytes({
        "mitglied_vorname": m_vorname,
        "mitglied_nachname": m_nachname,
        "mitglied_geburtsdatum": _wert("mitglied_geburtsdatum"),
        "strasse": _wert("strasse"),
        "hausnummer": _wert("hausnummer"),
        "plz": _wert("plz"),
        "ort": _wert("ort"),
        "eb_vorname": _wert("eb_vorname"),
        "eb_nachname": _wert("eb_nachname"),
        "eb_email": _wert("eb_email"),
        "eb_telefon": _wert("eb_telefon"),
        "sepa_kontoinhaber": (src.get("sepa_kontoinhaber") or "").strip(),
        "sepa_kreditinstitut": (src.get("sepa_kreditinstitut") or "").strip(),
        "sepa_iban": (src.get("sepa_iban") or "").strip(),
        "sepa_bic": (src.get("sepa_bic") or "").strip(),
        "print_zeitschriften": src.get("print_zeitschriften"),
    })

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=dateiname,
    )


@mitglieder_bp.route("/erfolg")
def erfolg():
    daten = session.get("anmeldung_daten") or {}
    if daten and not daten.get("bestaetigungsmail_gesendet"):
        try:
            _versende_bestaetigungsmail_aus_session(daten)
        except Exception as mail_err:
            current_app.logger.warning("Nachgelagerte Anmelde-Bestätigungsmail fehlgeschlagen: %s", mail_err)
    return render_template("erfolg.html", anmeldung_daten=daten)


@mitglieder_bp.route("/mitglieder", methods=["GET"])
@login_required
@require_role(3)
def mitglieder():
    mitglieder_daten, mitglieder_archiv_daten = get_mitglieder_und_archiv()
    mitglieder_unapproved_daten = get_unapproved_mitglieder_daten()
    kuendigungen_ausstehend_count = len(get_kuendigungen("ausstehende"))

    return render_template(
        "mitglieder.html",
        mitglieder_daten=mitglieder_daten,
        mitglieder_archiv_daten=mitglieder_archiv_daten,
        mitglieder_unapproved_daten=mitglieder_unapproved_daten,
        kuendigungen_ausstehend_count=kuendigungen_ausstehend_count,
    )


@mitglieder_bp.route("/mitglied/<int:mitglied_id>/edit", methods=["GET", "POST"])
@login_required
@require_role(3)
def mitglied_bearbeiten(mitglied_id):
    mitglied = get_mitglieder_daten(mitglied_id=mitglied_id)
    if not mitglied:
        flash("Mitglied wurde nicht gefunden.", "error")
        return redirect(url_for("mitglieder.mitglieder"))

    if request.method == "POST":
        action = request.form.get("action", "speichern")
        bearbeiter = _aktueller_bearbeiter()

        if action == "manuelle_kuendigung":
            try:
                ergebnis = manuelle_kuendigung_erfassen(
                    mitglied_id=mitglied_id,
                    kuendigung_datum_raw=request.form.get("kuendigungsdatum", ""),
                    sende_bestaetigung=bool(request.form.get("sende_bestaetigung")),
                    adressbuch_ids_raw=request.form.getlist("adressbuch_ids"),
                    bearbeiter_name=bearbeiter,
                    freitext=(request.form.get("kuendigung_freitext") or "").strip(),
                )
                log_aenderung(
                    "Kündigung manuell erfasst",
                    f"{mitglied['vorname']} {mitglied['nachname']} (ID {mitglied_id}) "
                    f"zum {ergebnis['kuendigung_datum_de']} (Vorgang {ergebnis['vorgangsnummer']})",
                )
                msg = (
                    f"Manuelle Kündigung zum {ergebnis['kuendigung_datum_de']} "
                    f"(Vorgang {ergebnis['vorgangsnummer']}) erfasst."
                )
                if ergebnis["bestaetigung_gesendet"]:
                    msg += " Bestätigungs-Mail wurde versendet."
                if ergebnis["interne_mails_gesendet"]:
                    msg += f" {ergebnis['interne_mails_gesendet']} interne Info-Mail(s) versendet."
                flash(msg, "success")
                for mf in ergebnis.get("mail_fehler") or []:
                    flash(mf, "warning")
            except Exception as exc:
                flash(f"Fehler beim Erfassen der Kündigung: {exc}", "error")
            return redirect(url_for("mitglieder.mitglied_bearbeiten", mitglied_id=mitglied_id))

        if action in ("archivieren", "entarchivieren"):
            try:
                ziel_archiviert = action == "archivieren"
                info = setze_mitglied_archiviert(mitglied_id=mitglied_id, archiviert=ziel_archiviert)
                if ziel_archiviert:
                    log_aenderung(
                        "Mitglied direkt archiviert",
                        f"{info['vorname']} {info['nachname']} (ID {mitglied_id})",
                    )
                    flash(
                        f"{info['vorname']} {info['nachname']} wurde direkt ins Archiv verschoben.",
                        "success",
                    )
                else:
                    log_aenderung(
                        "Mitglied aus Archiv wiederhergestellt",
                        f"{info['vorname']} {info['nachname']} (ID {mitglied_id})",
                    )
                    flash(
                        f"{info['vorname']} {info['nachname']} wurde aus dem Direkt-Archiv wiederhergestellt.",
                        "success",
                    )
            except Exception as exc:
                flash(f"Fehler beim Archivieren: {exc}", "error")
            return redirect(url_for("mitglieder.mitglied_bearbeiten", mitglied_id=mitglied_id))

        update_mitglied(mitglied_id, request.form)
        vorname = request.form.get("vorname", mitglied.get("vorname", "")).strip()
        nachname = request.form.get("nachname", mitglied.get("nachname", "")).strip()
        log_aenderung(
            "Mitgliedsdaten bearbeitet",
            f"{vorname} {nachname} (ID {mitglied_id})",
        )
        flash("Änderungen am Mitglied wurden gespeichert.", "success")
        return redirect(url_for("mitglieder.mitglieder"))

    erziehungsberechtigte = mitglied.get("erziehungsberechtigte") or {}
    adressbuch_eintraege = [e for e in hole_adressbuch_eintraege() if e.get("aktiv")]
    historie_eintraege, kuendigung_info = get_mitglied_historie(mitglied_id)

    return render_template(
        "mitglied_bearbeiten.html",
        mitglied=mitglied,
        erziehungsberechtigte=erziehungsberechtigte,
        adressbuch_eintraege=adressbuch_eintraege,
        historie_eintraege=historie_eintraege,
        kuendigung_info=kuendigung_info,
    )


@mitglieder_bp.route("/mitglieder/kuendigungen", methods=["GET"])
@login_required
@require_role(3)
def kuendigungen_verwalten():
    aktueller_filter = request.args.get("filter", "ausstehende").strip()
    if aktueller_filter not in KUENDIGUNG_FILTER_LABELS:
        aktueller_filter = "ausstehende"

    kuendigungen = get_kuendigungen(aktueller_filter)
    mitglieder = get_alle_mitglieder_kurz()
    adressbuch = [e for e in hole_adressbuch_eintraege() if e.get("aktiv")]

    return render_template(
        "mitglieder_kuendigungen.html",
        kuendigungen=kuendigungen,
        mitglieder=mitglieder,
        adressbuch=adressbuch,
        aktueller_filter=aktueller_filter,
        filter_labels=KUENDIGUNG_FILTER_LABELS,
    )


@mitglieder_bp.route("/mitglieder/kuendigungen/<int:kuendigung_id>/bestaetigen", methods=["POST"])
@login_required
@require_role(3)
def kuendigung_bestaetigen_route(kuendigung_id):
    mitglied_id = request.form.get("mitglied_id")
    adressbuch_ids = request.form.getlist("adressbuch_ids")
    freitext = request.form.get("freitext", "")
    bearbeiter = _aktueller_bearbeiter()

    try:
        ergebnis = bestaetige_kuendigung(
            kuendigung_id=kuendigung_id,
            mitglied_id_raw=mitglied_id,
            adressbuch_ids_raw=adressbuch_ids,
            freitext=freitext,
            bearbeiter_name=bearbeiter,
        )
        log_aenderung(
            "Kündigung bestätigt",
            f"Vorgang #{ergebnis['vorgangsnummer']} (Kündigung-ID {kuendigung_id}, Mitglied-ID {mitglied_id or '-'})",
        )
        hinweise = []
        if ergebnis["mitglied_vermerkt"]:
            hinweise.append("in Mitglieder vermerkt")
        if ergebnis["interne_mails_gesendet"]:
            hinweise.append(f"{ergebnis['interne_mails_gesendet']} interne Info-Mail(s) gesendet")

        zusatz = f" ({', '.join(hinweise)})" if hinweise else ""
        flash(
            f"Kündigung #{ergebnis['vorgangsnummer']} bestätigt und Bestätigung versendet{zusatz}.",
            "success",
        )
    except Exception as exc:
        flash(f"Fehler beim Bestätigen der Kündigung: {exc}", "error")

    return redirect(url_for("mitglieder.kuendigungen_verwalten"))


@mitglieder_bp.route("/mitglieder/kuendigungen/<int:kuendigung_id>/verwerfen", methods=["POST"])
@login_required
@require_role(3)
def kuendigung_verwerfen_route(kuendigung_id):
    bearbeiter = _aktueller_bearbeiter()
    try:
        vorgangsnummer = verwerfe_kuendigung(
            kuendigung_id=kuendigung_id,
            bearbeiter_name=bearbeiter,
        )
        log_aenderung(
            "Kündigung verworfen",
            f"Vorgang #{vorgangsnummer} (Kündigung-ID {kuendigung_id})",
        )
        flash(
            f"Kündigung #{vorgangsnummer} wurde verworfen (keine E-Mail versendet).",
            "success",
        )
    except Exception as exc:
        flash(f"Fehler beim Verwerfen der Kündigung: {exc}", "error")

    return redirect(url_for("mitglieder.kuendigungen_verwalten"))