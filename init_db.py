"""
init_db.py — Création et initialisation de la base de données FIDÉLITÉ+
Indépendante de la base de données de SANDWICH_DU_ROI_APP.

Utilisation :
    python init_db.py

Crée le fichier fidelite.db avec 3 tables :
    - clients_fidelite
    - niveaux_fidelite
    - historique_achats
Et pré-remplit 5 niveaux de fidélité par défaut.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fidelite.db")


def creer_tables(conn):
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients_fidelite (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lien_unique TEXT NOT NULL UNIQUE,
            photo TEXT,
            nom TEXT NOT NULL,
            prenom TEXT NOT NULL,
            numero TEXT,
            age INTEGER,
            nombre_achats INTEGER NOT NULL DEFAULT 0,
            score_points INTEGER NOT NULL DEFAULT 0,
            statut_actuel TEXT NOT NULL DEFAULT 'Bienvenue Prince',
            date_creation TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            date_derniere_modification TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS niveaux_fidelite (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom_niveau TEXT NOT NULL,
            nombre_achats_requis INTEGER NOT NULL,
            avantages TEXT,
            couleur TEXT DEFAULT '#D4AF37',
            icone TEXT DEFAULT '🏆',
            actif INTEGER NOT NULL DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS historique_achats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            date_achat TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            montant REAL DEFAULT 0,
            points_ajoutes INTEGER DEFAULT 0,
            note TEXT,
            FOREIGN KEY (client_id) REFERENCES clients_fidelite (id)
        )
    """)

    conn.commit()


def seed_niveaux(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM niveaux_fidelite")
    if cur.fetchone()[0] > 0:
        return  # déjà initialisé, on ne double pas les niveaux

    niveaux_par_defaut = [
        ("Nouveau Client", 0, "Bienvenue chez Sandwich du Roi ! Votre carte de fidélité est activée.", "#8B0000", "👑", 1),
        ("Bienvenue Prince", 1, "5% de réduction sur votre prochain achat", "#D4AF37", "🥉", 1),
        ("Client Fidèle", 5, "10% de réduction + 1 sandwich offert", "#D4AF37", "🥈", 1),
        ("Client Royal", 10, "15% de réduction + livraison gratuite", "#D4AF37", "🥇", 1),
        ("Client Légendaire", 20, "20% de réduction + 2 sandwichs offerts", "#8B0000", "💎", 1),
    ]

    cur.executemany("""
        INSERT INTO niveaux_fidelite (nom_niveau, nombre_achats_requis, avantages, couleur, icone, actif)
        VALUES (?, ?, ?, ?, ?, ?)
    """, niveaux_par_defaut)

    conn.commit()


def main():
    conn = sqlite3.connect(DB_PATH)
    creer_tables(conn)
    seed_niveaux(conn)
    conn.close()
    print(f"Base de données FIDÉLITÉ+ prête : {DB_PATH}")


if __name__ == "__main__":
    main()
