"""
FIDÉLITÉ+ — Application de gestion des cartes de fidélité
Sandwich du Roi

Application INDÉPENDANTE de SANDWICH_DU_ROI_APP (base de données séparée : fidelite.db).

Lancement local (Pydroid 3 / ordinateur) :
    python app.py
Puis ouvrir http://127.0.0.1:5000 dans le navigateur.

Pour un accès réel des clients à leurs liens individuels depuis l'extérieur,
cette application doit être hébergée en ligne (ex. Render) — voir README.txt.
"""

import os
import re
import sqlite3
import unicodedata
import secrets
from datetime import datetime
from functools import wraps

from flask import (
    Flask, g, render_template, request, redirect,
    url_for, session, flash, abort
)
from werkzeug.utils import secure_filename

# ----------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "fidelite.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 Mo max par photo

# Mot de passe admin : personnalisable via variable d'environnement.
# ⚠️ À changer en production (voir README.txt).
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD_FIDELITE", "SandwichRoi2026")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY_FIDELITE", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

# Git ne conserve pas les dossiers vides : on le recrée nous-mêmes si absent
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

COULEUR_OR = "#D4AF37"
COULEUR_BORDEAUX = "#8B0000"
COULEUR_VERT = "#32CD32"
NB_CASES_GRILLE = 20  # 4 lignes de 5


# ----------------------------------------------------------------------
# BASE DE DONNÉES
# ----------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db_if_needed():
    """Crée les tables et les niveaux par défaut si la base n'existe pas encore."""
    import init_db
    conn = sqlite3.connect(DB_PATH)
    init_db.creer_tables(conn)
    init_db.seed_niveaux(conn)
    conn.close()


# ----------------------------------------------------------------------
# UTILITAIRES MÉTIER
# ----------------------------------------------------------------------

def slugifier(texte):
    """Transforme 'Jean Dupont' en 'jean-dupont' (sans accents, sans espaces)."""
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    texte = re.sub(r"[^a-zA-Z0-9]+", "-", texte).strip("-").lower()
    return texte or "client"


def generer_lien_unique(db, nom, prenom):
    """Génère un identifiant de lien unique et garanti non-existant en base."""
    base = slugifier(f"{prenom}-{nom}")
    while True:
        suffixe = secrets.token_hex(3)  # 6 caractères
        candidat = f"{base}-{suffixe}"
        existe = db.execute(
            "SELECT 1 FROM clients_fidelite WHERE lien_unique = ?", (candidat,)
        ).fetchone()
        if not existe:
            return candidat


def get_niveaux_actifs(db):
    """Renvoie les niveaux actifs triés par seuil d'achats croissant."""
    return db.execute(
        "SELECT * FROM niveaux_fidelite WHERE actif = 1 ORDER BY nombre_achats_requis ASC"
    ).fetchall()


def calculer_statut(db, nombre_achats):
    """Détermine le nom du niveau atteint selon le nombre d'achats."""
    niveaux = get_niveaux_actifs(db)
    statut = niveaux[0]["nom_niveau"] if niveaux else "Nouveau Client"
    for niveau in niveaux:
        if nombre_achats >= niveau["nombre_achats_requis"]:
            statut = niveau["nom_niveau"]
        else:
            break
    return statut


def calculer_prochain_niveau(db, nombre_achats):
    """Renvoie (nom_prochain_niveau, achats_manquants) ou (None, 0) si niveau max atteint."""
    niveaux = get_niveaux_actifs(db)
    for niveau in niveaux:
        if nombre_achats < niveau["nombre_achats_requis"]:
            manquants = niveau["nombre_achats_requis"] - nombre_achats
            return niveau["nom_niveau"], manquants
    return None, 0


def maj_statut_client(db, client_id):
    """Recalcule et enregistre le statut d'un client à partir de son nombre d'achats."""
    client = db.execute(
        "SELECT nombre_achats FROM clients_fidelite WHERE id = ?", (client_id,)
    ).fetchone()
    if client is None:
        return
    nouveau_statut = calculer_statut(db, client["nombre_achats"])
    db.execute(
        """UPDATE clients_fidelite
           SET statut_actuel = ?, date_derniere_modification = ?
           WHERE id = ?""",
        (nouveau_statut, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), client_id),
    )
    db.commit()


def fichier_autorise(nom_fichier):
    return "." in nom_fichier and nom_fichier.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def enregistrer_photo(fichier, lien_unique):
    """Sauvegarde la photo uploadée et renvoie le chemin relatif à stocker en base."""
    if not fichier or fichier.filename == "":
        return None
    if not fichier_autorise(fichier.filename):
        flash("Format de photo non autorisé (formats acceptés : jpg, jpeg, png, gif, webp).", "danger")
        return None
    extension = fichier.filename.rsplit(".", 1)[1].lower()
    nom_final = secure_filename(f"{lien_unique}.{extension}")
    chemin_absolu = os.path.join(UPLOAD_FOLDER, nom_final)
    fichier.save(chemin_absolu)
    return f"uploads/{nom_final}"


