from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required

from app.routes.auth import require_role
from app.services.mitglieder_service import (
    approve,
    delete_unapproved,
    get_unapproved_mitglieder_daten,
)

mitglieder_freigeben_bp = Blueprint("mitglieder_freigeben", __name__)


@mitglieder_freigeben_bp.route("/mitglieder-freigeben", methods=["GET", "POST"])
@login_required
@require_role(3)
def mitglieder_freigeben():
    if request.method == "POST":
        mitglied_id = request.form.get("mitglied_id")
        action = request.form.get("action")

        if not mitglied_id:
            return redirect(url_for("mitglieder_freigeben.mitglieder_freigeben"))
        if action == "approve":
            approve(mitglied_id)
        elif action == "reject":
            delete_unapproved(mitglied_id)

        return redirect(url_for("mitglieder_freigeben.mitglieder_freigeben"))

    mitglieder_unapproved_daten = get_unapproved_mitglieder_daten()

    return render_template(
        "mitglieder_freigeben.html",
        mitglieder_unapproved_daten=mitglieder_unapproved_daten,
    )