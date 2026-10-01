"""
init_db.py — Création et initialisation de la base de données FIDÉLITÉ+
Indépendante de la base de données de SANDWICH_DU_ROI_APP.

Version PostgreSQL (Render) : la base est désignée par la variable
d'environnement DATABASE_URL.

Crée toutes les tables nécessaires (si elles n'existent pas déjà) et
pré-remplit les niveaux, le menu de récompenses et les missions par défaut.
"""

import db_compat


def creer_tables(conn):
    # Les dates sont stockées en texte "AAAA-MM-JJ HH:MM:SS", comme avant.
    defaut_date = "DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI:SS')"

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS clients_fidelite (
            id SERIAL PRIMARY KEY,
            lien_unique TEXT NOT NULL UNIQUE,
            photo TEXT,
            nom TEXT NOT NULL,
            prenom TEXT NOT NULL,
            numero TEXT,
            age INTEGER,
            lieu_livraison TEXT,
            nombre_achats INTEGER NOT NULL DEFAULT 0,
            score_points INTEGER NOT NULL DEFAULT 0,
            statut_actuel TEXT NOT NULL DEFAULT 'Bienvenue Prince',
            date_creation TEXT NOT NULL {defaut_date},
            date_derniere_modification TEXT NOT NULL {defaut_date}
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS niveaux_fidelite (
            id SERIAL PRIMARY KEY,
            nom_niveau TEXT NOT NULL,
            nombre_achats_requis INTEGER NOT NULL,
            avantages TEXT,
            couleur TEXT DEFAULT '#D4AF37',
            icone TEXT DEFAULT '🏆',
            actif INTEGER NOT NULL DEFAULT 1,
            seuil_partage_points INTEGER,
            palier_plancher INTEGER NOT NULL DEFAULT 0,
            debloque_partage INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS historique_achats (
            id SERIAL PRIMARY KEY,
            client_id INTEGER NOT NULL REFERENCES clients_fidelite (id),
            date_achat TEXT NOT NULL {defaut_date},
            montant DOUBLE PRECISION DEFAULT 0,
            points_ajoutes INTEGER DEFAULT 0,
            note TEXT
        )
    """)

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS partages_points (
            id SERIAL PRIMARY KEY,
            client_source_id INTEGER NOT NULL REFERENCES clients_fidelite (id),
            client_dest_id INTEGER NOT NULL REFERENCES clients_fidelite (id),
            points_partages INTEGER NOT NULL,
            date_partage TEXT NOT NULL {defaut_date}
        )
    """)

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS echanges_points (
            id SERIAL PRIMARY KEY,
            client_id INTEGER NOT NULL REFERENCES clients_fidelite (id),
            client_actuel_id INTEGER REFERENCES clients_fidelite (id),
            points_echanges INTEGER NOT NULL,
            nombre_bons INTEGER NOT NULL,
            statut TEXT NOT NULL DEFAULT 'en_attente',
            code_unique TEXT,
            date_demande TEXT NOT NULL {defaut_date},
            date_traitement TEXT,
            produit_nom TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS menu_echange (
            id SERIAL PRIMARY KEY,
            nom_produit TEXT NOT NULL,
            cout_points INTEGER NOT NULL,
            description TEXT,
            actif INTEGER NOT NULL DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS missions (
            id SERIAL PRIMARY KEY,
            titre TEXT NOT NULL,
            description TEXT,
            points_recompense INTEGER NOT NULL,
            type_mission TEXT NOT NULL DEFAULT 'manuelle',
            seuil_jours INTEGER,
            duree_heures INTEGER,
            icone TEXT DEFAULT '🎯',
            actif INTEGER NOT NULL DEFAULT 1
        )
    """)

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS missions_completees (
            id SERIAL PRIMARY KEY,
            client_id INTEGER NOT NULL REFERENCES clients_fidelite (id),
            mission_id INTEGER NOT NULL REFERENCES missions (id),
            statut TEXT NOT NULL DEFAULT 'en_attente',
            date_debut TEXT,
            preuve_photo TEXT,
            coffre_ouvert INTEGER NOT NULL DEFAULT 0,
            date_demande TEXT NOT NULL {defaut_date},
            date_validation TEXT
        )
    """)

    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY,
            client_id INTEGER NOT NULL REFERENCES clients_fidelite (id),
            message TEXT NOT NULL,
            lu INTEGER NOT NULL DEFAULT 0,
            date_creation TEXT NOT NULL {defaut_date}
        )
    """)

    # Photos des clients et captures de preuve : stockées dans la base
    # (le disque de Render gratuit est effacé à chaque redémarrage).
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fichiers_media (
            chemin TEXT PRIMARY KEY,
            contenu BYTEA NOT NULL,
            mimetype TEXT NOT NULL
        )
    """)

    conn.commit()