# ----------------------------------------------------------------------
# AUTHENTIFICATION ADMIN (protection simple, admin unique)
# ----------------------------------------------------------------------

def admin_requis(vue):
    @wraps(vue)
    def enveloppe(*args, **kwargs):
        if not session.get("admin_connecte"):
            return redirect(url_for("admin_login", suivant=request.path))
        return vue(*args, **kwargs)
    return enveloppe


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        mot_de_passe = request.form.get("mot_de_passe", "")
        if secrets.compare_digest(mot_de_passe, ADMIN_PASSWORD):
            session["admin_connecte"] = True
            suivant = request.args.get("suivant") or url_for("admin_dashboard")
            return redirect(suivant)
        flash("Mot de passe incorrect.", "danger")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_connecte", None)
    return redirect(url_for("admin_login"))


# ----------------------------------------------------------------------
# PAGE D'ACCUEIL
# ----------------------------------------------------------------------

@app.route("/")
def accueil():
    return render_template("accueil.html")


# ----------------------------------------------------------------------
# CARTE CLIENT (accès public via lien individuel)
# ----------------------------------------------------------------------

@app.route("/carte/<lien_unique>")
def carte_client(lien_unique):
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE lien_unique = ?", (lien_unique,)
    ).fetchone()
    if client is None:
        abort(404)

    niveaux = get_niveaux_actifs(db)
    prochain_niveau, achats_manquants = calculer_prochain_niveau(db, client["nombre_achats"])

    cases_cochees = min(client["nombre_achats"], NB_CASES_GRILLE)

    return render_template(
        "carte_client.html",
        client=client,
        niveaux=niveaux,
        prochain_niveau=prochain_niveau,
        achats_manquants=achats_manquants,
        cases_cochees=cases_cochees,
        nb_cases_total=NB_CASES_GRILLE,
        couleur_or=COULEUR_OR,
        couleur_bordeaux=COULEUR_BORDEAUX,
        couleur_vert=COULEUR_VERT,
    )


@app.route("/carte/<lien_unique>/modifier", methods=["POST"])
def modifier_infos(lien_unique):
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE lien_unique = ?", (lien_unique,)
    ).fetchone()
    if client is None:
        abort(404)

    nom = request.form.get("nom", client["nom"]).strip()
    prenom = request.form.get("prenom", client["prenom"]).strip()
    numero = request.form.get("numero", client["numero"])
    age_brut = request.form.get("age", "")

    age = client["age"]
    if age_brut.strip().isdigit():
        age = int(age_brut)

    photo_chemin = client["photo"]
    fichier = request.files.get("photo")
    nouvelle_photo = enregistrer_photo(fichier, lien_unique)
    if nouvelle_photo:
        photo_chemin = nouvelle_photo

    db.execute(
        """UPDATE clients_fidelite
           SET nom = ?, prenom = ?, numero = ?, age = ?, photo = ?, date_derniere_modification = ?
           WHERE lien_unique = ?""",
        (nom, prenom, numero, age, photo_chemin,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), lien_unique),
    )
    db.commit()
    flash("Vos informations ont été mises à jour.", "success")
    return redirect(url_for("carte_client", lien_unique=lien_unique))


# ----------------------------------------------------------------------
# ADMINISTRATION — TABLEAU DE BORD
# ----------------------------------------------------------------------

@app.route("/admin")
@admin_requis
def admin_dashboard():
    db = get_db()
    recherche = request.args.get("q", "").strip()

    if recherche:
        motif = f"%{recherche}%"
        clients = db.execute(
            """SELECT * FROM clients_fidelite
               WHERE nom LIKE ? OR prenom LIKE ? OR numero LIKE ?
               ORDER BY date_creation DESC""",
            (motif, motif, motif),
        ).fetchall()
    else:
        clients = db.execute(
            "SELECT * FROM clients_fidelite ORDER BY date_creation DESC"
        ).fetchall()

    total_clients = db.execute("SELECT COUNT(*) AS n FROM clients_fidelite").fetchone()["n"]

    return render_template(
        "admin_dashboard.html",
        clients=clients,
        total_clients=total_clients,
        recherche=recherche,
    )


