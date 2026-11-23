from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.routes.auth import require_role
from app.services.gruppen_service import loesche_gruppe, neue_gruppe_erstellen
from app.services.utils import jugendgruppen_preview

gruppen_bp = Blueprint("gruppen", __name__)

WOCHENTAGE_REIHENFOLGE = [
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
]


@gruppen_bp.route("/gruppen", methods=["GET", "POST"])
@login_required
@require_role(1)
def gruppen():
    filter_modus = request.args.get("filter", "alle").strip().lower()
    if filter_modus not in {"alle", "meine"}:
        filter_modus = "alle"

    alle_gruppen = jugendgruppen_preview(account_id=current_user.id)
    if filter_modus == "meine":
        gefilterte_gruppen = [g for g in alle_gruppen if g.get("ist_meine_gruppe")]
    else:
        gefilterte_gruppen = alle_gruppen

    gruppen_nach_tag = []
    bekannte_tage = []
    for tag in WOCHENTAGE_REIHENFOLGE:
        eintraege = [g for g in gefilterte_gruppen if (g.get("wochentag") or "") == tag]
        if eintraege:
            eintraege.sort(
                key=lambda g: (
                    (g.get("startzeit") or ""),
                    (g.get("name") or "").lower(),
                )
            )
            gruppen_nach_tag.append((tag, eintraege))
            bekannte_tage.append(tag)

    for g in gefilterte_gruppen:
        tag = g.get("wochentag") or "Ohne Wochentag"
        if tag not in bekannte_tage:
            existing = next((item for item in gruppen_nach_tag if item[0] == tag), None)
            if existing:
                existing[1].append(g)
            else:
                gruppen_nach_tag.append((tag, [g]))
                bekannte_tage.append(tag)

    return render_template(
        "gruppen.html",
        gruppen=gefilterte_gruppen,
        gruppen_nach_tag=gruppen_nach_tag,
        filter_modus=filter_modus,
    )


@gruppen_bp.route("/gruppen/neue", methods=["POST"])
@login_required
@require_role(3)
def neue_gruppe():
    data = request.form
    neue_gruppe_erstellen(data)
    return redirect(url_for("gruppen.gruppen"))


@gruppen_bp.route("/gruppen/<int:gruppe_id>/loeschen", methods=["POST"])
@login_required
@require_role(3)
def gruppe_loeschen(gruppe_id):
    loesche_gruppe(gruppe_id)
    return redirect(url_for("gruppen.gruppen"))