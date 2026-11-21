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
    log_aenderung,
    save_gruppenleiter,
    update_account_status,
)
from app.services.gruppe_service import baue_anwesenheits_excel
from app.services.mail_service import (
    SYSTEM_TEMPLATE_SCHLUESSEL,
    erstelle_mail_template,
    hole_adressbuch_eintraege,
    hole_alle_mail_templates,
    hole_mail_konfiguration,
    loesche_adressbuch_eintrag,
    loesche_mail_template,
    sende_test_mail,
    speichere_adressbuch_eintrag,
    speichere_mail_konfiguration,
    speichere_mail_template,
)


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
            bezeichnung = request.form.get("bezeichnung", "").strip()
            email_adresse = request.form.get("email", "").strip()
            if bezeichnung and email_adresse:
                speichere_adressbuch_eintrag(bezeichnung, email_adresse)
                log_aenderung("Adressbuch-Eintrag hinzugefügt", f"{bezeichnung} ({email_adresse})")
                flash("Adressbuch-Eintrag wurde hinzugefügt.", "success")
            else:
                flash("Bitte sowohl eine Bezeichnung als auch eine E-Mail-Adresse angeben.", "error")

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