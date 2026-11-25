from io import BytesIO
import zipfile

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import login_required

from app.models import get_kompass
from app.routes.auth import require_role
from app.services.admin_service import (
    change_account_role,
    delete_account,
    get_all_accounts,
    get_all_gruppenleiter,
    get_gruppenleiter,
    hole_aenderungs_log,
    hole_einstellungen,
    loesche_gruppenart,
    log_aenderung,
    save_gruppenleiter,
    speichere_beitrags_rahmen,
    speichere_einstellungen,
    speichere_gruppenart,
    update_account_status,
)
from app.services.gruppe_service import (
    baue_anwesenheits_excel,
    hole_teilnehmende_und_quartalsende,
)
from app.services.warteliste_service import (
    hole_passende_warteliste_kinder_fuer_gruppe,
    hole_warteliste_eintraege,
    lade_schnupper_kandidaten_fuer_gruppe,
    sende_schnupper_einladung,
    setze_warteliste_status_admin,
)
from app.services.gruppen_service import loesche_gruppe, neue_gruppe_erstellen
from app.services.mail_service import (
    SYSTEM_TEMPLATE_SCHLUESSEL,
    aktualisiere_adressbuch_eintrag,
    erstelle_mail_template,
    hole_adressbuch_eintraege,
    hole_alle_mail_templates,
    hole_mail_konfiguration,
    ist_mail_versand_aktiv,
    loesche_adressbuch_eintrag,
    loesche_mail_template,
    sende_test_mail,
    speichere_adressbuch_eintrag,
    speichere_mail_konfiguration,
    speichere_mail_template,
)
from app.services.utils import jugendgruppen_preview


admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin", methods=["GET"])
@login_required
@require_role(4)
def admin():
    accounts = get_all_accounts()
    juleis = get_all_gruppenleiter()
    offene_accounts = sum(1 for a in accounts if a["status"] == "pending")
    log_eintraege = hole_aenderungs_log(limit=10, nur_audit=True)
    return render_template(
        "admin/dashboard.html",
        active_tab="overview",
        accounts_count=len(accounts),
        offene_accounts_count=offene_accounts,
        juleis_count=len(juleis),
        log_eintraege=log_eintraege,
    )


@admin_bp.route("/admin/log", methods=["GET"])
@login_required
@require_role(4)
def admin_log():
    zeige_alle = request.args.get("alle") == "1"
    raw_limit = request.args.get("limit", "50").strip().lower()

    if raw_limit == "alle":
        log_limit = None
        aktives_limit = "alle"
    elif raw_limit in {"50", "100", "1000"}:
        log_limit = int(raw_limit)
        aktives_limit = raw_limit
    else:
        log_limit = 50
        aktives_limit = "50"

    return render_template(
        "admin/log.html",
        active_tab="log",
        log_eintraege=hole_aenderungs_log(limit=log_limit, nur_audit=not zeige_alle),
        zeige_alle=zeige_alle,
        aktives_limit=aktives_limit,
    )


@admin_bp.route("/admin/accounts", methods=["GET", "POST"])
@login_required
@require_role(4)
def admin_accounts():
    if request.method == "POST":
        account_id = request.form.get("account_id")
        action = request.form.get("action")
        new_role = request.form.get("new_role")

        if action == "approve":
            update_account_status(account_id, "active")
            log_aenderung("Account freigeschaltet", f"Account-ID {account_id}")
            flash("Account wurde freigeschaltet.", "success")
        elif action == "delete":
            delete_account(account_id)
            log_aenderung("Account gelöscht", f"Account-ID {account_id}")
            flash("Account wurde gelöscht.", "success")
        elif action == "change_role" and new_role in {"1", "2", "3", "4"}:
            change_account_role(account_id, new_role)
            log_aenderung("Account-Rolle geändert", f"Account-ID {account_id} -> Rolle {new_role}")
            flash("Rolle wurde aktualisiert.", "success")

        return redirect(url_for("admin.admin_accounts"))

    return render_template(
        "admin/accounts.html",
        active_tab="accounts",
        accounts=get_all_accounts(),
    )


@admin_bp.route("/admin/juleis", methods=["GET"])
@login_required
@require_role(4)
def admin_juleis():
    return render_template(
        "admin/juleis.html",
        active_tab="juleis",
        gruppenleiter=get_all_gruppenleiter(),
        accounts=get_all_accounts(),
    )


