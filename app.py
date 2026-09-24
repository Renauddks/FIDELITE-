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
UPLOAD_FOLDER_MISSIONS = os.path.join(BASE_DIR, "static", "uploads", "missions")
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
os.makedirs(UPLOAD_FOLDER_MISSIONS, exist_ok=True)

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
    init_db.seed_menu_echange(conn)
    init_db.seed_missions(conn)
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


def get_niveau_actuel_row(db, nombre_achats):
    """Renvoie la ligne complète (avec seuil, couleur...) du niveau actuellement atteint."""
    niveaux = get_niveaux_actifs(db)
    actuel = niveaux[0] if niveaux else None
    for niveau in niveaux:
        if nombre_achats >= niveau["nombre_achats_requis"]:
            actuel = niveau
        else:
            break
    return actuel


def get_prochain_niveau_row(db, nombre_achats):
    """Renvoie la ligne complète du prochain niveau à atteindre, ou None si niveau max."""
    niveaux = get_niveaux_actifs(db)
    for niveau in niveaux:
        if nombre_achats < niveau["nombre_achats_requis"]:
            return niveau
    return None


def calculer_progression(db, nombre_achats):
    """Calcule le pourcentage de progression vers le prochain niveau (pour la barre visuelle)."""
    niveau_actuel = get_niveau_actuel_row(db, nombre_achats)
    niveau_suivant = get_prochain_niveau_row(db, nombre_achats)

    if niveau_suivant is None:
        return {
            "pourcentage": 100,
            "achats_manquants": 0,
            "niveau_actuel": niveau_actuel,
            "niveau_suivant": None,
        }

    borne_basse = niveau_actuel["nombre_achats_requis"] if niveau_actuel else 0
    borne_haute = niveau_suivant["nombre_achats_requis"]
    portee = max(1, borne_haute - borne_basse)
    pourcentage = int(min(100, max(0, (nombre_achats - borne_basse) / portee * 100)))

    return {
        "pourcentage": pourcentage,
        "achats_manquants": borne_haute - nombre_achats,
        "niveau_actuel": niveau_actuel,
        "niveau_suivant": niveau_suivant,
    }


POINTS_PAR_BON = 50  # valeur d'un bon d'échange — modifiable selon les objectifs de Sandwich du Roi
WHATSAPP_SANDWICH_DU_ROI = os.environ.get("WHATSAPP_SANDWICH_DU_ROI", "2290197992160")


def lien_whatsapp(numero, texte):
    """Construit un lien wa.me pré-rempli (numéro nettoyé des espaces/signes)."""
    numero_propre = re.sub(r"[^0-9]", "", numero or "")
    if not numero_propre:
        return None
    from urllib.parse import quote
    return f"https://wa.me/{numero_propre}?text={quote(texte)}"