@app.route("/admin/client/ajouter", methods=["POST"])
@admin_requis
def ajouter_client():
    db = get_db()
    nom = request.form.get("nom", "").strip()
    prenom = request.form.get("prenom", "").strip()
    numero = request.form.get("numero", "").strip()
    age_brut = request.form.get("age", "")
    age = int(age_brut) if age_brut.strip().isdigit() else None

    if not nom or not prenom:
        flash("Le nom et le prénom sont obligatoires.", "danger")
        return redirect(url_for("admin_dashboard"))

    lien_unique = generer_lien_unique(db, nom, prenom)
    statut_initial = calculer_statut(db, 0)

    db.execute(
        """INSERT INTO clients_fidelite
           (lien_unique, nom, prenom, numero, age, nombre_achats, score_points, statut_actuel)
           VALUES (?, ?, ?, ?, ?, 0, 0, ?)""",
        (lien_unique, nom, prenom, numero, age, statut_initial),
    )
    db.commit()
    flash(f"Client créé. Lien individuel : /carte/{lien_unique}", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/client/<int:client_id>/fiche")
@admin_requis
def fiche_client(client_id):
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE id = ?", (client_id,)
    ).fetchone()
    if client is None:
        abort(404)
    historique = db.execute(
        """SELECT * FROM historique_achats
           WHERE client_id = ? ORDER BY date_achat DESC""",
        (client_id,),
    ).fetchall()
    niveaux = get_niveaux_actifs(db)
    return render_template(
        "fiche_client.html", client=client, historique=historique, niveaux=niveaux
    )


@app.route("/admin/client/<int:client_id>/ajouter_achat", methods=["POST"])
@admin_requis
def ajouter_achat(client_id):
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE id = ?", (client_id,)
    ).fetchone()
    if client is None:
        abort(404)

    montant_brut = request.form.get("montant", "0")
    points_brut = request.form.get("points_ajoutes", "0")
    note = request.form.get("note", "").strip()

    try:
        montant = float(montant_brut)
    except ValueError:
        montant = 0.0
    try:
        points_ajoutes = int(points_brut)
    except ValueError:
        points_ajoutes = 0

    db.execute(
        """INSERT INTO historique_achats (client_id, montant, points_ajoutes, note)
           VALUES (?, ?, ?, ?)""",
        (client_id, montant, points_ajoutes, note),
    )

    nouveau_nombre_achats = client["nombre_achats"] + 1
    nouveau_score = client["score_points"] + points_ajoutes

    db.execute(
        """UPDATE clients_fidelite
           SET nombre_achats = ?, score_points = ?, date_derniere_modification = ?
           WHERE id = ?""",
        (nouveau_nombre_achats, nouveau_score,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), client_id),
    )
    db.commit()

    maj_statut_client(db, client_id)

    flash("Achat enregistré et carte mise à jour.", "success")
    return redirect(url_for("fiche_client", client_id=client_id))


@app.route("/admin/client/<int:client_id>/modifier_points", methods=["POST"])
@admin_requis
def modifier_points(client_id):
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE id = ?", (client_id,)
    ).fetchone()
    if client is None:
        abort(404)

    action = request.form.get("action", "definir")  # definir | ajouter | retirer
    valeur_brute = request.form.get("valeur", "0")
    try:
        valeur = int(valeur_brute)
    except ValueError:
        valeur = 0

    if action == "ajouter":
        nouveau_score = client["score_points"] + valeur
    elif action == "retirer":
        nouveau_score = max(0, client["score_points"] - valeur)
    else:  # definir
        nouveau_score = max(0, valeur)

    db.execute(
        """UPDATE clients_fidelite
           SET score_points = ?, date_derniere_modification = ?
           WHERE id = ?""",
        (nouveau_score, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), client_id),
    )
    db.commit()
    flash("Score de points mis à jour.", "success")
    return redirect(url_for("fiche_client", client_id=client_id))


@app.route("/admin/client/<int:client_id>/modifier_achats", methods=["POST"])
@admin_requis
def modifier_achats(client_id):
    """Correction manuelle du nombre d'achats en cas d'erreur."""
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE id = ?", (client_id,)
    ).fetchone()
    if client is None:
        abort(404)

    valeur_brute = request.form.get("nombre_achats", "0")
    try:
        nouveau_nombre = max(0, int(valeur_brute))
    except ValueError:
        nouveau_nombre = client["nombre_achats"]

    db.execute(
        """UPDATE clients_fidelite
           SET nombre_achats = ?, date_derniere_modification = ?
           WHERE id = ?""",
        (nouveau_nombre, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), client_id),
    )
    db.commit()
    maj_statut_client(db, client_id)
    flash("Nombre d'achats corrigé.", "success")
    return redirect(url_for("fiche_client", client_id=client_id))