@admin_bp.route("/admin/gruppen", methods=["GET", "POST"])
@login_required
@require_role(4)
def admin_gruppen():
    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "delete":
            gruppe_id_raw = request.form.get("gruppe_id", "").strip()
            try:
                gruppe_id = int(gruppe_id_raw)
                loesche_gruppe(gruppe_id)
                log_aenderung("Gruppe gelöscht", f"Gruppen-ID {gruppe_id}")
                flash("Gruppe wurde gelöscht.", "success")
            except (TypeError, ValueError):
                flash("Ungültige Gruppen-ID.", "error")

        elif action == "save_gruppenart":
            try:
                ga_id_raw = request.form.get("gruppenart_id", "").strip()
                ga_id = int(ga_id_raw) if ga_id_raw else None
                name = request.form.get("ga_name", "").strip()
                dauer = int(request.form.get("ga_dauer", "0"))
                schluessel = int(request.form.get("ga_schluessel", "0"))
                geld = request.form.get("ga_betreuungsgeld", "").strip()
                speichere_gruppenart(name, dauer, schluessel, geld, gruppenart_id=ga_id)
                log_aenderung(
                    "Gruppenart gespeichert",
                    f"{name} ({dauer} Min, 1:{schluessel}, {geld} €/Monat)",
                )
                flash(f"Gruppenart „{name}“ wurde gespeichert.", "success")
            except Exception as exc:
                flash(f"Fehler bei Gruppenart: {exc}", "error")

        elif action == "delete_gruppenart":
            try:
                ga_id = int(request.form.get("gruppenart_id", "").strip())
                loesche_gruppenart(ga_id)
                log_aenderung("Gruppenart gelöscht", f"Gruppenart-ID {ga_id}")
                flash("Gruppenart wurde gelöscht.", "success")
            except (TypeError, ValueError):
                flash("Ungültige Gruppenart-ID.", "error")

        elif action == "save_beitrags_rahmen":
            speichere_beitrags_rahmen(
                betreuungsgeld_gueltig_ab=request.form.get("anmeldung_betreuungsgeld_gueltig_ab", ""),
                eintritt_gueltig_ab=request.form.get("anmeldung_eintritt_gueltig_ab", ""),
                eintritt_unter_14=request.form.get("anmeldung_eintritt_unter_14", ""),
                eintritt_ab_14=request.form.get("anmeldung_eintritt_ab_14", ""),
                eintritt_hinweis=request.form.get("anmeldung_eintritt_hinweis", ""),
            )
            log_aenderung(
                "Eintrittspreise & Gültigkeiten aktualisiert",
                "Rahmendaten für /anmeldung bearbeitet",
            )
            flash("Eintrittspreise und Gültigkeitsdaten wurden gespeichert.", "success")

        else:
            name = request.form.get("name", "").strip()
            wochentag = request.form.get("wochentag", "").strip()
            startzeit = request.form.get("startzeit", "").strip()
            endzeit = request.form.get("endzeit", "").strip()
            if not name or not wochentag or not startzeit or not endzeit:
                flash("Bitte Name, Wochentag, Startzeit und Endzeit angeben.", "error")
            else:
                neue_gruppe_erstellen(request.form)
                log_aenderung("Neue Gruppe erstellt", f"{name} ({wochentag}, {startzeit}–{endzeit})")
                flash(f"Gruppe „{name}“ wurde angelegt.", "success")

        return redirect(url_for("admin.admin_gruppen"))

    return render_template(
        "admin/gruppenverwaltung.html",
        active_tab="gruppen",
        gruppen=jugendgruppen_preview(),
        einstellungen=hole_einstellungen(),
    )


@admin_bp.route("/gruppenleiter/add", methods=["POST"])
@login_required
@require_role(4)
def add_gruppenleiter():
    save_gruppenleiter(request.form, None)
    vorname = request.form.get("vorname", "").strip()
    nachname = request.form.get("nachname", "").strip()
    log_aenderung("Jugendleiter:in hinzugefügt", f"{vorname} {nachname}".strip())
    flash("Jugendleiter:in wurde hinzugefügt.", "success")
    return redirect(url_for("admin.admin_juleis"))


