"""
db_compat.py — Adaptateur PostgreSQL pour FIDÉLITÉ+

Permet au reste de l'application de continuer à écrire ses requêtes
comme avec SQLite ("?" comme emplacements, lecture des colonnes par nom),
tout en parlant à une base PostgreSQL (celle de Render).

La connexion se fait avec la variable d'environnement DATABASE_URL.
"""

import os

import psycopg2
import psycopg2.extras


def Binary(octets):
    """Enveloppe des octets (ex. une photo) pour les stocker dans PostgreSQL."""
    return psycopg2.Binary(octets)


def _convertir(sql):
    """'?' (style SQLite) -> '%s' (style PostgreSQL), en protégeant les '%' littéraux."""
    return sql.replace("%", "%%").replace("?", "%s")


class Connexion:
    def __init__(self, url):
        self._conn = psycopg2.connect(url)

    def execute(self, sql, params=None):
        curseur = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        if params:
            curseur.execute(_convertir(sql), tuple(params))
        else:
            curseur.execute(sql)
        return curseur

    def executemany(self, sql, liste_params):
        curseur = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        curseur.executemany(_convertir(sql), [tuple(p) for p in liste_params])
        return curseur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def connecter():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "La variable d'environnement DATABASE_URL est absente. "
            "Sur Render : ajoutez-la dans l'onglet Environment du service "
            "(copiez l'Internal Database URL de votre base PostgreSQL)."
        )
    # Certains fournisseurs donnent une URL commençant par postgres://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return Connexion(url)
