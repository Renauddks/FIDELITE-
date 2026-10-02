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

import json
import os
import re
import unicodedata
import secrets
from datetime import datetime
from functools import wraps

from flask import (
    Flask, g, render_template, request, redirect,
    url_for, session, flash, abort, make_response
)
from werkzeug.utils import secure_filename

import db_compat

# ----------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
        g.db = db_compat.connecter()
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db_if_needed():
    """Crée les tables et les niveaux par défaut si la base est vide."""
    import init_db
    conn = db_compat.connecter()
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


CARACTERES_CODE_BON = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sans 0/O ni 1/I, pour éviter les confusions


def generer_code_bon(db):
    """Génère un code de bon unique, lisible (6 caractères, sans caractères ambigus)."""
    while True:
        candidat = "".join(secrets.choice(CARACTERES_CODE_BON) for _ in range(6))
        existe = db.execute(
            "SELECT 1 FROM echanges_points WHERE code_unique = ?", (candidat,)
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
        if mission["ciblage"] == "clients" and not mission_ciblee(db, mission, client):
            continue
        if mission["seuil_jours"] is not None and jours_ecart <= mission["seuil_jours"]:
            accorder_recompense(db, mission, client["id"])
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


# ----------------------------------------------------------------------
# Récompenses de missions : points, produit du menu ou avantage en nature
# ----------------------------------------------------------------------
TYPES_RECOMPENSE = {
    "points": "⭐ Points de fidélité",
    "produit": "🍔 Produit du menu",
    "avantage": "🎁 Autre avantage en nature",
}


def libelle_recompense(mission):
    """Texte lisible de la récompense d'une mission (ex. « +30 pts », « 🍔 Sandwich viennois »)."""
    type_recompense = mission["type_recompense"] or "points"
    points = mission["points_recompense"] or 0
    morceaux = []
    if points > 0:
        morceaux.append(f"+{points} pts")
    if type_recompense == "produit":
        morceaux.append(f"🍔 {mission['recompense_texte'] or 'Produit offert'}")
    elif type_recompense == "avantage":
        morceaux.append(f"🎁 {mission['recompense_texte'] or 'Avantage offert'}")
    return " + ".join(morceaux) or "🎁 Surprise"


def recompense_en_nature(mission):
    return (mission["type_recompense"] or "points") in ("produit", "avantage")


app.jinja_env.globals["libelle_recompense"] = libelle_recompense
app.jinja_env.globals["recompense_en_nature"] = recompense_en_nature


def lire_recompense_formulaire(db):
    """Lit la récompense du formulaire admin : (type, points, produit_id, texte) ou None si invalide."""
    type_recompense = request.form.get("type_recompense", "points")
    if type_recompense not in TYPES_RECOMPENSE:
        type_recompense = "points"
    try:
        points = int(request.form.get("points_recompense", "0") or 0)
    except ValueError:
        points = 0

    produit_id, texte = None, None
    if type_recompense == "points":
        points = max(1, points)
    else:
        points = max(0, points)
        if type_recompense == "produit":
            brut = request.form.get("produit_id", "")
            produit = None
            if brut.isdigit():
                produit = db.execute(
                    "SELECT * FROM menu_echange WHERE id = ?", (int(brut),)
                ).fetchone()
            if produit is None:
                flash("Choisissez un produit du menu à offrir.", "danger")
                return None
            produit_id, texte = produit["id"], produit["nom_produit"]
        else:
            texte = request.form.get("recompense_texte", "").strip()[:200]
            if not texte:
                flash("Décrivez l'avantage offert (ex : boisson offerte).", "danger")
                return None
    return type_recompense, points, produit_id, texte


def accorder_recompense(db, mission, client_id):
    """Crédite les points et/ou génère un bon pour le produit ou l'avantage en nature.

    Renvoie le code du bon généré (ou None s'il n'y a que des points).
    """
    points = mission["points_recompense"] or 0
    maintenant = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if points > 0:
        db.execute(
            "UPDATE clients_fidelite SET score_points = score_points + ?, date_derniere_modification = ? WHERE id = ?",
            (points, maintenant, client_id),
        )
    if not recompense_en_nature(mission):
        return None

    nom = mission["recompense_texte"] or "Récompense de mission"
    if mission["type_recompense"] == "produit" and mission["produit_id"]:
        produit = db.execute(
            "SELECT nom_produit FROM menu_echange WHERE id = ?", (mission["produit_id"],)
        ).fetchone()
        if produit:
            nom = produit["nom_produit"]
    code = generer_code_bon(db)
    db.execute(
        """INSERT INTO echanges_points (client_id, client_actuel_id, points_echanges, nombre_bons, produit_nom, code_unique)
           VALUES (?, ?, 0, 0, ?, ?)""",
        (client_id, client_id, f"🎯 {nom} (mission {mission['titre']})", code),
    )
    return code


# ----------------------------------------------------------------------
# Missions personnalisées : ciblage selon le comportement des clients
# ----------------------------------------------------------------------
SEGMENTS_CLIENTS = {
    "nouveaux": {"nom": "🌱 Nouveaux clients", "defaut": 2, "aide": "au plus N achats"},
    "inactifs": {"nom": "😴 Clients inactifs", "defaut": 14, "aide": "sans achat depuis N jours ou plus"},
    "fideles": {"nom": "👑 Clients fidèles", "defaut": 10, "aide": "au moins N achats"},
    "proches_niveau": {"nom": "🚀 Proches du niveau suivant", "defaut": 2, "aide": "à N achats ou moins du niveau suivant"},
}

OBJECTIFS_COMMERCIAUX = [
    "Faire revenir le client",
    "Augmenter la fréquence d'achat",
    "Faire essayer un produit",
    "Faire monter de niveau",
    "Récompenser la fidélité",
    "Attirer de nouveaux clients (parrainage)",
    "Visibilité / avis en ligne",
]


def _construire_profil(client, derniere_date, niveaux_requis):
    achats = client["nombre_achats"]
    suivants = [r for r in niveaux_requis if r > achats]
    return {
        "achats": achats,
        "jours_sans_achat": max(0, (datetime.now() - derniere_date).days),
        "achats_manquants": (min(suivants) - achats) if suivants else None,
    }


def profils_clients(db, clients):
    """Profil de comportement (achats, jours sans achat, distance au niveau suivant) de chaque client."""
    niveaux_requis = [n["nombre_achats_requis"] for n in get_niveaux_actifs(db)]
    dernieres = {
        ligne["client_id"]: ligne["derniere"]
        for ligne in db.execute(
            "SELECT client_id, MAX(date_achat) AS derniere FROM historique_achats GROUP BY client_id"
        ).fetchall()
    }
    profils = {}
    for c in clients:
        reference = dernieres.get(c["id"]) or c["date_creation"]
        try:
            derniere_date = datetime.strptime(reference, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            derniere_date = datetime.now()
        profils[c["id"]] = _construire_profil(c, derniere_date, niveaux_requis)
    return profils


def profil_client(db, client):
    return profils_clients(db, [client])[client["id"]]


def client_dans_segment(profil, segment, param=None):
    if segment not in SEGMENTS_CLIENTS:
        return False
    seuil = param if param is not None else SEGMENTS_CLIENTS[segment]["defaut"]
    if segment == "nouveaux":
        return profil["achats"] <= seuil
    if segment == "inactifs":
        return profil["jours_sans_achat"] >= seuil
    if segment == "fideles":
        return profil["achats"] >= seuil
    if segment == "proches_niveau":
        return profil["achats_manquants"] is not None and profil["achats_manquants"] <= seuil
    return False


def mission_ciblee(db, mission, client, profil=None):
    """True si la mission est destinée à ce client (tous / profil de clients / clients choisis)."""
    ciblage = mission["ciblage"] or "tous"
    if ciblage == "clients":
        return db.execute(
            "SELECT 1 FROM missions_clients WHERE mission_id = ? AND client_id = ?",
            (mission["id"], client["id"]),
        ).fetchone() is not None
    if ciblage == "segment":
        if profil is None:
            profil = profil_client(db, client)
        return client_dans_segment(profil, mission["segment"], mission["segment_param"])
    return True


def enregistrer_clients_cibles(db, mission_id, titre, ids):
    """Remplace la liste des clients choisis et prévient ceux qui viennent d'être ajoutés."""
    anciens = {
        l["client_id"]
        for l in db.execute(
            "SELECT client_id FROM missions_clients WHERE mission_id = ?", (mission_id,)
        ).fetchall()
    }
    nouveaux = set(ids)
    db.execute("DELETE FROM missions_clients WHERE mission_id = ?", (mission_id,))
    for client_id in nouveaux:
        db.execute(
            "INSERT INTO missions_clients (mission_id, client_id) VALUES (?, ?)",
            (mission_id, client_id),
        )
    for client_id in nouveaux - anciens:
        notifier(db, client_id, f"🎯 Nouvelle mission pour vous : {titre}")


def lire_ciblage_formulaire(db, type_mission):
    """Lit les champs de ciblage du formulaire admin : (ciblage, segment, param, objectif, ids_clients)."""
    ciblage = request.form.get("ciblage", "tous")
    if ciblage not in ("tous", "segment", "clients"):
        ciblage = "tous"
    segment = request.form.get("segment") or None
    param_brut = request.form.get("segment_param", "").strip()
    param = int(param_brut) if param_brut.isdigit() else None

    if ciblage == "segment":
        if segment not in SEGMENTS_CLIENTS:
            flash("Aucun profil choisi : la mission reste proposée à tous les clients.", "warning")
            ciblage, segment, param = "tous", None, None
        elif type_mission == "auto_retour":
            flash("Le ciblage par profil ne s'applique qu'aux missions manuelles : mission proposée à tous.", "warning")
            ciblage, segment, param = "tous", None, None
    else:
        segment, param = None, None

    objectif = request.form.get("objectif", "").strip() or None

    ids = []
    if ciblage == "clients":
        existants = {l["id"] for l in db.execute("SELECT id FROM clients_fidelite").fetchall()}
        ids = [
            int(x) for x in request.form.getlist("clients_cibles")
            if x.isdigit() and int(x) in existants
        ]
    return ciblage, segment, param, objectif, ids


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


def fichier_autorise(fichier):
    """Accepte un fichier si son extension OU son type MIME indique une image —
    certains téléphones envoient des photos sans extension reconnaissable."""
    nom_fichier = fichier.filename if hasattr(fichier, "filename") else fichier
    extension_ok = "." in nom_fichier and nom_fichier.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    mimetype = getattr(fichier, "mimetype", "") or getattr(fichier, "content_type", "") or ""
    mimetype_ok = mimetype.startswith("image/")
    return extension_ok or mimetype_ok


def extension_depuis_fichier(fichier):
    """Détermine l'extension à utiliser : celle du nom de fichier si reconnue,
    sinon déduite du type MIME envoyé par le téléphone."""
    nom_fichier = fichier.filename or ""
    if "." in nom_fichier and nom_fichier.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS:
        return nom_fichier.rsplit(".", 1)[1].lower()
    mimetype = getattr(fichier, "mimetype", "") or getattr(fichier, "content_type", "") or ""
    correspondance = {
        "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
        "image/gif": "gif", "image/webp": "webp",
    }
    return correspondance.get(mimetype, "jpg")  # jpg par défaut si type inconnu mais accepté


TYPES_MIME = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}


def sauvegarder_fichier_en_base(chemin, fichier, extension):
    """Stocke l'image dans la base de données (le disque de Render est effacé régulièrement)."""
    contenu = fichier.read()
    db = get_db()
    db.execute(
        """INSERT INTO fichiers_media (chemin, contenu, mimetype) VALUES (?, ?, ?)
           ON CONFLICT (chemin) DO UPDATE
           SET contenu = EXCLUDED.contenu, mimetype = EXCLUDED.mimetype""",
        (chemin, db_compat.Binary(contenu), TYPES_MIME.get(extension, "image/jpeg")),
    )
    db.commit()


def enregistrer_photo(fichier, lien_unique):
    """Sauvegarde la photo uploadée et renvoie le chemin à stocker en base."""
    if not fichier or fichier.filename == "":
        return None
    if not fichier_autorise(fichier):
        flash("Format de photo non autorisé (formats acceptés : jpg, jpeg, png, gif, webp).", "danger")
        return None
    extension = extension_depuis_fichier(fichier)
    nom_final = secure_filename(f"{lien_unique}.{extension}")
    chemin = f"uploads/{nom_final}"
    sauvegarder_fichier_en_base(chemin, fichier, extension)
    return chemin


def enregistrer_preuve_mission(fichier, completion_id):
    """Sauvegarde la capture d'écran envoyée comme preuve de mission."""
    if not fichier or fichier.filename == "":
        flash("Aucune capture d'écran reçue — vérifiez que vous avez bien sélectionné une image.", "danger")
        return None
    if not fichier_autorise(fichier):
        flash("Format d'image non autorisé (formats acceptés : jpg, jpeg, png, gif, webp).", "danger")
        return None
    extension = extension_depuis_fichier(fichier)
    nom_final = secure_filename(f"preuve-{completion_id}-{secrets.token_hex(2)}.{extension}")
    chemin = f"uploads/missions/{nom_final}"
    sauvegarder_fichier_en_base(chemin, fichier, extension)
    return chemin


@app.route("/fichiers/<path:chemin>")
def servir_fichier(chemin):
    """Affiche une photo stockée dans la base de données."""
    ligne = get_db().execute(
        "SELECT contenu, mimetype FROM fichiers_media WHERE chemin = ?", (chemin,)
    ).fetchone()
    if ligne is None:
        abort(404)
    reponse = make_response(bytes(ligne["contenu"]))
    reponse.headers["Content-Type"] = ligne["mimetype"]
    reponse.headers["Cache-Control"] = "public, max-age=60"
    return reponse


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

# ----------------------------------------------------------------------
# INSTALLATION SUR LE TÉLÉPHONE (application web installable / PWA)
# ----------------------------------------------------------------------

@app.route("/carte/<lien_unique>/manifest.webmanifest")
def manifest_client(lien_unique):
    """Description de l'application : à l'ouverture, elle affiche directement la carte du client."""
    client = get_db().execute(
        "SELECT prenom FROM clients_fidelite WHERE lien_unique = ?", (lien_unique,)
    ).fetchone()
    if client is None:
        abort(404)
    adresse_carte = url_for("carte_client", lien_unique=lien_unique)
    manifeste = {
        "id": adresse_carte,
        "name": "FIDÉLITÉ+ — Sandwich du Roi",
        "short_name": "FIDÉLITÉ+",
        "description": "Votre carte de fidélité Sandwich du Roi",
        "lang": "fr",
        "start_url": adresse_carte,
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#8B0000",
        "theme_color": "#8B0000",
        "icons": [
            {"src": url_for("static", filename="images/icone-app-192.png"),
             "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": url_for("static", filename="images/icone-app-512.png"),
             "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    reponse = make_response(json.dumps(manifeste, ensure_ascii=False))
    reponse.headers["Content-Type"] = "application/manifest+json; charset=utf-8"
    reponse.headers["Cache-Control"] = "no-cache"
    return reponse


SERVICE_WORKER_JS = """
// FIDÉLITÉ+ — service worker minimal : toujours le réseau d'abord (points à jour),
// avec une page de secours si le téléphone est hors connexion.
const CACHE = 'fidelite-statique-v1';
const PAGE_HORS_LIGNE = '<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8">' +
  '<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Hors connexion</title></head>' +
  '<body style="font-family:sans-serif;text-align:center;padding:3rem 1.5rem;background:#8B0000;color:#fff">' +
  '<h1>📶 Pas de connexion</h1><p>Vérifiez votre connexion internet puis rouvrez votre carte FIDÉLITÉ+.</p>' +
  '<button onclick="location.reload()" style="padding:.8rem 1.4rem;border:0;border-radius:8px;font-size:1rem">Réessayer</button>' +
  '</body></html>';

self.addEventListener('install', function () { self.skipWaiting(); });

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (noms) {
      return Promise.all(noms.filter(function (n) { return n !== CACHE; }).map(function (n) { return caches.delete(n); }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (event) {
  const requete = event.request;
  if (requete.method !== 'GET') return;
  const url = new URL(requete.url);
  if (url.origin !== self.location.origin) return;

  if (requete.mode === 'navigate') {
    event.respondWith(fetch(requete).catch(function () {
      return new Response(PAGE_HORS_LIGNE, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
    }));
    return;
  }

  if (url.pathname.indexOf('/static/') === 0) {
    event.respondWith(
      fetch(requete).then(function (reponse) {
        const copie = reponse.clone();
        caches.open(CACHE).then(function (cache) { cache.put(requete, copie); });
        return reponse;
      }).catch(function () { return caches.match(requete); })
    );
  }
});
"""


@app.route("/sw.js")
def service_worker():
    reponse = make_response(SERVICE_WORKER_JS)
    reponse.headers["Content-Type"] = "application/javascript; charset=utf-8"
    reponse.headers["Cache-Control"] = "no-cache"
    reponse.headers["Service-Worker-Allowed"] = "/"
    return reponse


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

    mes_bons = db.execute(
        """SELECT * FROM echanges_points
           WHERE client_actuel_id = ? AND statut = 'en_attente'
           ORDER BY date_demande DESC""",
        (client["id"],),
    ).fetchall()

    missions_brutes = db.execute(
        "SELECT * FROM missions WHERE actif = 1 ORDER BY type_mission DESC, points_recompense ASC"
    ).fetchall()
    missions_affichees = []
    maintenant = datetime.now()
    profil_du_client = profil_client(db, client)
    for m in missions_brutes:
        if not mission_ciblee(db, m, client, profil_du_client):
            continue
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

        m_dict["heures_avant_rejeu"] = None
        if derniere_demande and derniere_demande["statut"] == "validee" and derniere_demande["date_validation"]:
            try:
                date_val = datetime.strptime(derniere_demande["date_validation"], "%Y-%m-%d %H:%M:%S")
                secondes_ecoulees = (maintenant - date_val).total_seconds()
                if secondes_ecoulees < 24 * 3600:
                    m_dict["heures_avant_rejeu"] = round((24 * 3600 - secondes_ecoulees) / 3600, 1)
            except (TypeError, ValueError):
                pass

        missions_affichees.append(m_dict)

    total_missions_actives = len(missions_affichees)
    missions_validees_aujourdhui = db.execute(
        """SELECT COUNT(*) AS n FROM missions_completees
           WHERE client_id = ? AND statut = 'validee'
                 AND substr(date_validation, 1, 10) = ?""",
        (client["id"], datetime.now().strftime("%Y-%m-%d")),
    ).fetchone()["n"]
    missions_validees_aujourdhui = min(missions_validees_aujourdhui, total_missions_actives)

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
        mes_bons=mes_bons,
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
        total_missions_actives=total_missions_actives,
        missions_validees_aujourdhui=missions_validees_aujourdhui,
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

    notifier(
        db, client["id"],
        f"✅ Vous avez offert {points} points à {destinataire['prenom']} {destinataire['nom']}. "
        f"Nouveau solde : {nouveau_solde_x} points."
    )
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

    code_bon = generer_code_bon(db)

    db.execute(
        """INSERT INTO echanges_points (client_id, client_actuel_id, points_echanges, nombre_bons, produit_nom, code_unique)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (client["id"], client["id"], produit["cout_points"], 0, produit["nom_produit"], code_bon),
    )
    db.commit()

    flash(
        f"🍔 Demande enregistrée : {produit['nom_produit']} contre {produit['cout_points']} points. "
        f"Votre nouveau solde : {nouveau_solde} points. Votre code de bon : {code_bon}",
        "success",
    )

    texte_whatsapp = (
        f"Bonjour Sandwich du Roi ! Je suis {client['prenom']} {client['nom']} ({client['numero']}). "
        f"Je souhaite échanger {produit['cout_points']} points contre : {produit['nom_produit']} (code {code_bon}). Merci de confirmer 🙏"
    )
    session["notification_whatsapp"] = {
        "lien": lien_whatsapp(WHATSAPP_SANDWICH_DU_ROI, texte_whatsapp),
        "nom_destinataire": "Sandwich du Roi",
    }

    return redirect(url_for("carte_client", lien_unique=lien_unique))


@app.route("/carte/<lien_unique>/bon/<int:echange_id>/offrir", methods=["POST"])
def offrir_bon(lien_unique, echange_id):
    db = get_db()
    client = db.execute(
        "SELECT * FROM clients_fidelite WHERE lien_unique = ?", (lien_unique,)
    ).fetchone()
    if client is None:
        abort(404)

    bon = db.execute(
        """SELECT * FROM echanges_points
           WHERE id = ? AND client_actuel_id = ? AND statut = 'en_attente'""",
        (echange_id, client["id"]),
    ).fetchone()
    if bon is None:
        flash("Ce bon n'est plus disponible ou ne vous appartient plus.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    numero_destinataire = request.form.get("numero_destinataire", "").strip()
    destinataire = db.execute(
        "SELECT * FROM clients_fidelite WHERE numero = ?", (numero_destinataire,)
    ).fetchone()

    if destinataire is None:
        flash("Aucun client trouvé avec ce numéro.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    if destinataire["id"] == client["id"]:
        flash("Vous ne pouvez pas vous offrir un bon à vous-même.", "danger")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    db.execute(
        "UPDATE echanges_points SET client_actuel_id = ? WHERE id = ?",
        (destinataire["id"], echange_id),
    )

    notifier(
        db, client["id"],
        f"🎁 Vous avez offert votre bon « {bon['produit_nom']} » (code {bon['code_unique']}) à {destinataire['prenom']} {destinataire['nom']}."
    )
    notifier(
        db, destinataire["id"],
        f"🎁 {client['prenom']} {client['nom']} vous a offert un bon « {bon['produit_nom']} » ! "
        f"Présentez le code {bon['code_unique']} au comptoir Sandwich du Roi."
    )
    db.commit()

    flash(f"🎁 Bon « {bon['produit_nom']} » offert à {destinataire['prenom']} {destinataire['nom']} !", "success")
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
            f"révèle {libelle_recompense(mission)} !"
            + (f" Nouveau solde : {client['score_points']} points." if (mission["points_recompense"] or 0) > 0 else "")
            + (" Retrouvez votre bon dans l'onglet 🍔 Échanger." if recompense_en_nature(mission) else "")
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

    if client["nombre_achats"] < 1:
        flash("Les missions se débloquent après votre premier achat.", "warning")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    mission = db.execute(
        "SELECT * FROM missions WHERE id = ? AND actif = 1 AND type_mission = 'manuelle'",
        (mission_id,),
    ).fetchone()
    if mission is None or not mission_ciblee(db, mission, client):
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

    derniere_validation = db.execute(
        """SELECT date_validation FROM missions_completees
           WHERE client_id = ? AND mission_id = ? AND statut = 'validee'
           ORDER BY date_validation DESC LIMIT 1""",
        (client["id"], mission_id),
    ).fetchone()
    if derniere_validation and derniere_validation["date_validation"]:
        try:
            date_val = datetime.strptime(derniere_validation["date_validation"], "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - date_val).total_seconds() < 24 * 3600:
                flash("Vous avez déjà réalisé cette mission aujourd'hui — revenez demain pour la refaire !", "warning")
                return redirect(url_for("carte_client", lien_unique=lien_unique))
        except (TypeError, ValueError):
            pass

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

    if client["nombre_achats"] < 1:
        flash("Les missions se débloquent après votre premier achat.", "warning")
        return redirect(url_for("carte_client", lien_unique=lien_unique))

    mission = db.execute(
        "SELECT * FROM missions WHERE id = ? AND actif = 1 AND type_mission = 'manuelle'",
        (mission_id,),
    ).fetchone()
    if mission is None or not mission_ciblee(db, mission, client):
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
               WHERE nom ILIKE ? OR prenom ILIKE ? OR numero ILIKE ?
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

    profil = profil_client(db, client)
    segments_client = [
        cle for cle in SEGMENTS_CLIENTS if client_dans_segment(profil, cle)
    ]
    missions_perso = db.execute(
        """SELECT missions.* FROM missions_clients
           JOIN missions ON missions.id = missions_clients.mission_id
           WHERE missions_clients.client_id = ? ORDER BY missions.id DESC""",
        (client_id,),
    ).fetchall()
    missions_assignables = db.execute(
        """SELECT * FROM missions
           WHERE ciblage = 'clients' AND actif = 1
                 AND id NOT IN (SELECT mission_id FROM missions_clients WHERE client_id = ?)
           ORDER BY id DESC""",
        (client_id,),
    ).fetchall()
    missions_realisees = db.execute(
        "SELECT COUNT(*) AS n FROM missions_completees WHERE client_id = ? AND statut = 'validee'",
        (client_id,),
    ).fetchone()["n"]
    return render_template(
        "fiche_client.html", client=client, historique=historique, niveaux=niveaux,
        niveau_effectif=niveau_effectif, niveau_acquis=niveau_acquis,
        jours_inactivite=jours_inactivite, statut_degrade=statut_degrade,
        alerte_preventive=alerte_preventive,
        profil=profil, segments_client=segments_client, segments=SEGMENTS_CLIENTS,
        missions_perso=missions_perso, missions_assignables=missions_assignables,
        missions_realisees=missions_realisees, objectifs=OBJECTIFS_COMMERCIAUX,
        types_recompense=TYPES_RECOMPENSE,
        menu=db.execute("SELECT * FROM menu_echange WHERE actif = 1 ORDER BY nom_produit").fetchall(),
    )


@app.route("/admin/client/<int:client_id>/missions/assigner", methods=["POST"])
@admin_requis
def assigner_mission_client(client_id):
    db = get_db()
    client = db.execute("SELECT * FROM clients_fidelite WHERE id = ?", (client_id,)).fetchone()
    if client is None:
        abort(404)
    mission_brut = request.form.get("mission_id", "")
    mission = None
    if mission_brut.isdigit():
        mission = db.execute(
            "SELECT * FROM missions WHERE id = ? AND ciblage = 'clients'", (int(mission_brut),)
        ).fetchone()
    if mission is None:
        flash("Choisissez une mission personnalisée à assigner.", "danger")
        return redirect(url_for("fiche_client", client_id=client_id))
    deja = db.execute(
        "SELECT 1 FROM missions_clients WHERE mission_id = ? AND client_id = ?",
        (mission["id"], client_id),
    ).fetchone()
    if not deja:
        db.execute(
            "INSERT INTO missions_clients (mission_id, client_id) VALUES (?, ?)",
            (mission["id"], client_id),
        )
        notifier(db, client_id, f"🎯 Nouvelle mission pour vous : {mission['titre']}")
        db.commit()
    flash(f"Mission « {mission['titre']} » assignée à {client['prenom']}.", "success")
    return redirect(url_for("fiche_client", client_id=client_id))


@app.route("/admin/client/<int:client_id>/missions/creer", methods=["POST"])
@admin_requis
def creer_mission_client(client_id):
    db = get_db()
    client = db.execute("SELECT * FROM clients_fidelite WHERE id = ?", (client_id,)).fetchone()
    if client is None:
        abort(404)
    titre = request.form.get("titre", "").strip()
    if not titre:
        flash("Le titre de la mission est obligatoire.", "danger")
        return redirect(url_for("fiche_client", client_id=client_id))
    description = request.form.get("description", "").strip()
    recompense = lire_recompense_formulaire(db)
    if recompense is None:
        return redirect(url_for("fiche_client", client_id=client_id))
    type_recompense, points, produit_id, recompense_texte = recompense
    icone = request.form.get("icone", "🎯").strip() or "🎯"
    objectif = request.form.get("objectif", "").strip() or None

    mission_id = db.execute(
        """INSERT INTO missions (titre, description, points_recompense, type_mission, seuil_jours,
                                 duree_heures, icone, actif, ciblage, objectif,
                                 type_recompense, produit_id, recompense_texte)
           VALUES (?, ?, ?, 'manuelle', NULL, NULL, ?, 1, 'clients', ?, ?, ?, ?)
           RETURNING id""",
        (titre, description, points, icone, objectif, type_recompense, produit_id, recompense_texte),
    ).fetchone()["id"]
    db.execute(
        "INSERT INTO missions_clients (mission_id, client_id) VALUES (?, ?)", (mission_id, client_id)
    )
    notifier(db, client_id, f"🎯 Nouvelle mission pour vous : {titre}")
    db.commit()
    flash(f"Mission personnalisée créée pour {client['prenom']}.", "success")
    return redirect(url_for("fiche_client", client_id=client_id))


@app.route("/admin/client/<int:client_id>/missions/<int:mission_id>/retirer", methods=["POST"])
@admin_requis
def retirer_mission_client(client_id, mission_id):
    db = get_db()
    db.execute(
        "DELETE FROM missions_clients WHERE mission_id = ? AND client_id = ?", (mission_id, client_id)
    )
    db.commit()
    flash("Mission retirée pour ce client.", "warning")
    return redirect(url_for("fiche_client", client_id=client_id))


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
        db.execute("UPDATE niveaux_fidelite SET palier_plancher = 0 WHERE id != ?", (niveau_id,))
    if debloque_partage:
        db.execute("UPDATE niveaux_fidelite SET debloque_partage = 0 WHERE id != ?", (niveau_id,))

    db.execute(
        """UPDATE niveaux_fidelite
           SET nom_niveau = ?, nombre_achats_requis = ?, avantages = ?,
               couleur = ?, icone = ?, actif = ?, seuil_partage_points = ?, palier_plancher = ?, debloque_partage = ?
           WHERE id = ?""",
        (nom_niveau, seuil, avantages, couleur, icone, actif, seuil_partage, palier_plancher, debloque_partage, niveau_id),
    )
    db.commit()

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
        """SELECT echanges_points.*,
                  porteur.nom AS nom, porteur.prenom AS prenom, porteur.numero AS numero,
                  demandeur.nom AS nom_demandeur, demandeur.prenom AS prenom_demandeur
           FROM echanges_points
           LEFT JOIN clients_fidelite AS porteur ON porteur.id = echanges_points.client_actuel_id
           LEFT JOIN clients_fidelite AS demandeur ON demandeur.id = echanges_points.client_id
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
# ADMINISTRATION — MISSIONS
# ----------------------------------------------------------------------

@app.route("/admin/missions")
@admin_requis
def gestion_missions():
    db = get_db()
    missions = db.execute("SELECT * FROM missions ORDER BY id ASC").fetchall()
    demandes = db.execute(
        """SELECT missions_completees.*, missions.titre, missions.points_recompense, missions.icone,
                  missions.type_recompense, missions.recompense_texte,
                  clients_fidelite.nom, clients_fidelite.prenom, clients_fidelite.numero
           FROM missions_completees
           JOIN missions ON missions.id = missions_completees.mission_id
           JOIN clients_fidelite ON clients_fidelite.id = missions_completees.client_id
           WHERE missions_completees.statut IN ('en_attente', 'validee', 'rejetee')
           ORDER BY
               CASE WHEN missions_completees.statut = 'en_attente' THEN 0 ELSE 1 END,
               missions_completees.date_demande DESC"""
    ).fetchall()
    clients = db.execute(
        "SELECT id, nom, prenom, nombre_achats, date_creation FROM clients_fidelite ORDER BY prenom, nom"
    ).fetchall()
    cibles = {}
    for ligne in db.execute("SELECT mission_id, client_id FROM missions_clients").fetchall():
        cibles.setdefault(ligne["mission_id"], []).append(ligne["client_id"])
    profils = profils_clients(db, clients)
    nb_concernes = {}
    for m in missions:
        if m["ciblage"] == "clients":
            nb_concernes[m["id"]] = len(cibles.get(m["id"], []))
        elif m["ciblage"] == "segment":
            nb_concernes[m["id"]] = sum(
                1 for c in clients if client_dans_segment(profils[c["id"]], m["segment"], m["segment_param"])
            )
        else:
            nb_concernes[m["id"]] = len(clients)
    return render_template(
        "gestion_missions.html", missions=missions, demandes=demandes, clients=clients,
        cibles=cibles, nb_concernes=nb_concernes, segments=SEGMENTS_CLIENTS,
        objectifs=OBJECTIFS_COMMERCIAUX, types_recompense=TYPES_RECOMPENSE,
        menu=db.execute("SELECT * FROM menu_echange WHERE actif = 1 ORDER BY nom_produit").fetchall(),
    )


@app.route("/admin/missions/ajouter", methods=["POST"])
@admin_requis
def ajouter_mission():
    db = get_db()
    titre = request.form.get("titre", "").strip()
    description = request.form.get("description", "").strip()
    type_mission = request.form.get("type_mission", "manuelle")
    seuil_jours_brut = request.form.get("seuil_jours", "").strip()
    duree_heures_brut = request.form.get("duree_heures", "").strip()
    icone = request.form.get("icone", "🎯").strip() or "🎯"

    if not titre:
        flash("Le titre de la mission est obligatoire.", "danger")
        return redirect(url_for("gestion_missions"))

    recompense = lire_recompense_formulaire(db)
    if recompense is None:
        return redirect(url_for("gestion_missions"))
    type_recompense, points, produit_id, recompense_texte = recompense

    seuil_jours = int(seuil_jours_brut) if seuil_jours_brut.isdigit() else None
    duree_heures = int(duree_heures_brut) if duree_heures_brut.isdigit() else None
    ciblage, segment, segment_param, objectif, ids_clients = lire_ciblage_formulaire(db, type_mission)

    mission_id = db.execute(
        """INSERT INTO missions (titre, description, points_recompense, type_mission, seuil_jours,
                                 duree_heures, icone, actif, ciblage, segment, segment_param, objectif,
                                 type_recompense, produit_id, recompense_texte)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
           RETURNING id""",
        (titre, description, points, type_mission, seuil_jours, duree_heures, icone,
         ciblage, segment, segment_param, objectif, type_recompense, produit_id, recompense_texte),
    ).fetchone()["id"]
    if ciblage == "clients":
        enregistrer_clients_cibles(db, mission_id, titre, ids_clients)
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
    type_mission = request.form.get("type_mission", mission["type_mission"])
    seuil_jours_brut = request.form.get("seuil_jours", "").strip()
    duree_heures_brut = request.form.get("duree_heures", "").strip()
    icone = request.form.get("icone", mission["icone"])
    actif = 1 if request.form.get("actif") == "on" else 0

    recompense = lire_recompense_formulaire(db)
    if recompense is None:
        return redirect(url_for("gestion_missions"))
    type_recompense, points, produit_id, recompense_texte = recompense

    seuil_jours = int(seuil_jours_brut) if seuil_jours_brut.isdigit() else None
    duree_heures = int(duree_heures_brut) if duree_heures_brut.isdigit() else None

    ciblage, segment, segment_param, objectif, ids_clients = lire_ciblage_formulaire(db, type_mission)

    db.execute(
        """UPDATE missions
           SET titre = ?, description = ?, points_recompense = ?, type_mission = ?,
               seuil_jours = ?, duree_heures = ?, icone = ?, actif = ?,
               ciblage = ?, segment = ?, segment_param = ?, objectif = ?,
               type_recompense = ?, produit_id = ?, recompense_texte = ?
           WHERE id = ?""",
        (titre, description, points, type_mission, seuil_jours, duree_heures, icone, actif,
         ciblage, segment, segment_param, objectif, type_recompense, produit_id, recompense_texte, mission_id),
    )
    enregistrer_clients_cibles(db, mission_id, titre, ids_clients if ciblage == "clients" else [])
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

    code_bon = accorder_recompense(db, mission, client["id"])
    db.execute(
        "UPDATE missions_completees SET statut = 'validee', date_validation = ?, coffre_ouvert = 0 WHERE id = ?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), completion_id),
    )
    db.commit()
    if code_bon:
        flash(f"Mission validée : {libelle_recompense(mission)} accordé. Bon n°{code_bon} à honorer dans « Échanges ».", "success")
    else:
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