def verifier_missions_auto_retour(db, client, date_achat_precedent):
    """Vérifie et crédite automatiquement les missions de type 'retour rapide' après un nouvel achat."""
    if not date_achat_precedent:
        return  # premier achat du client : rien à comparer

    try:
        date_prec = datetime.strptime(date_achat_precedent, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return

    jours_ecart = (datetime.now() - date_prec).days

    missions_auto = db.execute(
        "SELECT * FROM missions WHERE actif = 1 AND type_mission = 'auto_retour'"
    ).fetchall()

    solde_courant = client["score_points"]
    for mission in missions_auto:
        if mission["seuil_jours"] is not None and jours_ecart <= mission["seuil_jours"]:
            db.execute(
                "UPDATE clients_fidelite SET score_points = score_points + ? WHERE id = ?",
                (mission["points_recompense"], client["id"]),
            )
            db.execute(
                """INSERT INTO missions_completees (client_id, mission_id, statut, date_validation, coffre_ouvert)
                   VALUES (?, ?, 'validee', ?, 0)""",
                (client["id"], mission["id"], datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            solde_courant += mission["points_recompense"]


def notifier(db, client_id, message):
    """Crée une notification interne (visible à la prochaine consultation de la carte du client)."""
    db.execute(
        "INSERT INTO notifications (client_id, message) VALUES (?, ?)",
        (client_id, message),
    )


JOURS_PAR_PALIER_INACTIVITE = 30  # durée d'inactivité avant de perdre un palier de statut


def get_derniere_date_achat(db, client):
    """Date du dernier achat du client, ou date de création de sa carte s'il n'a jamais acheté."""
    row = db.execute(
        "SELECT MAX(date_achat) AS derniere FROM historique_achats WHERE client_id = ?",
        (client["id"],),
    ).fetchone()
    reference = row["derniere"] if row and row["derniere"] else client["date_creation"]
    try:
        return datetime.strptime(reference, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return datetime.now()


def get_niveau_plancher(db):
    """Niveau en dessous duquel le statut ne redescend jamais, quelle que soit l'inactivité."""
    return db.execute(
        "SELECT * FROM niveaux_fidelite WHERE palier_plancher = 1 AND actif = 1 LIMIT 1"
    ).fetchone()


JOURS_AVANT_ALERTE_PREVENTIVE = 10  # nombre de jours avant la perte d'un palier où l'on prévient le client


def get_statut_effectif(db, client):
    """
    Renvoie (niveau_effectif, niveau_acquis, jours_inactivite, statut_degrade, alerte_preventive).
    Le niveau acquis (basé sur nombre_achats) ne change jamais.
    Le niveau effectif redescend d'un palier tous les 30 jours sans achat,
    jusqu'au niveau plancher (Noble par défaut) — puis se stabilise.
    Un nouvel achat restaure immédiatement le niveau acquis (l'inactivité repart à zéro).
    alerte_preventive est un dict {jours_restants, niveau_apres_perte} si le client approche
    (à 10 jours ou moins) d'une nouvelle perte de palier, sinon None.
    """
    niveaux = get_niveaux_actifs(db)
    niveau_acquis = get_niveau_actuel_row(db, client["nombre_achats"])
    if not niveaux or niveau_acquis is None:
        return niveau_acquis, niveau_acquis, 0, False, None

    derniere_date = get_derniere_date_achat(db, client)
    jours_inactivite = max(0, (datetime.now() - derniere_date).days)
    paliers_recul = jours_inactivite // JOURS_PAR_PALIER_INACTIVITE

    index_acquis = next((i for i, n in enumerate(niveaux) if n["id"] == niveau_acquis["id"]), 0)
    plancher = get_niveau_plancher(db)
    index_plancher = 0
    if plancher:
        index_plancher = next((i for i, n in enumerate(niveaux) if n["id"] == plancher["id"]), 0)

    if paliers_recul == 0:
        niveau_effectif = niveau_acquis
        statut_degrade = False
    else:
        nouvel_index = max(index_plancher, index_acquis - paliers_recul)
        niveau_effectif = niveaux[nouvel_index]
        statut_degrade = niveau_effectif["id"] != niveau_acquis["id"]

    # Alerte préventive : encore quelque chose à perdre et échéance proche ?
    alerte_preventive = None
    index_effectif = next((i for i, n in enumerate(niveaux) if n["id"] == niveau_effectif["id"]), 0)
    if index_effectif > index_plancher:
        prochain_seuil_jours = (paliers_recul + 1) * JOURS_PAR_PALIER_INACTIVITE
        jours_restants = prochain_seuil_jours - jours_inactivite
        if 0 < jours_restants <= JOURS_AVANT_ALERTE_PREVENTIVE:
            alerte_preventive = {
                "jours_restants": jours_restants,
                "niveau_apres_perte": niveaux[index_effectif - 1],
            }

    return niveau_effectif, niveau_acquis, jours_inactivite, statut_degrade, alerte_preventive


def get_niveau_max(db):
    """Renvoie le niveau actif le plus élevé (utilisé pour l'affichage, indépendant du déblocage du partage)."""
    return db.execute(
        "SELECT * FROM niveaux_fidelite WHERE actif = 1 ORDER BY nombre_achats_requis DESC LIMIT 1"
    ).fetchone()


def get_niveau_min_partage(db):
    """Renvoie le niveau minimum (le plus accessible) à partir duquel le partage de points est débloqué."""
    return db.execute(
        "SELECT * FROM niveaux_fidelite WHERE debloque_partage = 1 AND actif = 1 ORDER BY nombre_achats_requis ASC LIMIT 1"
    ).fetchone()


def client_peut_partager(db, client):
    """Le partage entre clients est débloqué à partir d'un niveau minimum désigné (Prince/Princesse par défaut),
    en tenant compte de la dégradation par inactivité (basé sur le niveau EFFECTIF)."""
    niveau_effectif, _, _, _, _ = get_statut_effectif(db, client)
    niveau_min = get_niveau_min_partage(db)
    if niveau_effectif is None or niveau_min is None:
        return False
    return niveau_effectif["nombre_achats_requis"] >= niveau_min["nombre_achats_requis"]


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


def enregistrer_preuve_mission(fichier, completion_id):
    """Sauvegarde la capture d'écran envoyée comme preuve de mission."""
    if not fichier or fichier.filename == "":
        return None
    if not fichier_autorise(fichier.filename):
        flash("Format d'image non autorisé (formats acceptés : jpg, jpeg, png, gif, webp).", "danger")
        return None
    extension = fichier.filename.rsplit(".", 1)[1].lower()
    nom_final = secure_filename(f"preuve-{completion_id}.{extension}")
    chemin_absolu = os.path.join(UPLOAD_FOLDER_MISSIONS, nom_final)
    fichier.save(chemin_absolu)
    return f"uploads/missions/{nom_final}"


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
    progression = calculer_progression(db, client["nombre_achats"])

    cartes_completees = client["nombre_achats"] // NB_CASES_GRILLE
    reste = client["nombre_achats"] % NB_CASES_GRILLE
    cases_cochees = NB_CASES_GRILLE if (reste == 0 and client["nombre_achats"] > 0) else reste

    peut_partager = client_peut_partager(db, client)
    niveau_max = get_niveau_max(db)
    niveau_min_partage = get_niveau_min_partage(db)
    niveau_effectif, niveau_acquis, jours_inactivite, statut_degrade, alerte_preventive = get_statut_effectif(db, client)

    menu = db.execute(
        "SELECT * FROM menu_echange WHERE actif = 1 ORDER BY cout_points ASC"
    ).fetchall()

    missions_brutes = db.execute(
        "SELECT * FROM missions WHERE actif = 1 ORDER BY type_mission DESC, points_recompense ASC"
    ).fetchall()
    missions_affichees = []
    maintenant = datetime.now()
    for m in missions_brutes:
        derniere_demande = None
        if m["type_mission"] == "manuelle":
            derniere_demande = db.execute(
                """SELECT * FROM missions_completees
                   WHERE client_id = ? AND mission_id = ?
                   ORDER BY COALESCE(date_debut, date_demande) DESC LIMIT 1""",
                (client["id"], m["id"]),
            ).fetchone()

        m_dict = dict(m)
        m_dict["statut_client"] = derniere_demande["statut"] if derniere_demande else None
        m_dict["preuve_photo"] = derniere_demande["preuve_photo"] if derniere_demande else None

        # Progression pour les missions minutées, en cours
        m_dict["minutes_restantes"] = None
        m_dict["pourcentage_temps"] = None
        if derniere_demande and derniere_demande["statut"] == "en_cours" and m["duree_heures"] and derniere_demande["date_debut"]:
            try:
                debut = datetime.strptime(derniere_demande["date_debut"], "%Y-%m-%d %H:%M:%S")
                ecoule_min = (maintenant - debut).total_seconds() / 60
                duree_min = m["duree_heures"] * 60
                m_dict["minutes_restantes"] = max(0, round(duree_min - ecoule_min))
                m_dict["pourcentage_temps"] = int(min(100, max(0, ecoule_min / duree_min * 100)))
            except (TypeError, ValueError):
                pass

        missions_affichees.append(m_dict)

    notifications = db.execute(
        "SELECT * FROM notifications WHERE client_id = ? AND lu = 0 ORDER BY date_creation ASC",
        (client["id"],),
    ).fetchall()
    if notifications:
        db.execute(
            "UPDATE notifications SET lu = 1 WHERE client_id = ? AND lu = 0",
            (client["id"],),
        )
        db.commit()

    notification_whatsapp = session.pop("notification_whatsapp", None)

    coffres_a_ouvrir = db.execute(
        """SELECT missions_completees.id AS completion_id, missions.titre, missions.icone
           FROM missions_completees
           JOIN missions ON missions.id = missions_completees.mission_id
           WHERE missions_completees.client_id = ? AND missions_completees.statut = 'validee'
                 AND missions_completees.coffre_ouvert = 0
           ORDER BY missions_completees.date_validation ASC""",
        (client["id"],),
    ).fetchall()

    historique = db.execute(
        """SELECT * FROM historique_achats
           WHERE client_id = ? ORDER BY date_achat DESC""",
        (client["id"],),
    ).fetchall()

    # Récompenses déjà débloquées / à venir, pour l'onglet "Mes privilèges"
    niveaux_debloques = [n for n in niveaux if client["nombre_achats"] >= n["nombre_achats_requis"]]
    niveau_suivant_privilege = progression["niveau_suivant"]

    return render_template(
        "carte_client.html",
        client=client,
        niveaux=niveaux,
        progression=progression,
        cases_cochees=cases_cochees,
        nb_cases_total=NB_CASES_GRILLE,
        couleur_or=COULEUR_OR,
        couleur_bordeaux=COULEUR_BORDEAUX,
        couleur_vert=COULEUR_VERT,
        peut_partager=peut_partager,
        niveau_max=niveau_max,
        niveau_min_partage=niveau_min_partage,
        menu=menu,
        cartes_completees=cartes_completees,
        niveau_effectif=niveau_effectif,
        niveau_acquis=niveau_acquis,
        jours_inactivite=jours_inactivite,
        statut_degrade=statut_degrade,
        alerte_preventive=alerte_preventive,
        notifications=notifications,
        notification_whatsapp=notification_whatsapp,
        coffres_a_ouvrir=coffres_a_ouvrir,
        historique=historique,
        niveaux_debloques=niveaux_debloques,
        niveau_suivant_privilege=niveau_suivant_privilege,
        missions=missions_affichees,
    )


@app.route("/carte/<lien_unique>/partager", methods=["POST"])
def partager_points(lien_unique):
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE lien_unique = ?", (lien_unique,)
    ).fetchone()
    if client is None:
        abort(404)

    if not client_peut_partager(db, client):
        flash("Le partage de points n'est pas encore disponible pour votre niveau.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    numero_destinataire = request.form.get("numero_destinataire", "").strip()
    points_bruts = request.form.get("points", "0")
    try:
        points = int(points_bruts)
    except ValueError:
        points = 0

    if points <= 0:
        flash("Le nombre de points doit être supérieur à zéro.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    if points > client["score_points"]:
        flash("Vous n'avez pas assez de points pour partager ce montant.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    destinataire = db.execute(
        "SELECT * FROM clients_fidelite WHERE numero = ?", (numero_destinataire,)
    ).fetchone()

    if destinataire is None:
        flash("Aucun client trouvé avec ce numéro. Vérifiez qu'il est bien inscrit à FIDÉLITÉ+.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    if destinataire["id"] == client["id"]:
        flash("Vous ne pouvez pas vous partager des points à vous-même.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    nouveau_solde_x = client["score_points"] - points
    nouveau_solde_y = destinataire["score_points"] + points

    maintenant = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "UPDATE clients_fidelite SET score_points = score_points - ?, date_derniere_modification = ? WHERE id = ?",
        (points, maintenant, client["id"]),
    )
    db.execute(
        "UPDATE clients_fidelite SET score_points = score_points + ?, date_derniere_modification = ? WHERE id = ?",
        (points, maintenant, destinataire["id"]),
    )
    db.execute(
        """INSERT INTO partages_points (client_source_id, client_dest_id, points_partages)
           VALUES (?, ?, ?)""",
        (client["id"], destinataire["id"], points),
    )
    db.commit()

    # Notification interne pour X (l'expéditeur)
    notifier(
        db, client["id"],
        f"✅ Vous avez offert {points} points à {destinataire['prenom']} {destinataire['nom']}. "
        f"Nouveau solde : {nouveau_solde_x} points."
    )

    # Notification interne pour Y (le bénéficiaire)
    notifier(
        db, destinataire["id"],
        f"🎁 {client['prenom']} {client['nom']} vous a offert {points} points ! "
        f"Nouveau solde : {nouveau_solde_y} points."
    )
    db.commit()

    flash(
        f"🎁 Vous avez offert {points} points à {destinataire['prenom']} {destinataire['nom']}. "
        f"Votre nouveau solde : {nouveau_solde_x} points.",
        "success",
    )

    return redirect(url_for("carte_client", lien_unique=lien_unique))


@app.route("/carte/<lien_unique>/echanger", methods=["POST"])
def echanger_points(lien_unique):
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE lien_unique = ?", (lien_unique,)
    ).fetchone()
    if client is None:
        abort(404)

    produit_id_brut = request.form.get("produit_id", "")
    try:
        produit_id = int(produit_id_brut)
    except ValueError:
        flash("Choisissez un article du menu.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    produit = db.execute(
        "SELECT * FROM menu_echange WHERE id = ? AND actif = 1", (produit_id,)
    ).fetchone()

    if produit is None:
        flash("Cet article n'est plus disponible.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    if produit["cout_points"] > client["score_points"]:
        flash("Vous n'avez pas assez de points pour cet article.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    nouveau_solde = client["score_points"] - produit["cout_points"]
    maintenant = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db.execute(
        "UPDATE clients_fidelite SET score_points = score_points - ?, date_derniere_modification = ? WHERE id = ?",
        (produit["cout_points"], maintenant, client["id"]),
    )
    db.execute(
        """INSERT INTO echanges_points (client_id, points_echanges, nombre_bons, produit_nom)
           VALUES (?, ?, ?, ?)""",
        (client["id"], produit["cout_points"], 0, produit["nom_produit"]),
    )
    db.commit()

    flash(
        f"🍔 Demande enregistrée : {produit['nom_produit']} contre {produit['cout_points']} points. "
        f"Votre nouveau solde : {nouveau_solde} points.",
        "success",
    )

    texte_whatsapp = (
        f"Bonjour Sandwich du Roi ! Je suis {client['prenom']} {client['nom']} ({client['numero']}). "
        f"Je souhaite échanger {produit['cout_points']} points contre : {produit['nom_produit']}. Merci de confirmer 🙏"
    )
    session["notification_whatsapp"] = {
        "lien": lien_whatsapp(WHATSAPP_SANDWICH_DU_ROI, texte_whatsapp),
        "nom_destinataire": "Sandwich du Roi",
    }

    return redirect(url_for("carte_client", lien_unique=lien_unique))


@app.route("/carte/<lien_unique>/coffre/<int:completion_id>/ouvrir", methods=["POST"])
def ouvrir_coffre(lien_unique, completion_id):
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE lien_unique = ?", (lien_unique,)
    ).fetchone()
    if client is None:
        abort(404)

    completion = db.execute(
        """SELECT * FROM missions_completees
           WHERE id = ? AND client_id = ? AND statut = 'validee' AND coffre_ouvert = 0""",
        (completion_id, client["id"]),
    ).fetchone()
    if completion is None:
        flash("Ce coffre n'est plus disponible.", "warning")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    mission = db.execute("SELECT * FROM missions WHERE id = ?", (completion["mission_id"],)).fetchone()

    db.execute(
        "UPDATE missions_completees SET coffre_ouvert = 1 WHERE id = ?", (completion_id,)
    )

    if mission is not None:
        notifier(
            db, client["id"],
            f"🎉 Félicitations ! Le coffre de la mission {mission['icone']} « {mission['titre']} » "
            f"révèle +{mission['points_recompense']} points ! Nouveau solde : {client['score_points']} points."
        )
    db.commit()

    return redirect(url_for("carte_client", lien_unique=lien_unique))


@app.route("/carte/<lien_unique>/mission/<int:mission_id>/demarrer", methods=["POST"])
def demarrer_mission(lien_unique, mission_id):
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE lien_unique = ?", (lien_unique,)
    ).fetchone()
    if client is None:
        abort(404)

    mission = db.execute(
        "SELECT * FROM missions WHERE id = ? AND actif = 1 AND type_mission = 'manuelle'",
        (mission_id,),
    ).fetchone()
    if mission is None:
        flash("Cette mission n'est plus disponible.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    en_cours_ou_attente = db.execute(
        """SELECT 1 FROM missions_completees
           WHERE client_id = ? AND mission_id = ? AND statut IN ('en_cours', 'en_attente')""",
        (client["id"], mission_id),
    ).fetchone()
    if en_cours_ou_attente:
        flash("Cette mission est déjà en cours.", "warning")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    db.execute(
        """INSERT INTO missions_completees (client_id, mission_id, statut, date_debut)
           VALUES (?, ?, 'en_cours', ?)""",
        (client["id"], mission_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    db.commit()

    if mission["duree_heures"]:
        flash(f"🚀 Mission « {mission['titre']} » démarrée ! Revenez dans {mission['duree_heures']}h pour envoyer votre preuve.", "success")
    else:
        flash(f"🚀 Mission « {mission['titre']} » démarrée ! Vous pouvez envoyer votre preuve dès maintenant.", "success")

    return redirect(url_for("carte_client", lien_unique=lien_unique))


@app.route("/carte/<lien_unique>/mission/<int:mission_id>/soumettre", methods=["POST"])
def soumettre_mission(lien_unique, mission_id):
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE lien_unique = ?", (lien_unique,)
    ).fetchone()
    if client is None:
        abort(404)

    mission = db.execute(
        "SELECT * FROM missions WHERE id = ? AND actif = 1 AND type_mission = 'manuelle'",
        (mission_id,),
    ).fetchone()
    if mission is None:
        flash("Cette mission n'est plus disponible.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    demande_en_cours = db.execute(
        """SELECT * FROM missions_completees
           WHERE client_id = ? AND mission_id = ? AND statut = 'en_cours'
           ORDER BY date_debut DESC LIMIT 1""",
        (client["id"], mission_id),
    ).fetchone()
    if demande_en_cours is None:
        flash("Démarrez d'abord la mission avant d'envoyer votre preuve.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    fichier = request.files.get("preuve_photo")
    chemin_preuve = enregistrer_preuve_mission(fichier, demande_en_cours["id"])
    if not chemin_preuve:
        flash("Merci de joindre une capture d'écran comme preuve.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    db.execute(
        """UPDATE missions_completees
           SET statut = 'en_attente', preuve_photo = ?, date_demande = ?
           WHERE id = ?""",
        (chemin_preuve, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), demande_en_cours["id"]),
    )
    db.commit()

    flash(f"🎯 Preuve envoyée ! Mission « {mission['titre']} » en attente de validation par Sandwich du Roi.", "success")

    texte_whatsapp = (
        f"Bonjour Sandwich du Roi ! Je suis {client['prenom']} {client['nom']} ({client['numero']}). "
        f"J'ai réalisé la mission « {mission['titre']} » (+{mission['points_recompense']} points) et envoyé ma preuve dans l'appli. Merci de vérifier et valider 🙏"
    )
    session["notification_whatsapp"] = {
        "lien": lien_whatsapp(WHATSAPP_SANDWICH_DU_ROI, texte_whatsapp),
        "nom_destinataire": "Sandwich du Roi",
    }

    return redirect(url_for("carte_client", lien_unique=lien_unique))


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
    lieu_livraison = request.form.get("lieu_livraison", client["lieu_livraison"] or "").strip()
    age_brut = request.form.get("age", "")

    age = client["age"]
    if age_brut.strip().isdigit():
        age = int(age_brut)

    if numero and numero != client["numero"]:
        doublon = db.execute(
            "SELECT * FROM clients_fidelite WHERE numero = ? AND id != ?", (numero, client["id"])
        ).fetchone()
        if doublon:
            flash("Ce numéro est déjà utilisé par un autre client FIDÉLITÉ+.", "danger")
            return redirect(url_for("carte_client", lien_unique=lien_unique))

    photo_chemin = client["photo"]
    fichier = request.files.get("photo")
    nouvelle_photo = enregistrer_photo(fichier, lien_unique)
    if nouvelle_photo:
        photo_chemin = nouvelle_photo

    db.execute(
        """UPDATE clients_fidelite
           SET nom = ?, prenom = ?, numero = ?, age = ?, photo = ?, lieu_livraison = ?, date_derniere_modification = ?
           WHERE lien_unique = ?""",
        (nom, prenom, numero, age, photo_chemin, lieu_livraison,
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

    clients_enrichis = []
    for c in clients:
        niveau_effectif, _, _, statut_degrade, _ = get_statut_effectif(db, c)
        c_dict = dict(c)
        c_dict["statut_effectif"] = niveau_effectif["nom_niveau"] if niveau_effectif else c["statut_actuel"]
        c_dict["statut_degrade"] = statut_degrade
        clients_enrichis.append(c_dict)

    return render_template(
        "admin_dashboard.html",
        clients=clients_enrichis,
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

    if numero:
        doublon = db.execute(
            "SELECT * FROM clients_fidelite WHERE numero = ?", (numero,)
        ).fetchone()
        if doublon:
            flash(
                f"Ce numéro est déjà utilisé par {doublon['prenom']} {doublon['nom']}. "
                f"Chaque numéro ne peut être enregistré qu'une seule fois.",
                "danger",
            )
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
    niveau_effectif, niveau_acquis, jours_inactivite, statut_degrade, alerte_preventive = get_statut_effectif(db, client)
    return render_template(
        "fiche_client.html", client=client, historique=historique, niveaux=niveaux,
        niveau_effectif=niveau_effectif, niveau_acquis=niveau_acquis,
        jours_inactivite=jours_inactivite, statut_degrade=statut_degrade,
        alerte_preventive=alerte_preventive,
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

    date_achat_precedent = db.execute(
        "SELECT MAX(date_achat) AS derniere FROM historique_achats WHERE client_id = ?",
        (client_id,),
    ).fetchone()["derniere"]

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

    client_a_jour = db.execute("SELECT * FROM clients_fidelite WHERE id = ?", (client_id,)).fetchone()
    verifier_missions_auto_retour(db, client_a_jour, date_achat_precedent)
    db.commit()

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
    seuil_partage_brut = request.form.get("seuil_partage_points", "").strip()
    seuil_partage = int(seuil_partage_brut) if seuil_partage_brut.isdigit() else None

    if not nom_niveau:
        flash("Le nom du niveau est obligatoire.", "danger")
        return redirect(url_for("gestion_niveaux"))

    try:
        seuil = max(0, int(seuil_brut))
    except ValueError:
        seuil = 0

    db.execute(
        """INSERT INTO niveaux_fidelite
           (nom_niveau, nombre_achats_requis, avantages, couleur, icone, actif, seuil_partage_points)
           VALUES (?, ?, ?, ?, ?, 1, ?)""",
        (nom_niveau, seuil, avantages, couleur, icone, seuil_partage),
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
    seuil_partage_brut = request.form.get("seuil_partage_points", "").strip()
    seuil_partage = int(seuil_partage_brut) if seuil_partage_brut.isdigit() else None
    palier_plancher = 1 if request.form.get("palier_plancher") == "on" else 0
    debloque_partage = 1 if request.form.get("debloque_partage") == "on" else 0

    try:
        seuil = max(0, int(seuil_brut))
    except ValueError:
        seuil = niveau["nombre_achats_requis"]

    if palier_plancher:
        # Un seul niveau plancher à la fois : on retire le statut des autres
        db.execute("UPDATE niveaux_fidelite SET palier_plancher = 0 WHERE id != ?", (niveau_id,))
    if debloque_partage:
        # Un seul niveau de déblocage du partage à la fois
        db.execute("UPDATE niveaux_fidelite SET debloque_partage = 0 WHERE id != ?", (niveau_id,))

    db.execute(
        """UPDATE niveaux_fidelite
           SET nom_niveau = ?, nombre_achats_requis = ?, avantages = ?,
               couleur = ?, icone = ?, actif = ?, seuil_partage_points = ?, palier_plancher = ?, debloque_partage = ?
           WHERE id = ?""",
        (nom_niveau, seuil, avantages, couleur, icone, actif, seuil_partage, palier_plancher, debloque_partage, niveau_id),
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


# ----------------------------------------------------------------------
# ADMINISTRATION — ÉCHANGES DE POINTS CONTRE BONS
# ----------------------------------------------------------------------

@app.route("/admin/echanges")
@admin_requis
def gestion_echanges():
    db = get_db()
    echanges = db.execute(
        """SELECT echanges_points.*, clients_fidelite.nom, clients_fidelite.prenom, clients_fidelite.numero
           FROM echanges_points
           JOIN clients_fidelite ON clients_fidelite.id = echanges_points.client_id
           ORDER BY
               CASE WHEN echanges_points.statut = 'en_attente' THEN 0 ELSE 1 END,
               echanges_points.date_demande DESC"""
    ).fetchall()
    return render_template("gestion_echanges.html", echanges=echanges)


@app.route("/admin/echanges/<int:echange_id>/honorer", methods=["POST"])
@admin_requis
def honorer_echange(echange_id):
    db = get_db()
    db.execute(
        """UPDATE echanges_points
           SET statut = 'honore', date_traitement = ?
           WHERE id = ?""",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), echange_id),
    )
    db.commit()
    flash("Échange marqué comme honoré.", "success")
    return redirect(url_for("gestion_echanges"))


# ----------------------------------------------------------------------
# ADMINISTRATION — MENU DE RÉCOMPENSES (échange de points)
# ----------------------------------------------------------------------

@app.route("/admin/missions")
@admin_requis
def gestion_missions():
    db = get_db()
    missions = db.execute("SELECT * FROM missions ORDER BY id ASC").fetchall()
    demandes = db.execute(
        """SELECT missions_completees.*, missions.titre, missions.points_recompense, missions.icone,
                  clients_fidelite.nom, clients_fidelite.prenom, clients_fidelite.numero
           FROM missions_completees
           JOIN missions ON missions.id = missions_completees.mission_id
           JOIN clients_fidelite ON clients_fidelite.id = missions_completees.client_id
           ORDER BY
               CASE WHEN missions_completees.statut = 'en_attente' THEN 0 ELSE 1 END,
               missions_completees.date_demande DESC"""
    ).fetchall()
    return render_template("gestion_missions.html", missions=missions, demandes=demandes)


@app.route("/admin/missions/ajouter", methods=["POST"])
@admin_requis
def ajouter_mission():
    db = get_db()
    titre = request.form.get("titre", "").strip()
    description = request.form.get("description", "").strip()
    points_brut = request.form.get("points_recompense", "0")
    type_mission = request.form.get("type_mission", "manuelle")
    seuil_jours_brut = request.form.get("seuil_jours", "").strip()
    icone = request.form.get("icone", "🎯").strip() or "🎯"

    if not titre:
        flash("Le titre de la mission est obligatoire.", "danger")
        return redirect(url_for("gestion_missions"))

    try:
        points = max(1, int(points_brut))
    except ValueError:
        points = 1

    seuil_jours = int(seuil_jours_brut) if seuil_jours_brut.isdigit() else None

    db.execute(
        """INSERT INTO missions (titre, description, points_recompense, type_mission, seuil_jours, icone, actif)
           VALUES (?, ?, ?, ?, ?, ?, 1)""",
        (titre, description, points, type_mission, seuil_jours, icone),
    )
    db.commit()
    flash("Mission ajoutée.", "success")
    return redirect(url_for("gestion_missions"))


@app.route("/admin/missions/modifier/<int:mission_id>", methods=["POST"])
@admin_requis
def modifier_mission(mission_id):
    db = get_db()
    mission = db.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
    if mission is None:
        abort(404)

    titre = request.form.get("titre", mission["titre"]).strip()
    description = request.form.get("description", mission["description"])
    points_brut = request.form.get("points_recompense", str(mission["points_recompense"]))
    type_mission = request.form.get("type_mission", mission["type_mission"])
    seuil_jours_brut = request.form.get("seuil_jours", "").strip()
    icone = request.form.get("icone", mission["icone"])
    actif = 1 if request.form.get("actif") == "on" else 0

    try:
        points = max(1, int(points_brut))
    except ValueError:
        points = mission["points_recompense"]

    seuil_jours = int(seuil_jours_brut) if seuil_jours_brut.isdigit() else None

    db.execute(
        """UPDATE missions
           SET titre = ?, description = ?, points_recompense = ?, type_mission = ?,
               seuil_jours = ?, icone = ?, actif = ?
           WHERE id = ?""",
        (titre, description, points, type_mission, seuil_jours, icone, actif, mission_id),
    )
    db.commit()
    flash("Mission modifiée.", "success")
    return redirect(url_for("gestion_missions"))


@app.route("/admin/missions/supprimer/<int:mission_id>", methods=["POST"])
@admin_requis
def supprimer_mission(mission_id):
    db = get_db()
    db.execute("DELETE FROM missions WHERE id = ?", (mission_id,))
    db.commit()
    flash("Mission supprimée.", "warning")
    return redirect(url_for("gestion_missions"))


@app.route("/admin/missions/demande/<int:completion_id>/valider", methods=["POST"])
@admin_requis
def valider_mission(completion_id):
    db = get_db()
    demande = db.execute(
        "SELECT * FROM missions_completees WHERE id = ?", (completion_id,)
    ).fetchone()
    if demande is None:
        abort(404)

    mission = db.execute("SELECT * FROM missions WHERE id = ?", (demande["mission_id"],)).fetchone()
    client = db.execute("SELECT * FROM clients_fidelite WHERE id = ?", (demande["client_id"],)).fetchone()

    if demande["statut"] != "en_attente" or mission is None or client is None:
        flash("Cette demande a déjà été traitée.", "warning")
        return redirect(url_for("gestion_missions"))

    nouveau_solde = client["score_points"] + mission["points_recompense"]
    db.execute(
        "UPDATE clients_fidelite SET score_points = ?, date_derniere_modification = ? WHERE id = ?",
        (nouveau_solde, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), client["id"]),
    )
    db.execute(
        "UPDATE missions_completees SET statut = 'validee', date_validation = ?, coffre_ouvert = 0 WHERE id = ?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), completion_id),
    )
    db.commit()
    flash("Mission validée, points crédités. Le client pourra ouvrir son coffre royal.", "success")
    return redirect(url_for("gestion_missions"))


@app.route("/admin/missions/demande/<int:completion_id>/rejeter", methods=["POST"])
@admin_requis
def rejeter_mission(completion_id):
    db = get_db()
    demande = db.execute("SELECT * FROM missions_completees WHERE id = ?", (completion_id,)).fetchone()
    if demande is None:
        abort(404)
    mission = db.execute("SELECT * FROM missions WHERE id = ?", (demande["mission_id"],)).fetchone()
    db.execute(
        "UPDATE missions_completees SET statut = 'rejetee', date_validation = ? WHERE id = ?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), completion_id),
    )
    if mission is not None:
        notifier(
            db, demande["client_id"],
            f"😕 Votre preuve pour la mission « {mission['titre']} » n'a pas été validée. "
            f"Vous pouvez recommencer cette mission quand vous voulez !"
        )
    db.commit()
    flash("Demande rejetée.", "warning")
    return redirect(url_for("gestion_missions"))


@app.route("/admin/menu-echange")
@admin_requis
def gestion_menu_echange():
    db = get_db()
    menu = db.execute(
        "SELECT * FROM menu_echange ORDER BY cout_points ASC"
    ).fetchall()
    return render_template("gestion_menu_echange.html", menu=menu)


@app.route("/admin/menu-echange/ajouter", methods=["POST"])
@admin_requis
def ajouter_produit_menu():
    db = get_db()
    nom_produit = request.form.get("nom_produit", "").strip()
    cout_brut = request.form.get("cout_points", "0")
    description = request.form.get("description", "").strip()

    if not nom_produit:
        flash("Le nom de l'article est obligatoire.", "danger")
        return redirect(url_for("gestion_menu_echange"))

    try:
        cout_points = max(1, int(cout_brut))
    except ValueError:
        cout_points = 1

    db.execute(
        """INSERT INTO menu_echange (nom_produit, cout_points, description, actif)
           VALUES (?, ?, ?, 1)""",
        (nom_produit, cout_points, description),
    )
    db.commit()
    flash("Article ajouté au menu.", "success")
    return redirect(url_for("gestion_menu_echange"))


@app.route("/admin/menu-echange/modifier/<int:produit_id>", methods=["POST"])
@admin_requis
def modifier_produit_menu(produit_id):
    db = get_db()
    produit = db.execute(
        "SELECT * FROM menu_echange WHERE id = ?", (produit_id,)
    ).fetchone()
    if produit is None:
        abort(404)

    nom_produit = request.form.get("nom_produit", produit["nom_produit"]).strip()
    cout_brut = request.form.get("cout_points", str(produit["cout_points"]))
    description = request.form.get("description", produit["description"])
    actif = 1 if request.form.get("actif") == "on" else 0

    try:
        cout_points = max(1, int(cout_brut))
    except ValueError:
        cout_points = produit["cout_points"]

    db.execute(
        """UPDATE menu_echange
           SET nom_produit = ?, cout_points = ?, description = ?, actif = ?
           WHERE id = ?""",
        (nom_produit, cout_points, description, actif, produit_id),
    )
    db.commit()
    flash("Article modifié.", "success")
    return redirect(url_for("gestion_menu_echange"))


@app.route("/admin/menu-echange/supprimer/<int:produit_id>", methods=["POST"])
@admin_requis
def supprimer_produit_menu(produit_id):
    db = get_db()
    db.execute("DELETE FROM menu_echange WHERE id = ?", (produit_id,))
    db.commit()
    flash("Article supprimé du menu.", "warning")
    return redirect(url_for("gestion_menu_echange"))


# ----------------------------------------------------------------------
# POINT D'ENTRÉE
# ----------------------------------------------------------------------

if __name__ == "__main__":
    init_db_if_needed()
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
else:
    # Cas d'un déploiement via serveur WSGI (ex. Render / gunicorn)
    init_db_if_needed()
