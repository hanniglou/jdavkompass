import datetime as dt
from datetime import date, timedelta

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from app.models import get_kompass
from app.routes.auth import require_role
from app.services.admin_service import log_aenderung
from app.services.gruppe_service import (
    baue_anwesenheits_excel,
    bereinige_und_hole_gruppen_hinweise,
    empfehle_gruppen_jahrgang,
    get_anwesenheit,
    get_gruppe,
    get_gruppenleiter,
    get_vertretungen_by_date,
    get_vertretungs_kandidaten,
    gruppenleiter_aus_gruppe_entfernen,
    gruppenleiter_zu_gruppe_hinzufuegen,
    gruppenwechsel_hinweis_erfassen,
    hole_teilnehmende_und_quartalsende,
    mitglied_aus_gruppe_entfernen,
    mitglied_zu_gruppe_hinzufuegen,
    new_day,
    search_person,
    termin_loeschen,
    update_anwesenheit,
    update_vertretungen,
)
from app.services.mitglieder_service import (
    log_gruppenaufnahme_historie,
    log_gruppenentfernung_historie,
    log_gruppenverschiebung_historie,
)
from app.services.utils import heute

gruppe_bp = Blueprint("gruppe", __name__)


@gruppe_bp.route("/gruppen/<int:gruppe_id>", methods=["GET", "POST"])
@login_required
@require_role(1)
def gruppe(gruppe_id):
    today = date.today()
    today_name = heute()
    total_age = 0

    gruppe = get_gruppe(gruppe_id)
    entfernte_namen, gekuendigt_folgequartal_hinweise, verschoben_folgequartal_hinweise = (
        bereinige_und_hole_gruppen_hinweise(
            gruppe_id=gruppe_id,
            referenz_datum=today,
        )
    )
    if entfernte_namen:
        log_aenderung(
            "Mitglieder nach Kündigungsquartal entfernt",
            f"{gruppe['name']} (ID {gruppe_id}): {', '.join(entfernte_namen)}",
        )

    mitglieder, anzahl_aktuell, anzahl_quartalsende = hole_teilnehmende_und_quartalsende(
        gruppe_id=gruppe_id,
        referenz_datum=today,
    )
    gruppenleiter = get_gruppenleiter(gruppe_id)

    alle_daten_mitglieder, anwesenheit = get_anwesenheit(gruppe_id, "mitglied")
    alle_daten_gruppenleiter, gruppenleiter_anwesenheit = get_anwesenheit(
        gruppe_id, "gruppenleiter"
    )
    alle_daten = list(dict.fromkeys(alle_daten_mitglieder + alle_daten_gruppenleiter))

    if today_name == gruppe["wochentag"] and str(today) not in alle_daten:
        new_day(gruppe_id=gruppe_id, datum=today)

        alle_daten_mitglieder, anwesenheit = get_anwesenheit(gruppe_id, "mitglied")
        alle_daten_gruppenleiter, gruppenleiter_anwesenheit = get_anwesenheit(
            gruppe_id, "gruppenleiter"
        )
        alle_daten = list(dict.fromkeys(alle_daten_mitglieder + alle_daten_gruppenleiter))

    zeitraum = request.args.get("filter", "quartal")
    if zeitraum == "quartal":
        quartal_start_monat = ((today.month - 1) // 3) * 3 + 1
        start_datum = date(today.year, quartal_start_monat, 1).isoformat()
        alle_daten = [d for d in alle_daten if d >= start_datum]
    elif zeitraum == "6monate":
        start_datum = (today - timedelta(days=183)).isoformat()
        alle_daten = [d for d in alle_daten if d >= start_datum]

    for mitglied in mitglieder:
        geburtsdatum = dt.datetime.strptime(mitglied["geburtsdatum"], "%Y-%m-%d")
        total_age += today.year - geburtsdatum.year
    avg_age = int(total_age / len(mitglieder)) if len(mitglieder) > 0 else 0

    if request.method == "POST":
        if current_user.role < 2:
            abort(403)

        action = request.form.get("action", "speichern_anwesenheit")

        if action == "termin_nachtragen":
            datum_raw = (request.form.get("nachtrag_datum") or "").strip()
            ziel_filter = zeitraum
            try:
                gewaehltes_datum = dt.datetime.strptime(datum_raw, "%Y-%m-%d").date()
                if gewaehltes_datum > today:
                    flash("Zukünftige Termine können nicht nachgetragen werden.", "error")
                else:
                    datum_iso = gewaehltes_datum.isoformat()
                    alle_vorhandenen, _ = get_anwesenheit(gruppe_id, "mitglied")
                    alle_vorhandenen_leiter, _ = get_anwesenheit(gruppe_id, "gruppenleiter")
                    vorhandene_daten = set(alle_vorhandenen) | set(alle_vorhandenen_leiter)

                    if datum_iso in vorhandene_daten:
                        flash(
                            f"Für den {gewaehltes_datum.strftime('%d.%m.%Y')} existiert bereits ein Termin.",
                            "info",
                        )
                    else:
                        new_day(gruppe_id=gruppe_id, datum=datum_iso)
                        log_aenderung(
                            "Termin nachgetragen",
                            f"{gruppe['name']} (ID {gruppe_id}) am {datum_iso}",
                        )
                        flash(
                            f"Termin am {gewaehltes_datum.strftime('%d.%m.%Y')} wurde nachgetragen.",
                            "success",
                        )

                    if zeitraum == "quartal":
                        quartal_start_monat = ((today.month - 1) // 3) * 3 + 1
                        start_datum = date(today.year, quartal_start_monat, 1).isoformat()
                        if datum_iso < start_datum:
                            ziel_filter = "alle"
                    elif zeitraum == "6monate":
                        start_datum = (today - timedelta(days=183)).isoformat()
                        if datum_iso < start_datum:
                            ziel_filter = "alle"

            except ValueError:
                flash("Bitte ein gültiges Datum auswählen.", "error")

            return redirect(url_for("gruppe.gruppe", gruppe_id=gruppe_id, filter=ziel_filter))

        if action == "termin_loeschen":
            datum_loeschen = (request.form.get("datum") or "").strip()
            try:
                geprueftes_datum = dt.datetime.strptime(datum_loeschen, "%Y-%m-%d").date().isoformat()
                termin_loeschen(gruppe_id=gruppe_id, datum=geprueftes_datum)
                log_aenderung(
                    "Termin gelöscht",
                    f"{gruppe['name']} (ID {gruppe_id}) am {geprueftes_datum}",
                )
                flash(
                    f"Termin am {dt.datetime.strptime(geprueftes_datum, '%Y-%m-%d').strftime('%d.%m.%Y')} wurde gelöscht.",
                    "success",
                )
            except ValueError:
                flash("Ungültiges Datum zum Löschen.", "error")

            return redirect(url_for("gruppe.gruppe", gruppe_id=gruppe_id, filter=zeitraum))

        update_anwesenheit(gruppe_id, "mitglied", mitglieder, request, alle_daten)
        update_anwesenheit(gruppe_id, "gruppenleiter", gruppenleiter, request, alle_daten)
        update_vertretungen(gruppe_id, gruppenleiter, request, alle_daten)
        log_aenderung(
            "Anwesenheit gespeichert",
            f"{gruppe['name']} (ID {gruppe_id})",
        )
        flash("Anwesenheit wurde gespeichert.", "success")

        return redirect(url_for("gruppe.gruppe", gruppe_id=gruppe_id, filter=zeitraum))

    vertretungs_kandidaten = get_vertretungs_kandidaten(gruppenleiter)
    vertretungen = get_vertretungen_by_date(gruppe_id, gruppenleiter)

    sortierte_daten = sorted(alle_daten)
    daten_formatiert = {}
    for d in sortierte_daten:
        try:
            daten_formatiert[d] = dt.datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            daten_formatiert[d] = d

    return render_template(
        "gruppe.html",
        today_name=today_name,
        today=str(today),
        gruppe=gruppe,
        mitglieder=mitglieder,
        anzahl_aktuell=anzahl_aktuell,
        anzahl_quartalsende=anzahl_quartalsende,
        anwesenheit=anwesenheit,
        alle_daten=sortierte_daten,
        daten_formatiert=daten_formatiert,
        gruppenleiter=gruppenleiter,
        gruppenleiter_anwesenheit=gruppenleiter_anwesenheit,
        avg_age=avg_age,
        zeitraum=zeitraum,
        vertretungs_kandidaten=vertretungs_kandidaten,
        vertretungen=vertretungen,
        gekuendigt_folgequartal_hinweise=gekuendigt_folgequartal_hinweise,
        verschoben_folgequartal_hinweise=verschoben_folgequartal_hinweise,
    )


@gruppe_bp.route("/search", methods=["GET"])
@login_required
@require_role(1)
def search():
    search_query = request.args.get("query", "")
    if "mitglied" in search_query:
        search_in = "mitglieder"
        search_query = search_query.replace("mitglied", "").strip()
    else:
        search_in = "gruppenleiter"
        search_query = search_query.replace("leiter", "").strip()

    return jsonify(
        {
            "results": [
                {
                    "id": row["id"],
                    "vorname": row["vorname"],
                    "nachname": row["nachname"],
                }
                for row in search_person(search_in, search_query)
            ]
        }
    )


@gruppe_bp.route("/gruppe/<int:gruppe_id>/mitglied/<int:mitglied_id>", methods=["POST"])
@login_required
@require_role(2)
def mitglied_zu_gruppe(gruppe_id, mitglied_id):
    mitglied_zu_gruppe_hinzufuegen(gruppe_id, mitglied_id)
    gruppe_info = get_gruppe(gruppe_id)
    gruppen_name = gruppe_info["name"] if gruppe_info else f"Gruppe {gruppe_id}"
    log_gruppenaufnahme_historie(
        mitglied_id=mitglied_id,
        gruppen_name=gruppen_name,
        bearbeiter_uname=current_user.display_name,
    )
    log_aenderung(
        "Mitglied zu Gruppe hinzugefügt",
        f"Mitglied-ID {mitglied_id} -> {gruppen_name} (ID {gruppe_id})",
    )

    return redirect(url_for("gruppe.gruppe", gruppe_id=gruppe_id))


@gruppe_bp.route(
    "/gruppenleiter_zu_gruppe/<int:gruppe_id>/<int:gruppenleiter_id>", methods=["POST"]
)
@login_required
@require_role(2)
def gruppenleiter_zu_gruppe(gruppe_id, gruppenleiter_id):
    gruppenleiter_zu_gruppe_hinzufuegen(gruppe_id, gruppenleiter_id)
    log_aenderung(
        "JuLei zu Gruppe hinzugefügt",
        f"JuLei-ID {gruppenleiter_id} -> Gruppe-ID {gruppe_id}",
    )

    return redirect(url_for("gruppe.gruppe", gruppe_id=gruppe_id))


@gruppe_bp.route(
    "/gruppe/<int:gruppe_id>/mitglied/<int:mitglied_id>/verschieben",
    methods=["GET", "POST"],
)
@login_required
@require_role(2)
def mitglied_verschieben(gruppe_id, mitglied_id):
    heute_datum = date.today()
    quell_gruppe = get_gruppe(gruppe_id)
    if not quell_gruppe:
        abort(404)

    with get_kompass() as db:
        mitglied = db.execute(
            "SELECT id, vorname, nachname FROM mitglieder WHERE id = ?",
            (mitglied_id,),
        ).fetchone()
        if not mitglied:
            abort(404)

        ziel_gruppen = db.execute(
            """
            SELECT id, name, wochentag, startzeit
            FROM jugendgruppen
            WHERE id != ?
            ORDER BY name COLLATE NOCASE ASC
            """,
            (gruppe_id,),
        ).fetchall()

    if request.method == "POST":
        ziel_gruppe_raw = (request.form.get("ziel_gruppe_id") or "").strip()
        datum_raw = (request.form.get("wechsel_datum") or "").strip()

        if not ziel_gruppe_raw.isdigit():
            flash("Bitte eine gültige Zielgruppe auswählen.", "error")
            return redirect(
                url_for(
                    "gruppe.mitglied_verschieben",
                    gruppe_id=gruppe_id,
                    mitglied_id=mitglied_id,
                )
            )

        ziel_gruppe_id = int(ziel_gruppe_raw)
        if ziel_gruppe_id == gruppe_id:
            flash("Die Zielgruppe muss sich von der aktuellen Gruppe unterscheiden.", "error")
            return redirect(
                url_for(
                    "gruppe.mitglied_verschieben",
                    gruppe_id=gruppe_id,
                    mitglied_id=mitglied_id,
                )
            )

        ziel_gruppe = get_gruppe(ziel_gruppe_id)
        if not ziel_gruppe:
            flash("Die ausgewählte Zielgruppe existiert nicht.", "error")
            return redirect(
                url_for(
                    "gruppe.mitglied_verschieben",
                    gruppe_id=gruppe_id,
                    mitglied_id=mitglied_id,
                )
            )

        try:
            wechsel_datum = (
                dt.datetime.strptime(datum_raw, "%Y-%m-%d").date()
                if datum_raw
                else heute_datum
            )
        except ValueError:
            flash("Bitte ein gültiges Wechseldatum angeben.", "error")
            return redirect(
                url_for(
                    "gruppe.mitglied_verschieben",
                    gruppe_id=gruppe_id,
                    mitglied_id=mitglied_id,
                )
            )

        voller_name = f"{mitglied['vorname']} {mitglied['nachname']}".strip()
        wechsel_datum_iso = wechsel_datum.isoformat()
        wechsel_datum_de = wechsel_datum.strftime("%d.%m.%Y")

        mitglied_zu_gruppe_hinzufuegen(ziel_gruppe_id, mitglied_id)
        mitglied_aus_gruppe_entfernen(gruppe_id, mitglied_id)

        gruppenwechsel_hinweis_erfassen(
            quell_gruppe_id=gruppe_id,
            ziel_gruppe_name=ziel_gruppe["name"],
            mitglied_id=mitglied_id,
            mitglied_name=voller_name,
            wechsel_datum=wechsel_datum_iso,
        )

        log_gruppenverschiebung_historie(
            mitglied_id=mitglied_id,
            quell_gruppe_name=quell_gruppe["name"],
            ziel_gruppe_name=ziel_gruppe["name"],
            wechsel_datum_de=wechsel_datum_de,
            bearbeiter_uname=current_user.display_name,
        )
        log_aenderung(
            "Mitglied in andere Gruppe verschoben",
            f"{voller_name} (ID {mitglied_id}): {quell_gruppe['name']} -> {ziel_gruppe['name']} (am {wechsel_datum_de})",
        )
        flash(
            f"{voller_name} wurde am {wechsel_datum_de} in „{ziel_gruppe['name']}“ verschoben.",
            "success",
        )
        return redirect(url_for("gruppe.gruppe_bearbeiten", gruppe_id=gruppe_id))

    return render_template(
        "mitglied_verschieben.html",
        gruppe=quell_gruppe,
        mitglied=mitglied,
        ziel_gruppen=ziel_gruppen,
        default_iso=heute_datum.isoformat(),
        default_de=heute_datum.strftime("%d.%m.%Y"),
    )


@gruppe_bp.route(
    "/gruppe/<int:gruppe_id>/mitglied/<int:mitglied_id>/entfernen", methods=["POST"]
)
@login_required
@require_role(3)
def mitglied_entfernen(gruppe_id, mitglied_id):
    mitglied_aus_gruppe_entfernen(gruppe_id, mitglied_id)
    gruppe_info = get_gruppe(gruppe_id)
    gruppen_name = gruppe_info["name"] if gruppe_info else f"Gruppe {gruppe_id}"
    log_gruppenentfernung_historie(
        mitglied_id=mitglied_id,
        gruppen_name=gruppen_name,
        bearbeiter_uname=current_user.display_name,
    )
    log_aenderung(
        "Mitglied aus Gruppe entfernt",
        f"Mitglied-ID {mitglied_id} <- {gruppen_name} (ID {gruppe_id})",
    )

    return redirect(url_for("gruppe.gruppe", gruppe_id=gruppe_id))


@gruppe_bp.route(
    "/gruppe/<int:gruppe_id>/gruppenleiter/<int:gruppenleiter_id>/entfernen",
    methods=["POST"],
)
@login_required
@require_role(3)
def gruppenleiter_entfernen(gruppe_id, gruppenleiter_id):
    gruppenleiter_aus_gruppe_entfernen(gruppe_id, gruppenleiter_id)
    log_aenderung(
        "JuLei aus Gruppe entfernt",
        f"JuLei-ID {gruppenleiter_id} <- Gruppe-ID {gruppe_id}",
    )

    return redirect(url_for("gruppe.gruppe", gruppe_id=gruppe_id))


@gruppe_bp.route("/gruppe/<int:gruppe_id>/download_attendance", methods=["GET"])
@login_required
@require_role(2)
def download_attendance(gruppe_id):
    excel_buf, dateiname = baue_anwesenheits_excel(gruppe_id)
    return send_file(
        excel_buf,
        as_attachment=True,
        download_name=dateiname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@gruppe_bp.route("/<int:gruppe_id>/bearbeiten", methods=["GET", "POST"])
@login_required
@require_role(2)
def gruppe_bearbeiten(gruppe_id):
    with get_kompass() as db:
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            wochentag = (request.form.get("wochentag") or "").strip()
            startzeit = (request.form.get("startzeit") or "").strip()
            dauer_raw = (request.form.get("dauer") or "").strip()
            gruppenart = (request.form.get("gruppenart") or "").strip()
            max_tn_raw = (request.form.get("max_teilnehmer") or "").strip()
            jahrgang_raw = (request.form.get("jahrgang") or "").strip()

            dauer = int(dauer_raw) if dauer_raw.isdigit() else None
            default_max = 6 if gruppenart.lower() == "minis" else 12
            max_teilnehmer = (
                int(max_tn_raw)
                if max_tn_raw.isdigit() and int(max_tn_raw) > 0
                else default_max
            )
            jahrgang = int(jahrgang_raw) if jahrgang_raw.isdigit() else None

            db.execute(
                """
                UPDATE jugendgruppen
                SET name = ?, wochentag = ?, startzeit = ?, dauer = ?, gruppenart = ?,
                    max_teilnehmer = ?, jahrgang = ?
                WHERE id = ?
                """,
                (
                    name,
                    wochentag,
                    startzeit,
                    dauer,
                    gruppenart,
                    max_teilnehmer,
                    jahrgang,
                    gruppe_id,
                ),
            )
            db.commit()
            log_aenderung(
                "Gruppe bearbeitet",
                f"{name} (ID {gruppe_id})",
            )

            return redirect(url_for("gruppe.gruppe", gruppe_id=gruppe_id))

        gruppe = db.execute("SELECT * FROM jugendgruppen WHERE id = ?", (gruppe_id,)).fetchone()
        mitglieder = db.execute("SELECT * FROM mitglieder ORDER BY nachname").fetchall()
        gruppenleiter = db.execute("SELECT * FROM gruppenleiter ORDER BY nachname").fetchall()

        aktive_mitglieder = db.execute(
            """
            SELECT m.* FROM mitglieder m
            JOIN mitglied_jugendgruppen mg ON m.id = mg.mitglied_id
            WHERE mg.jugendgruppe_id = ?
            """,
            (gruppe_id,),
        ).fetchall()

        aktive_leiter = db.execute(
            """
            SELECT gl.* FROM gruppenleiter gl
            JOIN gruppenleiter_jugendgruppen glg ON gl.id = glg.gruppenleiter_id
            WHERE glg.jugendgruppe_id = ?
            """,
            (gruppe_id,),
        ).fetchall()

    jahrgang_empfehlung = empfehle_gruppen_jahrgang(
        gruppe_id=gruppe_id,
        gruppenart=gruppe["gruppenart"] if gruppe else None,
    )

    return render_template(
        "gruppe_bearbeiten.html",
        gruppe=gruppe,
        mitglieder=mitglieder,
        gruppenleiter=gruppenleiter,
        aktive_mitglieder=aktive_mitglieder,
        aktive_leiter=aktive_leiter,
        jahrgang_empfehlung=jahrgang_empfehlung,
    )