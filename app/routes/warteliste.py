from datetime import date, datetime

from flask import Blueprint, flash, redirect, render_template, jsonify, request, session, url_for

from app.services.warteliste_service import (
    aktualisiere_portal_eintrag,
    authentifiziere_portal_login,
    erstelle_neuen_warteliste_eintrag,
    hole_portal_gruppen_und_positionen,
    hole_warteliste_eintrag_nach_id,
    hole_warteliste_eintrag_nach_token,
    melde_portal_eintrag_ab,
    setze_gruppe_blockiert_fuer_eintrag,
    verarbeite_alt_warteliste_antwort,
)

warteliste_bp = Blueprint("warteliste", __name__)

PORTAL_SESSION_KEY = "warteliste_portal_eintrag_id"

ERFAHRUNG_OPTIONEN = [
    "Keine",
    "Ein bisschen",
    "Ja, Toprope Kenntnisse",
    "Ja, Vorstiegs Kenntnisse",
]

INTERESSE_OPTIONEN = [
    "Klettern & Bouldern",
    "Nur Bouldern",
    "Inklusive Klettergruppe",
]


def _formatiere_datum_de_safe(raw_val: str | None) -> str:
    if not raw_val:
        return "-"
    text = str(raw_val).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return text[:10]


@warteliste_bp.route("/warteliste", methods=["GET", "POST"])
def warteliste_anmeldung():
    form_daten = {}
    if request.method == "POST":
        form_daten = {
            "vorname": (request.form.get("vorname") or "").strip(),
            "nachname": (request.form.get("nachname") or "").strip(),
            "geburtsdatum": (request.form.get("geburtsdatum") or "").strip(),
            "erfahrung": (request.form.get("erfahrung") or "").strip(),
            "interesse": (request.form.get("interesse") or "").strip(),
            "inklusionsgruppe": request.form.get("inklusionsgruppe") == "1",
            "kontakt_vorname": (request.form.get("kontakt_vorname") or "").strip(),
            "kontakt_nachname": (request.form.get("kontakt_nachname") or "").strip(),
            "email": (request.form.get("email") or "").strip(),
            "telefon": (request.form.get("telefon") or "").strip(),
            "neuigkeiten_optin": request.form.get("neuigkeiten_optin") == "1",
            "datenschutz_bestaetigt": request.form.get("datenschutz_bestaetigt") == "1",
        }

        if form_daten["erfahrung"] not in ERFAHRUNG_OPTIONEN:
            flash("Bitte wähle eine gültige Angabe zur Kletter-Erfahrung.", "error")
            return render_template(
                "warteliste_anmeldung.html",
                form_daten=form_daten,
                erfahrung_optionen=ERFAHRUNG_OPTIONEN,
                interesse_optionen=INTERESSE_OPTIONEN,
            )

        if form_daten["interesse"] not in INTERESSE_OPTIONEN:
            flash("Bitte wähle eine gültige Angabe zum Interesse.", "error")
            return render_template(
                "warteliste_anmeldung.html",
                form_daten=form_daten,
                erfahrung_optionen=ERFAHRUNG_OPTIONEN,
                interesse_optionen=INTERESSE_OPTIONEN,
            )

        try:
            portal_url = url_for("warteliste.portal", _external=True)
            ergebnis = erstelle_neuen_warteliste_eintrag(form_daten, portal_url=portal_url)
            eintrag = ergebnis["eintrag"]
            return render_template(
                "warteliste_bestaetigung.html",
                titel="Anmeldung auf der Warteliste erfolgreich",
                eintrag=eintrag,
                geburtsdatum_de=_formatiere_datum_de_safe(eintrag.get("geburtsdatum")),
                erstellt_am_de=_formatiere_datum_de_safe(eintrag.get("erstellt_am")),
                tier_passwort=ergebnis["tier_passwort"],
                mail_gesendet=ergebnis["mail_gesendet"],
                mail_hinweis=ergebnis["mail_hinweis"],
                portal_url=portal_url,
                aus_alter_warteliste=False,
            )
        except ValueError as exc:
            flash(str(exc), "error")

    return render_template(
        "warteliste_anmeldung.html",
        form_daten=form_daten,
        erfahrung_optionen=ERFAHRUNG_OPTIONEN,
        interesse_optionen=INTERESSE_OPTIONEN,
    )


