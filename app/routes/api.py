import os
from flask import Blueprint, jsonify, request, abort
from app.models import get_kompass

api_bp = Blueprint("api", __name__, url_prefix="/api")

API_SECRET_KEY = os.getenv("API_SECRET_KEY", "kompass_api_a16ab0a738613118494415a18c93c932b84ee0c9")


def _map_anwesenheit_status(wert):
    if wert is None:
        return "Abwesend"
    s = str(wert).strip().lower()
    if s in ("1", "true", "anwesend", "ja"):
        return "Anwesend"
    if s in ("2", "-1", "entschuldigt", "e"):
        return "Entschuldigt"
    return "Abwesend"


@api_bp.route("/abrechnung", methods=["GET"])
def get_abrechnung_data():
    auth_header = request.headers.get("X-API-Key")
    if not auth_header or auth_header != API_SECRET_KEY:
        abort(401, description="Unauthorized: Ungültiger oder fehlender API-Schlüssel")

    datum_von = (request.args.get("datum_von") or "").strip() or None
    datum_bis = (request.args.get("datum_bis") or "").strip() or None

    with get_kompass() as conn:
        cursor = conn.cursor()

        # 1. Alle Jugendgruppen inkl. gruppenart laden
        cursor.execute("""
            SELECT id, name, wochentag, startzeit, endzeit, gruppenart
            FROM jugendgruppen
            ORDER BY id
        """)
        gruppen_rows = cursor.fetchall()

        # 2. Erziehungsberechtigte pro Mitglied laden
        cursor.execute("""
            SELECT
                me.mitglied_id,
                eb.vorname,
                eb.nachname,
                eb.email
            FROM mitglied_erziehungsberechtigte me
            JOIN erziehungsberechtigte eb ON eb.id = me.erziehungsberechtigter_id
            ORDER BY eb.nachname, eb.vorname
        """)
        eb_by_mitglied = {}
        for row in cursor.fetchall():
            name = f"{(row['vorname'] or '').strip()} {(row['nachname'] or '').strip()}".strip()
            eb_by_mitglied.setdefault(row["mitglied_id"], []).append({
                "name": name,
                "email": (row["email"] or "").strip(),
            })

        # 3. Aktuell zugeordnete Mitglieder pro Gruppe laden
        cursor.execute("""
            SELECT
                mj.jugendgruppe_id,
                m.id,
                m.mitgliedsnummer,
                m.vorname,
                m.nachname,
                m.geburtsdatum
            FROM mitglied_jugendgruppen mj
            JOIN mitglieder m ON m.id = mj.mitglied_id
            ORDER BY m.vorname, m.nachname
        """)
        mitglieder_by_gruppe = {}
        for row in cursor.fetchall():
            m_id = row["id"]
            mitglieder_by_gruppe.setdefault(row["jugendgruppe_id"], []).append({
                "id": m_id,
                "mitgliedsnummer": row["mitgliedsnummer"],
                "vorname": row["vorname"],
                "nachname": row["nachname"],
                "geburtsdatum": row["geburtsdatum"],
                "erziehungsberechtigte": eb_by_mitglied.get(m_id, []),
            })

        # 4. Konkrete TN-Anwesenheiten laden
        tn_where = []
        tn_params = []
        if datum_von:
            tn_where.append("a.datum >= ?")
            tn_params.append(datum_von)
        if datum_bis:
            tn_where.append("a.datum <= ?")
            tn_params.append(datum_bis)

        tn_where_sql = f"WHERE {' AND '.join(tn_where)}" if tn_where else ""
        cursor.execute(f"""
            SELECT
                a.gruppe_id,
                a.datum,
                a.anwesend,
                m.id AS mitglied_id,
                m.mitgliedsnummer,
                m.vorname,
                m.nachname,
                m.geburtsdatum
            FROM anwesenheit a
            JOIN mitglieder m ON m.id = a.mitglied_id
            {tn_where_sql}
            ORDER BY a.datum, m.vorname, m.nachname
        """, tn_params)

        tn_termine_by_gruppe = {}
        for row in cursor.fetchall():
            m_id = row["mitglied_id"]
            tn_termine_by_gruppe.setdefault(row["gruppe_id"], []).append({
                "datum": row["datum"],
                "status": _map_anwesenheit_status(row["anwesend"]),
                "mitglied_id": m_id,
                "mitgliedsnummer": row["mitgliedsnummer"],
                "vorname": row["vorname"],
                "nachname": row["nachname"],
                "geburtsdatum": row["geburtsdatum"],
                "erziehungsberechtigte": eb_by_mitglied.get(m_id, []),
            })

        # 5. Feste Jugendleitende pro Gruppe laden
        cursor.execute("""
            SELECT glj.jugendgruppe_id, gl.id, gl.vorname, gl.nachname, gl.gruppenrolle
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
            })

        # 6. Geleistete JuLei-Termine laden
        jl_where = []
        jl_params = []
        if datum_von:
            jl_where.append("al.datum >= ?")
            jl_params.append(datum_von)
        if datum_bis:
            jl_where.append("al.datum <= ?")
            jl_params.append(datum_bis)

        jl_where_sql = f"WHERE {' AND '.join(jl_where)}" if jl_where else ""
        cursor.execute(f"""
            SELECT
                al.gruppe_id,
                al.datum,
                al.anwesend,
                gl.id AS leiter_id,
                gl.vorname,
                gl.nachname,
                gl.gruppenrolle
            FROM anwesenheit_leiter al
            JOIN gruppenleiter gl ON gl.id = al.gruppenleiter_id
            {jl_where_sql}
            ORDER BY al.datum, gl.vorname, gl.nachname
        """, jl_params)

        termine_by_gruppe = {}
        for row in cursor.fetchall():
            termine_by_gruppe.setdefault(row["gruppe_id"], []).append({
                "datum": row["datum"],
                "status": _map_anwesenheit_status(row["anwesend"]),
                "leiter_id": row["leiter_id"],
                "vorname": row["vorname"],
                "nachname": row["nachname"],
                "gruppenrolle": row["gruppenrolle"],
            })

        # Alles pro Gruppe bündeln
        daten = []
        for g in gruppen_rows:
            g_id = g["id"]
            daten.append({
                "gruppe_id": g_id,
                "gruppen_name": g["name"],
                "gruppenart": g["gruppenart"],
                "wochentag": g["wochentag"],
                "startzeit": g["startzeit"],
                "endzeit": g["endzeit"],
                "teilnehmer": mitglieder_by_gruppe.get(g_id, []),
                "teilnehmer_anwesenheiten": tn_termine_by_gruppe.get(g_id, []),
                "feste_leiter": leiter_by_gruppe.get(g_id, []),
                "leiter_anwesenheiten": termine_by_gruppe.get(g_id, []),
            })

    return jsonify({
        "status": "success",
        "filter": {
            "datum_von": datum_von,
            "datum_bis": datum_bis,
        },
        "daten": daten,
    })