@app.route("/admin/client/<int:client_id>/reinitialiser", methods=["POST"])
@admin_requis
def reinitialiser_client(client_id):
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE id = ?", (client_id,)
    ).fetchone()
    if client is None:
        abort(404)

    statut_initial = calculer_statut(db, 0)
    db.execute(
        """UPDATE clients_fidelite
           SET nombre_achats = 0, score_points = 0, statut_actuel = ?, date_derniere_modification = ?
           WHERE id = ?""",
        (statut_initial, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), client_id),
    )
    db.commit()
    flash("Carte réinitialisée.", "warning")
    return redirect(url_for("fiche_client", client_id=client_id))


@app.route("/admin/client/<int:client_id>/generer_lien")
@admin_requis
def generer_lien(client_id):
    """Affiche/renvoie le lien individuel existant du client (pour copie/partage)."""
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE id = ?", (client_id,)
    ).fetchone()
    if client is None:
        abort(404)
    lien_complet = url_for("carte_client", lien_unique=client["lien_unique"], _external=True)
    return {"lien_unique": client["lien_unique"], "lien_complet": lien_complet}


# ----------------------------------------------------------------------
# ADMINISTRATION — NIVEAUX ET AVANTAGES
# ----------------------------------------------------------------------

@app.route("/admin/niveaux")
@admin_requis
def gestion_niveaux():
    db = get_db()
    niveaux = db.execute(
        "SELECT * FROM niveaux_fidelite ORDER BY nombre_achats_requis ASC"
    ).fetchall()
    return render_template("gestion_niveaux.html", niveaux=niveaux)


@app.route("/admin/niveaux/ajouter", methods=["POST"])
@admin_requis
def ajouter_niveau():
    db = get_db()
    nom_niveau = request.form.get("nom_niveau", "").strip()
    seuil_brut = request.form.get("nombre_achats_requis", "0")
    avantages = request.form.get("avantages", "").strip()
    couleur = request.form.get("couleur", COULEUR_OR).strip() or COULEUR_OR
    icone = request.form.get("icone", "🏆").strip() or "🏆"

    if not nom_niveau:
        flash("Le nom du niveau est obligatoire.", "danger")
        return redirect(url_for("gestion_niveaux"))

    try:
        seuil = max(0, int(seuil_brut))
    except ValueError:
        seuil = 0

    db.execute(
        """INSERT INTO niveaux_fidelite
           (nom_niveau, nombre_achats_requis, avantages, couleur, icone, actif)
           VALUES (?, ?, ?, ?, ?, 1)""",
        (nom_niveau, seuil, avantages, couleur, icone),
    )
    db.commit()
    flash("Niveau ajouté.", "success")
    return redirect(url_for("gestion_niveaux"))


@app.route("/admin/niveaux/modifier/<int:niveau_id>", methods=["POST"])
@admin_requis
def modifier_niveau(niveau_id):
    db = get_db()
    niveau = db.execute(
        "SELECT * FROM niveaux_fidelite WHERE id = ?", (niveau_id,)
    ).fetchone()
    if niveau is None:
        abort(404)

    nom_niveau = request.form.get("nom_niveau", niveau["nom_niveau"]).strip()
    seuil_brut = request.form.get("nombre_achats_requis", str(niveau["nombre_achats_requis"]))
    avantages = request.form.get("avantages", niveau["avantages"])
    couleur = request.form.get("couleur", niveau["couleur"])
    icone = request.form.get("icone", niveau["icone"])
    actif = 1 if request.form.get("actif") == "on" else 0

    try:
        seuil = max(0, int(seuil_brut))
    except ValueError:
        seuil = niveau["nombre_achats_requis"]

    db.execute(
        """UPDATE niveaux_fidelite
           SET nom_niveau = ?, nombre_achats_requis = ?, avantages = ?,
               couleur = ?, icone = ?, actif = ?
           WHERE id = ?""",
        (nom_niveau, seuil, avantages, couleur, icone, actif, niveau_id),
    )
    db.commit()

    # Le changement d'un seuil peut changer le statut de tous les clients concernés
    clients = db.execute("SELECT id FROM clients_fidelite").fetchall()
    for c in clients:
        maj_statut_client(db, c["id"])

    flash("Niveau modifié.", "success")
    return redirect(url_for("gestion_niveaux"))


@app.route("/admin/niveaux/supprimer/<int:niveau_id>", methods=["POST"])
@admin_requis
def supprimer_niveau(niveau_id):
    db = get_db()
    db.execute("DELETE FROM niveaux_fidelite WHERE id = ?", (niveau_id,))
    db.commit()
    flash("Niveau supprimé.", "warning")
    return redirect(url_for("gestion_niveaux"))


# ----------------