@warteliste_bp.route("/warteliste/bestaetigen/<token>", methods=["GET", "POST"])
def alt_eintrag_bestaetigen(token):
    eintrag = hole_warteliste_eintrag_nach_token(token)
    if not eintrag:
        return render_template(
            "warteliste_bestaetigen.html",
            eintrag=None,
            status_meldung="Dieser Bestätigungslink ist ungültig oder existiert nicht mehr.",
            status_typ="error",
            erfahrung_optionen=ERFAHRUNG_OPTIONEN,
            interesse_optionen=INTERESSE_OPTIONEN,
        )

    if request.method == "POST":
        aktion = (request.form.get("aktion") or "").strip()
        portal_url = url_for("warteliste.portal", _external=True)
        aktualisiert = {
            "geburtsdatum": (request.form.get("geburtsdatum") or "").strip(),
            "kontakt_vorname": (request.form.get("kontakt_vorname") or "").strip(),
            "kontakt_nachname": (request.form.get("kontakt_nachname") or "").strip(),
            "email": (request.form.get("email") or "").strip(),
            "telefon": (request.form.get("telefon") or "").strip(),
            "erfahrung": (request.form.get("erfahrung") or "").strip(),
            "interesse": (request.form.get("interesse") or "").strip(),
            "inklusionsgruppe": request.form.get("inklusionsgruppe") == "1",
            "notizen": (request.form.get("notizen") or "").strip(),
        }

        try:
            res = verarbeite_alt_warteliste_antwort(
                token=token,
                aktion=aktion,
                aktualisierte_daten=aktualisiert,
                portal_url=portal_url,
            )
        except Exception as exc:
            eintrag_neu = hole_warteliste_eintrag_nach_token(token)
            if eintrag_neu and eintrag_neu.get("bestaetigungs_status") == "bestaetigt":
                return render_template(
                    "warteliste_bestaetigung.html",
                    titel="Wartelisteneintrag bestätigt",
                    eintrag=eintrag_neu,
                    geburtsdatum_de=_formatiere_datum_de_safe(eintrag_neu.get("geburtsdatum")),
                    erstellt_am_de=_formatiere_datum_de_safe(eintrag_neu.get("erstellt_am")),
                    tier_passwort=None,
                    mail_gesendet=False,
                    mail_hinweis=str(exc),
                    portal_url=portal_url,
                    aus_alter_warteliste=True,
                )
            flash(f"Fehler bei der Verarbeitung: {exc}", "error")
            return render_template(
                "warteliste_bestaetigen.html",
                eintrag=eintrag,
                geburtsdatum_de=_formatiere_datum_de_safe(eintrag.get("geburtsdatum")),
                erstellt_am_de=_formatiere_datum_de_safe(eintrag.get("erstellt_am")),
                frist_de=_formatiere_datum_de_safe(eintrag.get("bestaetigungsfrist_bis")),
                erfahrung_optionen=ERFAHRUNG_OPTIONEN,
                interesse_optionen=INTERESSE_OPTIONEN,
            )

        if not res.get("ok"):
            flash(res.get("nachricht") or "Aktion konnte nicht ausgeführt werden.", "error")
            eintrag_aktuell = hole_warteliste_eintrag_nach_token(token) or eintrag
            return render_template(
                "warteliste_bestaetigen.html",
                eintrag=eintrag_aktuell,
                geburtsdatum_de=_formatiere_datum_de_safe(eintrag_aktuell.get("geburtsdatum")),
                erstellt_am_de=_formatiere_datum_de_safe(eintrag_aktuell.get("erstellt_am")),
                frist_de=_formatiere_datum_de_safe(eintrag_aktuell.get("bestaetigungsfrist_bis")),
                erfahrung_optionen=ERFAHRUNG_OPTIONEN,
                interesse_optionen=INTERESSE_OPTIONEN,
            )

        if res.get("grund") == "abgemeldet":
            return render_template(
                "warteliste_bestaetigen.html",
                eintrag=hole_warteliste_eintrag_nach_token(token),
                status_meldung=res.get("nachricht"),
                status_typ="info",
                erfahrung_optionen=ERFAHRUNG_OPTIONEN,
                interesse_optionen=INTERESSE_OPTIONEN,
            )

        eintrag_neu = res["eintrag"]
        return render_template(
            "warteliste_bestaetigung.html",
            titel="Wartelisteneintrag bestätigt & übernommen",
            eintrag=eintrag_neu,
            geburtsdatum_de=_formatiere_datum_de_safe(eintrag_neu.get("geburtsdatum")),
            erstellt_am_de=_formatiere_datum_de_safe(eintrag_neu.get("erstellt_am")),
            tier_passwort=res.get("tier_passwort"),
            mail_gesendet=True,
            mail_hinweis=None,
            portal_url=portal_url,
            aus_alter_warteliste=True,
        )

    return render_template(
        "warteliste_bestaetigen.html",
        eintrag=eintrag,
        geburtsdatum_de=_formatiere_datum_de_safe(eintrag.get("geburtsdatum")),
        erstellt_am_de=_formatiere_datum_de_safe(eintrag.get("erstellt_am")),
        frist_de=_formatiere_datum_de_safe(eintrag.get("bestaetigungsfrist_bis")),
        erfahrung_optionen=ERFAHRUNG_OPTIONEN,
        interesse_optionen=INTERESSE_OPTIONEN,
    )


