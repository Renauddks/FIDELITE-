"""
demarrer_app.py — Lanceur en un clic pour FIDÉLITÉ+ (Pydroid 3)

Ouvrir ce fichier dans Pydroid 3 et appuyer sur ▶ (Play).
Le serveur démarre, la base de données se crée automatiquement si besoin,
puis ouvre ton navigateur sur http://127.0.0.1:5000
"""

import webbrowser
import threading
import time
import app as application_fidelite


def ouvrir_navigateur():
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:5000")


if __name__ == "__main__":
    print("=" * 50)
    print("   FIDÉLITÉ+ — Sandwich du Roi")
    print("   Démarrage du serveur local...")
    print("=" * 50)

    application_fidelite.init_db_if_needed()

    threading.Thread(target=ouvrir_navigateur, daemon=True).start()

    application_fidelite.app.run(host="127.0.0.1", port=5000, debug=False)