def seed_niveaux(conn):
    cur = conn.execute("SELECT COUNT(*) FROM niveaux_fidelite")
    if cur.fetchone()[0] > 0:
        return  # déjà initialisé, on ne double pas les niveaux

    niveaux_par_defaut = [
        ("Citoyen d'Honneur", 0, "Bienvenue dans ROYAL+ ! Votre premier achat vous ouvrira les portes de la noblesse.", "#8B0000", "🌱", 1, None, 0, 0),
        ("Noble", 2, "5% de réduction débloquée", "#D4AF37", "⚜️", 1, None, 1, 0),
        ("Baron / Baronne", 5, "10% de réduction", "#D4AF37", "🎖️", 1, None, 0, 0),
        ("Duc / Duchesse", 12, "Un sandwich offert", "#D4AF37", "🏰", 1, None, 0, 0),
        ("Prince / Princesse", 17, "Un menu offert", "#D4AF37", "🤴", 1, None, 0, 1),
        ("Roi / Reine", 25, "Privilège VIP : livraison gratuite + priorité de commande", "#8B0000", "👑", 1, None, 0, 0),
        ("Empereur / Impératrice", 32, "Récompense ultime : 2 menus offerts + statut permanent", "#8B0000", "🏆", 1, None, 0, 0),
    ]

    conn.executemany("""
        INSERT INTO niveaux_fidelite (nom_niveau, nombre_achats_requis, avantages, couleur, icone, actif, seuil_partage_points, palier_plancher, debloque_partage)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, niveaux_par_defaut)

    conn.commit()


def seed_menu_echange(conn):
    cur = conn.execute("SELECT COUNT(*) FROM menu_echange")
    if cur.fetchone()[0] > 0:
        return  # déjà initialisé

    menu_par_defaut = [
        ("Sandwich ordinaire offert", 150, "Un sandwich ordinaire au choix, offert", 1),
        ("Boisson offerte", 50, "Jus, café ou lait caillé au choix", 1),
        ("Chocolat chaud offert", 100, "Un chocolat chaud offert", 1),
    ]

    conn.executemany("""
        INSERT INTO menu_echange (nom_produit, cout_points, description, actif)
        VALUES (?, ?, ?, ?)
    """, menu_par_defaut)

    conn.commit()


def seed_missions(conn):
    cur = conn.execute("SELECT COUNT(*) FROM missions")
    if cur.fetchone()[0] > 0:
        return  # déjà initialisé

    missions_par_defaut = [
        ("Statut Sandwich du Roi", "Partagez une photo de notre logo ou d'un de nos plats en statut WhatsApp pendant 24h, puis envoyez une capture d'écran", 30, "manuelle", None, 24, "📸", 1),
        ("Retour éclair", "Revenez dans les 7 jours suivant votre dernier achat — crédité automatiquement", 15, "auto_retour", 7, None, "⚡", 1),
        ("Parrainage royal", "Parrainez un ami qui s'inscrit à ROYAL+", 30, "manuelle", None, None, "🤝", 1),
        ("Avis 5 étoiles", "Laissez un avis 5 étoiles sur notre page (Google, Facebook...) puis envoyez une capture d'écran", 25, "manuelle", None, None, "⭐", 1),
        ("Explorateur du menu", "Goûtez un article que vous n'avez jamais commandé", 20, "manuelle", None, None, "🎲", 1),
    ]

    conn.executemany("""
        INSERT INTO missions (titre, description, points_recompense, type_mission, seuil_jours, duree_heures, icone, actif)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, missions_par_defaut)

    conn.commit()


def main():
    conn = db_compat.connecter()
    creer_tables(conn)
    seed_niveaux(conn)
    seed_menu_echange(conn)
    seed_missions(conn)
    conn.close()
    print("Base de données FIDÉLITÉ+ prête (PostgreSQL).")


if __name__ == "__main__":
    main()