@warteliste_bp.route("/warteliste/portal", methods=["GET", "POST"])
def portal():
    session_id = session.get(PORTAL_SESSION_KEY)
    eintrag = hole_warteliste_eintrag_nach_id(session_id) if session_id else None

    if eintrag and (eintrag.get("bestaetigungs_status") or "").lower() == "abgemeldet":
        session.pop(PORTAL_SESSION_KEY, None)
        eintrag = None

    if request.method == "POST":
        aktion = (request.form.get("aktion") or "").strip()

        if aktion == "logout":
            session.pop(PORTAL_SESSION_KEY, None)
            flash("Du wurdest aus dem Wartelistenportal abgemeldet.", "info")
            return redirect(url_for("warteliste.portal"))

        if aktion == "login" or not eintrag:
            kind_name_eingabe = (request.form.get("kind_name") or "").strip()
            passwort_eingabe = (request.form.get("passwort") or "").strip()
            treffer = authentifiziere_portal_login(kind_name_eingabe, passwort_eingabe)
            if not treffer:
                flash(
                    "Name des Kindes oder Passwort ist ungültig (oder der Eintrag wurde abgemeldet).",
                    "error",
                )
                return render_template(
                    "warteliste_portal.html",
                    eintrag=None,
                    login_kind_name=kind_name_eingabe,
                    erfahrung_optionen=ERFAHRUNG_OPTIONEN,
                    interesse_optionen=INTERESSE_OPTIONEN,
                    zeige_umstellungs_hinweis=date.today() <= date(2026, 12, 25),
                )

            session[PORTAL_SESSION_KEY] = treffer["id"]
            session.permanent = True
            flash(f"Willkommen im Wartelistenportal für {treffer['kind_name']}.", "info")
            return redirect(url_for("warteliste.portal"))

        if aktion == "aktualisieren":
            aktualisierte_daten = {
                "kontakt_vorname": (request.form.get("kontakt_vorname") or "").strip(),
                "kontakt_nachname": (request.form.get("kontakt_nachname") or "").strip(),
                "email": (request.form.get("email") or "").strip(),
                "telefon": (request.form.get("telefon") or "").strip(),
                "erfahrung": (request.form.get("erfahrung") or "").strip(),
                "interesse": (request.form.get("interesse") or "").strip(),
                "inklusionsgruppe": request.form.get("inklusionsgruppe") == "1",
                "neuigkeiten_optin": request.form.get("neuigkeiten_optin") == "1",
                "notizen": (request.form.get("notizen") or "").strip(),
            }
            if aktualisierte_daten["erfahrung"] not in ERFAHRUNG_OPTIONEN:
                flash("Bitte wähle eine gültige Angabe zur Kletter-Erfahrung.", "error")
            elif aktualisierte_daten["interesse"] not in INTERESSE_OPTIONEN:
                flash("Bitte wähle eine gültige Angabe zum Interesse.", "error")
            else:
                try:
                    eintrag = aktualisiere_portal_eintrag(eintrag["id"], aktualisierte_daten)
                    flash("Die Angaben wurden erfolgreich gespeichert.", "info")
                except ValueError as exc:
                    flash(str(exc), "error")

        elif aktion == "abmelden":
            try:
                abgemeldet = melde_portal_eintrag_ab(eintrag["id"])
                session.pop(PORTAL_SESSION_KEY, None)
                return render_template(
                    "warteliste_portal.html",
                    eintrag=None,
                    abgemeldeter_eintrag=abgemeldet,
                    erfahrung_optionen=ERFAHRUNG_OPTIONEN,
                    interesse_optionen=INTERESSE_OPTIONEN,
                    zeige_umstellungs_hinweis=date.today() <= date(2026, 12, 25),
                )
            except ValueError as exc:
                flash(str(exc), "error")

    portal_daten = (
        hole_portal_gruppen_und_positionen(eintrag)
        if eintrag
        else {"gruppen": [], "boulder_hinweis": None}
    )
    return render_template(
        "warteliste_portal.html",
        eintrag=eintrag,
        gruppen_positionen=portal_daten["gruppen"],
        boulder_hinweis=portal_daten["boulder_hinweis"],
        erfahrung_optionen=ERFAHRUNG_OPTIONEN,
        interesse_optionen=INTERESSE_OPTIONEN,
        zeige_umstellungs_hinweis=date.today() <= date(2026, 12, 25),
    )


@warteliste_bp.route("/warteliste/portal/gruppe-toggle", methods=["POST"])
def portal_gruppe_toggle():
    session_id = session.get(PORTAL_SESSION_KEY)
    eintrag = hole_warteliste_eintrag_nach_id(session_id) if session_id else None
    if not eintrag or (eintrag.get("bestaetigungs_status") or "").lower() == "abgemeldet":
        return jsonify({"ok": False, "error": "Nicht eingeloggt."}), 401

    payload = request.get_json(silent=True) or {}
    raw_gruppe_id = payload.get("gruppe_id", request.form.get("gruppe_id"))
    raw_blockiert = payload.get("blockiert", request.form.get("blockiert"))

    try:
        gruppe_id = int(raw_gruppe_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Ungültige Gruppen-ID."}), 400

    blockiert = str(raw_blockiert).strip().lower() in {"1", "true", "yes", "on"} or bool(
        raw_blockiert is True
    )
    setze_gruppe_blockiert_fuer_eintrag(eintrag["id"], gruppe_id, blockiert)
    return jsonify({"ok": True, "gruppe_id": gruppe_id, "blockiert": blockiert})