@admin_bp.route("/gruppenleiter/<int:gruppenleiter_id>/edit", methods=["GET", "POST"])
@login_required
@require_role(4)
def gruppenleiter_bearbeiten(gruppenleiter_id):
    if request.method == "POST":
        save_gruppenleiter(request.form, gruppenleiter_id)
        vorname = request.form.get("vorname", "").strip()
        nachname = request.form.get("nachname", "").strip()
        log_aenderung(
            "Jugendleiter:in bearbeitet",
            f"{vorname} {nachname} (ID {gruppenleiter_id})".strip(),
        )
        flash("Angaben der Jugendleiter:in wurden gespeichert.", "success")
        return redirect(url_for("admin.admin_juleis"))

    gruppenleiter = get_gruppenleiter(gruppenleiter_id)
    return render_template(
        "gruppenleiter_bearbeiten.html",
        leiter=gruppenleiter,
        accounts=get_all_accounts(),
    )


@admin_bp.route("/admin/email", methods=["GET", "POST"])
@login_required
@require_role(4)
def admin_email():
    aktiver_schluessel = request.args.get("tpl", "").strip()

    if request.method == "POST":
        form_typ = request.form.get("form_typ")
        aktiver_schluessel = request.form.get("schluessel", aktiver_schluessel).strip()

        if form_typ == "template_create":
            bezeichnung = request.form.get("bezeichnung", "").strip()
            betreff = request.form.get("betreff", "").strip()
            text_vorlage = request.form.get("text_vorlage", "").strip()
            html_vorlage = request.form.get("html_vorlage", "")

            if bezeichnung and betreff and text_vorlage:
                neuer_schluessel = erstelle_mail_template(
                    bezeichnung=bezeichnung,
                    betreff=betreff,
                    text_vorlage=text_vorlage,
                    html_vorlage=html_vorlage,
                )
                log_aenderung("E-Mail-Vorlage erstellt", f"{bezeichnung} ({neuer_schluessel})")
                flash(f"Neue Vorlage „{bezeichnung}“ wurde angelegt.", "success")
                return redirect(url_for("admin.admin_email", tpl=neuer_schluessel))
            else:
                flash("Bezeichnung, Betreff und Klartext-Vorlage dürfen nicht leer sein.", "error")

        elif form_typ == "mail_switch":
            aktiv_wert = "1" if request.form.get("mail_versand_aktiv") == "1" else "0"
            speichere_einstellungen({"mail_versand_aktiv": aktiv_wert})
            zustand_text = "aktiviert" if aktiv_wert == "1" else "deaktiviert (Sperre aktiv)"
            log_aenderung("E-Mail-Versand-Schalter geändert", f"Status: {zustand_text}")
            flash(f"E-Mail-Versand wurde {zustand_text}.", "success")
            return redirect(url_for("admin.admin_email", tpl=aktiver_schluessel))

        elif form_typ == "template":
            schluessel = request.form.get("schluessel", "").strip()
            bezeichnung = request.form.get("bezeichnung", "").strip()
            betreff = request.form.get("betreff", "").strip()
            text_vorlage = request.form.get("text_vorlage", "").strip()
            html_vorlage = request.form.get("html_vorlage", "")
            if schluessel and betreff and text_vorlage:
                speichere_mail_template(
                    schluessel=schluessel,
                    betreff=betreff,
                    text_vorlage=text_vorlage,
                    html_vorlage=html_vorlage,
                    bezeichnung=bezeichnung,
                )
                log_aenderung("E-Mail-Vorlage gespeichert", f"Schlüssel: {schluessel}")
                flash("E-Mail-Vorlage wurde gespeichert.", "success")
                return redirect(url_for("admin.admin_email", tpl=schluessel))
            else:
                flash("Betreff und Klartext-Vorlage dürfen nicht leer sein.", "error")

        elif form_typ == "template_delete":
            schluessel = request.form.get("schluessel", "").strip()
            if loesche_mail_template(schluessel):
                log_aenderung("E-Mail-Vorlage gelöscht", f"Schlüssel: {schluessel}")
                flash("E-Mail-Vorlage wurde gelöscht.", "success")
                return redirect(url_for("admin.admin_email"))
            else:
                flash("System-Vorlagen können nicht gelöscht werden.", "error")

        elif form_typ == "smtp":
            smtp_server = request.form.get("smtp_server", "").strip()
            smtp_port_raw = request.form.get("smtp_port", "465").strip()
            smtp_username = request.form.get("smtp_username", "").strip()
            smtp_password = request.form.get("smtp_password", "")
            sender_name = request.form.get("sender_name", "").strip()

            try:
                smtp_port = int(smtp_port_raw)
                if not smtp_server or not smtp_username:
                    flash("SMTP-Server und Benutzername dürfen nicht leer sein.", "error")
                else:
                    speichere_mail_konfiguration(
                        smtp_server=smtp_server,
                        smtp_port=smtp_port,
                        smtp_username=smtp_username,
                        sender_name=sender_name or "JDAV Kompass Hildesheim",
                        smtp_password=smtp_password,
                    )
                    log_aenderung("SMTP-Einstellungen gespeichert", f"Server: {smtp_server}")
                    flash("SMTP-Einstellungen wurden gespeichert.", "success")
            except ValueError:
                flash("Bitte einen gültigen SMTP-Port (Zahl) eingeben.", "error")

        elif form_typ == "test_mail":
            test_schluessel = request.form.get("test_schluessel", "").strip()
            empfaenger_email = request.form.get("empfaenger_email", "").strip()
            if not test_schluessel or not empfaenger_email:
                flash("Bitte eine Vorlage und eine Empfänger-Adresse auswählen.", "error")
            else:
                try:
                    betreff_gesendet = sende_test_mail(test_schluessel, empfaenger_email)
                    log_aenderung("Test-E-Mail versendet", f"{betreff_gesendet} -> {empfaenger_email}")
                    flash(f"Test-E-Mail wurde erfolgreich an {empfaenger_email} gesendet.", "success")
                except Exception as exc:
                    flash(f"Fehler beim Test-Mail-Versand: {exc}", "error")
            return redirect(url_for("admin.admin_email", tpl=test_schluessel or aktiver_schluessel))

        elif form_typ == "adressbuch_add":
            funktion = (request.form.get("funktion") or request.form.get("bezeichnung") or "").strip()
            kontakt_name = request.form.get("name", "").strip()
            email_adresse = request.form.get("email", "").strip()
            if funktion and email_adresse:
                speichere_adressbuch_eintrag(funktion=funktion, email=email_adresse, name=kontakt_name)
                log_aenderung(
                    "Adressbuch-Eintrag hinzugefügt",
                    f"{funktion} – {kontakt_name} ({email_adresse})" if kontakt_name else f"{funktion} ({email_adresse})",
                )
                flash("Adressbuch-Eintrag wurde hinzugefügt.", "success")
            else:
                flash("Bitte sowohl eine Funktion als auch eine E-Mail-Adresse angeben.", "error")

        elif form_typ == "adressbuch_edit":
            eintrag_id_raw = request.form.get("eintrag_id", "").strip()
            funktion = (request.form.get("funktion") or request.form.get("bezeichnung") or "").strip()
            kontakt_name = request.form.get("name", "").strip()
            email_adresse = request.form.get("email", "").strip()
            try:
                eintrag_id = int(eintrag_id_raw)
                if not funktion or not email_adresse:
                    flash("Bitte sowohl eine Funktion als auch eine E-Mail-Adresse angeben.", "error")
                else:
                    aktualisiere_adressbuch_eintrag(
                        eintrag_id=eintrag_id,
                        funktion=funktion,
                        email=email_adresse,
                        name=kontakt_name,
                    )
                    log_aenderung(
                        "Adressbuch-Eintrag bearbeitet",
                        f"{funktion} – {kontakt_name} ({email_adresse})" if kontakt_name else f"{funktion} ({email_adresse})",
                    )
                    flash("Adressbuch-Eintrag wurde aktualisiert.", "success")
            except (TypeError, ValueError):
                flash("Ungültiger Adressbuch-Eintrag.", "error")

        elif form_typ == "adressbuch_delete":
            eintrag_id_raw = request.form.get("eintrag_id", "").strip()
            try:
                loesche_adressbuch_eintrag(int(eintrag_id_raw))
                log_aenderung("Adressbuch-Eintrag entfernt", f"Eintrag-ID {eintrag_id_raw}")
                flash("Adressbuch-Eintrag wurde entfernt.", "success")
            except (TypeError, ValueError):
                flash("Ungültiger Adressbuch-Eintrag.", "error")

        return redirect(url_for("admin.admin_email", tpl=aktiver_schluessel))

    templates = hole_alle_mail_templates()
    gewaehltes_template = next(
        (t for t in templates if t["schluessel"] == aktiver_schluessel),
        templates[0] if templates else None,
    )

    return render_template(
        "admin/email.html",
        active_tab="email",
        mail_cfg=hole_mail_konfiguration(),
        templates=templates,
        gewaehltes_template=gewaehltes_template,
        system_schluessel=SYSTEM_TEMPLATE_SCHLUESSEL,
        adressbuch=hole_adressbuch_eintraege(),
        mail_versand_aktiv=ist_mail_versand_aktiv(),
    )


