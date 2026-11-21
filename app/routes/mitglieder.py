from datetime import date
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.models import get_kompass
from app.routes.auth import require_role
from app.services.admin_service import log_aenderung
from app.services.mail_service import hole_adressbuch_eintraege
from app.services.kuendigung_service import (
    KUENDIGUNG_FILTER_LABELS,
    bestaetige_kuendigung,
    get_alle_mitglieder_kurz,
    get_kuendigungen,
    verwerfe_kuendigung,
)
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


@mitglieder_bp.route("/anmeldung", methods=["GET", "POST"])
def anmeldung():
    if request.method == "POST":
        add_mitglied_to_unapproved(request.form)
        vorname = request.form.get("vorname", "").strip()
        nachname = request.form.get("nachname", "").strip()
        log_aenderung(
            "Online-Anmeldung eingegangen",
            f"{vorname} {nachname}".strip(),
            account_name="Öffentliches Formular",
        )
        return redirect(url_for("mitglieder.erfolg"))

    return render_template("anmeldung.html")


@mitglieder_bp.route("/erfolg")
def erfolg():
    return render_template("erfolg.html")


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
        bearbeiter = (
            getattr(current_user, "anzeigename", None)
            or getattr(current_user, "uname", None)
            or getattr(current_user, "username", "")
            or ""
        )

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
    bearbeiter = (
        getattr(current_user, "anzeigename", None)
        or getattr(current_user, "uname", None)
        or getattr(current_user, "username", "")
    )

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
    bearbeiter = (
        getattr(current_user, "anzeigename", None)
        or getattr(current_user, "uname", None)
        or getattr(current_user, "username", "")
    )
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