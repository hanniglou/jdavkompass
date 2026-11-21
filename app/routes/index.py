from flask import Blueprint, render_template, abort, send_from_directory
from flask_login import current_user, login_required
import os

from app.routes.auth import require_role
from app.services.mitglieder_service import get_unapproved_mitglieder_daten
from app.services.utils import jugendgruppen_preview, heute
from app.services.kuendigung_service import get_kuendigungen

index_bp = Blueprint("index", __name__)


@index_bp.route("/", methods=["GET"])
@login_required
@require_role(1)
def index():
    """Zeigt die Hauptseite für alle eingeloggten Nutzer:innen,
    die mindestens Rolle 1 haben an.
    """
    heutige_gruppen = [g for g in jugendgruppen_preview() if g["wochentag"] == heute()]

    mitglieder_unapproved_daten = []
    kuendigungen_ausstehend_count = 0
    if getattr(current_user, "role", 0) >= 3:
        mitglieder_unapproved_daten = get_unapproved_mitglieder_daten()
        kuendigungen_ausstehend_count = len(get_kuendigungen("ausstehende"))

    return render_template(
        "index.html",
        user=current_user,
        today=heute(),
        heutige_gruppen=heutige_gruppen,
        mitglieder_unapproved_daten=mitglieder_unapproved_daten,
        kuendigungen_ausstehend_count=kuendigungen_ausstehend_count,
    )


@index_bp.route("/download/<path:file>", methods=["GET"])
@login_required
def download(file):
    if file != "Datenschutzerklärung.pdf" and current_user is None:
        abort(403)
    upload_folder = os.path.join(os.getcwd(), "uploads")
    return send_from_directory(upload_folder, file, as_attachment=True)