@admin_bp.route("/admin/export", methods=["GET", "POST"])
@login_required
@require_role(4)
def admin_export():
    with get_kompass() as conn:
        gruppen_rows = conn.execute(
            """
            SELECT id, name, wochentag, startzeit, endzeit
            FROM jugendgruppen
            ORDER BY name ASC, id ASC
            """
        ).fetchall()
    gruppen = [dict(r) for r in gruppen_rows]

    if request.method == "POST":
        ausgewaehlte_ids = []
        for raw_id in request.form.getlist("gruppe_ids"):
            try:
                ausgewaehlte_ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue

        if not ausgewaehlte_ids:
            flash("Bitte mindestens eine Gruppe für den Export auswählen.", "error")
            return redirect(url_for("admin.admin_export"))

        log_aenderung(
            "Anwesenheitslisten exportiert",
            f"Gruppen-IDs: {', '.join(str(i) for i in ausgewaehlte_ids)}",
        )

        if len(ausgewaehlte_ids) == 1:
            excel_buf, dateiname = baue_anwesenheits_excel(ausgewaehlte_ids[0])
            return send_file(
                excel_buf,
                as_attachment=True,
                download_name=dateiname,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for gid in ausgewaehlte_ids:
                excel_buf, dateiname = baue_anwesenheits_excel(gid)
                zf.writestr(dateiname, excel_buf.getvalue())

        zip_buffer.seek(0)
        return send_file(
            zip_buffer,
            as_attachment=True,
            download_name="anwesenheitslisten_export.zip",
            mimetype="application/zip",
        )

    return render_template(
        "admin/export.html",
        active_tab="export",
        gruppen=gruppen,
    )


@admin_bp.route("/admin/texte", methods=["GET", "POST"])
@login_required
@require_role(4)
def admin_texte():
    if request.method == "POST":
        speichere_einstellungen(
            {
                "footer_text": request.form.get("footer_text", ""),
                "footer_extern_text": request.form.get("footer_extern_text", ""),
                "anmeldung_intro_text": request.form.get("anmeldung_intro_text", ""),
                "anmeldung_beitraege_intro": request.form.get("anmeldung_beitraege_intro", ""),
                "anmeldung_beitraege_details": request.form.get("anmeldung_beitraege_details", ""),
            }
        )
        log_aenderung("Texte & Footer aktualisiert", "Footer und Anmeldungstexte bearbeitet")
        flash("Texte und Footer wurden gespeichert.", "success")
        return redirect(url_for("admin.admin_texte"))

    return render_template(
        "admin/texte.html",
        active_tab="texte",
        texte=hole_einstellungen(),
    )


@admin_bp.route("/admin/warteliste", methods=["GET", "POST"])
@login_required
@require_role(4)
def admin_warteliste():
    status_filter = (request.args.get("status") or "bestaetigt").strip()
    quelle_filter = (request.args.get("quelle") or "").strip()
    nur_inklusion = request.args.get("inklusion") == "1"
    suchbegriff = (request.args.get("q") or "").strip()
    gruppe_id_raw = (request.args.get("gruppe_id") or "").strip()

    with get_kompass() as conn:
        gruppen_rows = conn.execute(
            """
            SELECT id, name, wochentag, startzeit, endzeit, gruppenart, max_teilnehmer, jahrgang
            FROM jugendgruppen
            ORDER BY name ASC, id ASC
            """
        ).fetchall()
    gruppen = [dict(r) for r in gruppen_rows]

    for g in gruppen:
        _, anzahl_aktuell, anzahl_quartalsende = hole_teilnehmende_und_quartalsende(g["id"])
        max_tn = int(g.get("max_teilnehmer") or 12)
        g["max_teilnehmer"] = max_tn
        g["aktuell"] = anzahl_aktuell
        g["quartalsende"] = anzahl_quartalsende
        g["frei_aktuell"] = max(0, max_tn - anzahl_aktuell)
        g["frei_quartalsende"] = max(0, max_tn - anzahl_quartalsende)

    gewaehlte_gruppe = None
    if gruppe_id_raw:
        try:
            gid = int(gruppe_id_raw)
            gewaehlte_gruppe = next((g for g in gruppen if g["id"] == gid), None)
        except ValueError:
            gewaehlte_gruppe = None
    if gewaehlte_gruppe is None and gruppen:
        gewaehlte_gruppe = gruppen[0]

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "set_status":
            try:
                eid = int(request.form.get("eintrag_id", "").strip())
                neuer_status = (request.form.get("neuer_status") or "").strip()
                setze_warteliste_status_admin(eid, neuer_status)
                log_aenderung("Warteliste Status geändert", f"ID {eid} -> {neuer_status}")
                flash("Status wurde aktualisiert.", "success")
            except Exception as exc:
                flash(f"Fehler beim Statuswechsel: {exc}", "error")

        elif action == "send_schnupper":
            try:
                eid = int(request.form.get("eintrag_id", "").strip())
                if not gewaehlte_gruppe:
                    raise ValueError("Keine Gruppe ausgewählt.")
                sende_schnupper_einladung(eid, gewaehlte_gruppe)
                log_aenderung(
                    "Warteliste Schnupper-Einladung gesendet",
                    f"Eintrag-ID {eid} für Gruppe {gewaehlte_gruppe['name']}",
                )
                flash(f"Schnupper-Einladung für „{gewaehlte_gruppe['name']}“ wurde versendet.", "success")
            except Exception as exc:
                flash(f"Schnupper-Einladung fehlgeschlagen: {exc}", "error")

        return redirect(
            url_for(
                "admin.admin_warteliste",
                status=status_filter,
                quelle=quelle_filter,
                inklusion="1" if nur_inklusion else "",
                q=suchbegriff,
                gruppe_id=gewaehlte_gruppe["id"] if gewaehlte_gruppe else "",
            )
        )

    db_status_filter = "" if status_filter == "alle" else status_filter
    eintraege = hole_warteliste_eintraege(
        status_filter=db_status_filter,
        quelle_filter=quelle_filter,
        nur_inklusion=nur_inklusion,
        suchbegriff=suchbegriff,
    )

    eintraege = hole_warteliste_eintraege(
        status_filter=db_status_filter,
        quelle_filter=quelle_filter,
        nur_inklusion=False,
        suchbegriff=suchbegriff,
    )

    for e in eintraege:
        passende = []
        for g in gruppen:
            kinder_g = hole_passende_warteliste_kinder_fuer_gruppe(g)
            if any(k.get("id") == e.get("id") for k in kinder_g):
                passende.append(g["name"])
        e["passende_gruppen"] = passende

    gruppen_belegung = None
    passende_kinder = []
    schnupper_kandidaten = []
    if gewaehlte_gruppe:
        gruppen_belegung = {
            "max_teilnehmer": gewaehlte_gruppe["max_teilnehmer"],
            "aktuell": gewaehlte_gruppe["aktuell"],
            "quartalsende": gewaehlte_gruppe["quartalsende"],
            "frei_aktuell": gewaehlte_gruppe["frei_aktuell"],
            "frei_quartalsende": gewaehlte_gruppe["frei_quartalsende"],
        }
        passende_kinder = hole_passende_warteliste_kinder_fuer_gruppe(gewaehlte_gruppe)
        schnupper_kandidaten = lade_schnupper_kandidaten_fuer_gruppe(gewaehlte_gruppe, anzahl=3)

    return render_template(
        "admin/warteliste.html",
        active_tab="warteliste",
        eintraege=eintraege,
        gruppen=gruppen,
        gewaehlte_gruppe=gewaehlte_gruppe,
        gruppen_belegung=gruppen_belegung,
        passende_kinder=passende_kinder,
        schnupper_kandidaten=schnupper_kandidaten,
        status_filter=status_filter,
        quelle_filter=quelle_filter,
        nur_inklusion=nur_inklusion,
        suchbegriff=suchbegriff,
    )