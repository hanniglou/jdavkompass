import os
from flask import Blueprint, jsonify, request, abort
from app.models import get_kompass

api_bp = Blueprint("api", __name__, url_prefix="/api")

API_SECRET_KEY = os.getenv("API_SECRET_KEY", "kompass_api_9f8b2c4e7a1d6f3b8c5e2a9f4d7b1c3e")


@api_bp.route("/abrechnung", methods=["GET"])
def get_abrechnung_data():
    auth_header = request.headers.get("X-API-Key")
    if not auth_header or auth_header != API_SECRET_KEY:
        abort(401, description="Unauthorized: Ungültiger oder fehlender API-Schlüssel")

    with get_kompass() as conn:
        cursor = conn.cursor()

        # 1. Alle Jugendgruppen laden
        cursor.execute("""
            SELECT id, name, wochentag, startzeit, endzeit
            FROM jugendgruppen
            ORDER BY id
        """)
        gruppen_rows = cursor.fetchall()

        # 2. Alle Mitglieder pro Gruppe laden (inkl. Geburtsdatum für TN-Abrechnung)
        cursor.execute("""
            SELECT mj.jugendgruppe_id, m.id, m.vorname, m.nachname, m.geburtsdatum
            FROM mitglied_jugendgruppen mj
            JOIN mitglieder m ON m.id = mj.mitglied_id
            ORDER BY m.vorname, m.nachname
        """)
        mitglieder_by_gruppe = {}
        for row in cursor.fetchall():
            mitglieder_by_gruppe.setdefault(row["jugendgruppe_id"], []).append({
                "id": row["id"],
                "vorname": row["vorname"],
                "nachname": row["nachname"],
                "geburtsdatum": row["geburtsdatum"],
            })

        # 3. Feste Jugendleitende pro Gruppe laden
        cursor.execute("""
            SELECT glj.jugendgruppe_id, gl.id, gl.vorname, gl.nachname, gl.gruppenrolle, gl.vereinsrolle
            FROM gruppenleiter_jugendgruppen glj
            JOIN gruppenleiter gl ON gl.id = glj.gruppenleiter_id
            ORDER BY gl.vorname, gl.nachname
        """)
        leiter_by_gruppe = {}
        for row in cursor.fetchall():
            leiter_by_gruppe.setdefault(row["jugendgruppe_id"], []).append({
                "id": row["id"],
                "vorname": row["vorname"],
                "nachname": row["nachname"],
                "gruppenrolle": row["gruppenrolle"],
                "vereinsrolle": row["vereinsrolle"],
            })

        # 4. Alle geleisteten JuLei-Termine (inkl. Vertretungen!) laden
        cursor.execute("""
            SELECT
                al.gruppe_id,
                al.datum,
                gl.id AS leiter_id,
                gl.vorname,
                gl.nachname,
                gl.gruppenrolle,
                gl.vereinsrolle
            FROM anwesenheit_leiter al
            JOIN gruppenleiter gl ON gl.id = al.gruppenleiter_id
            WHERE al.anwesend = 1
            ORDER BY al.datum, gl.vorname, gl.nachname
        """)
        termine_by_gruppe = {}
        for row in cursor.fetchall():
            termine_by_gruppe.setdefault(row["gruppe_id"], []).append({
                "datum": row["datum"],  # Format: YYYY-MM-DD
                "leiter_id": row["leiter_id"],
                "vorname": row["vorname"],
                "nachname": row["nachname"],
                "gruppenrolle": row["gruppenrolle"],
                "vereinsrolle": row["vereinsrolle"],
            })

        # Alles sauber pro Gruppe bündeln
        daten = []
        for g in gruppen_rows:
            g_id = g["id"]
            daten.append({
                "gruppe_id": g_id,
                "gruppen_name": g["name"],
                "wochentag": g["wochentag"],
                "startzeit": g["startzeit"],
                "endzeit": g["endzeit"],
                "teilnehmer": mitglieder_by_gruppe.get(g_id, []),
                "feste_leiter": leiter_by_gruppe.get(g_id, []),
                "leiter_anwesenheiten": termine_by_gruppe.get(g_id, []),
            })

    return jsonify({
        "status": "success",
        "daten": daten,
    })