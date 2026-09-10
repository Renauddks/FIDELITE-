====================================================
FIDÉLITÉ+ — Programme de fidélité Sandwich du Roi
====================================================

Application INDÉPENDANTE de l'application de gestion SANDWICH_DU_ROI_APP
(base de données séparée : fidelite.db).


1. UTILISATION SUR TÉLÉPHONE (PYDROID 3) — MODE TEST / LOCAL
--------------------------------------------------------------
1. Copier tout le dossier FIDELITE_PLUS_APP sur le téléphone.
2. Ouvrir Pydroid 3, installer les dépendances une seule fois :
   - Ouvrir le terminal Pydroid (icône Pip / Terminal)
   - Taper : pip install flask
3. Ouvrir le fichier demarrer_app.py dans Pydroid 3.
4. Appuyer sur ▶ (Play).
5. Le navigateur s'ouvre automatiquement sur http://127.0.0.1:5000

⚠️ En mode local, seul le téléphone qui fait tourner le serveur peut
   accéder à l'application. Les liens individuels des clients NE
   FONCTIONNENT PAS à distance dans ce mode — ce mode sert uniquement
   à tester le fonctionnement avant la mise en ligne.


2. MOT DE PASSE ADMINISTRATEUR
--------------------------------
Mot de passe par défaut : SandwichRoi2026

Pour le changer :
- En local : définir la variable d'environnement ADMIN_PASSWORD_FIDELITE
  avant de lancer l'application.
- En ligne (Render) : ajouter/modifier la variable d'environnement
  ADMIN_PASSWORD_FIDELITE dans les paramètres du service.

⚠️ Changer ce mot de passe avant le lancement public de la campagne
   (1er octobre 2026).


3. PASSAGE EN LIGNE (RENDER) — AVANT LE 30 SEPTEMBRE 2026
------------------------------------------------------------
Pour que les clients puissent ouvrir leur lien individuel depuis
n'importe où, l'application doit être hébergée en ligne.

Étapes :
1. Créer un dépôt GitHub (public ou privé) et y déposer tout le
   contenu de ce dossier.
2. Créer un service web sur Render, relié à ce dépôt :
   - Commande de build : pip install -r requirements.txt
   - Commande de démarrage : gunicorn app:app
   - Plan : Free (gratuit)
3. Ajouter les variables d'environnement nécessaires
   (ADMIN_PASSWORD_FIDELITE au minimum).

⚠️ IMPORTANT — STOCKAGE DES DONNÉES :
Cette version utilise un fichier SQLite local (fidelite.db). Sur le
plan gratuit Render, ce fichier est effacé à chaque redémarrage ou
redéploiement du service (c'est ce qui s'est produit avec
l'application principale Sandwich du Roi). Cette version convient
pour un TEST en conditions réelles, mais PAS pour la campagne réelle
avec de vraies données clients.

Avant le lancement du 1er octobre, prévoir la migration vers une base
PostgreSQL gratuite Render (comme cela a été fait pour l'application
principale), à créer la veille du lancement (30 septembre) pour
profiter au mieux des 30 jours gratuits pendant la campagne.


4. STRUCTURE DES FICHIERS
----------------------------
FIDELITE_PLUS_APP/
├── app.py                  → serveur Flask (toutes les routes)
├── init_db.py               → création de la base de données + niveaux par défaut
├── demarrer_app.py          → lanceur en un clic (Pydroid 3)
├── requirements.txt         → dépendances (pour Render)
├── fidelite.db               → base de données (créée automatiquement)
├── templates/                → pages HTML
│   ├── _base.html
│   ├── accueil.html
│   ├── admin_login.html
│   ├── admin_dashboard.html
│   ├── fiche_client.html
│   ├── gestion_niveaux.html
│   └── carte_client.html     → la carte de fidélité elle-même
└── static/
    ├── css/style.css         → design (couleurs Or/Bordeaux, cercle clignotant)
    ├── js/script.js
    └── uploads/               → photos des clients


5. UTILISATION AU QUOTIDIEN
------------------------------
- Créer un client : tableau de bord admin → formulaire "Ajouter un client"
- Copier son lien individuel : bouton "📋 Copier" dans la liste, à
  envoyer par WhatsApp/SMS/email
- Après un achat : ouvrir la fiche du client → "Enregistrer un achat"
  (la case suivante se coche automatiquement, le statut se met à jour
  tout seul si un palier est atteint)
- Modifier les niveaux et leurs avantages : "🏆 Gérer les niveaux"
- Le client peut lui-même modifier sa photo, son nom, prénom, numéro
  et âge depuis sa carte (bouton "✏️ Modifier mes infos") — il ne
  peut pas modifier son statut, ses points ou ses achats.


6. DÉPANNAGE
---------------
- "Adresse déjà utilisée" au démarrage local → un autre programme
  utilise déjà le port 5000, fermer les autres instances de l'app.
- Photo qui ne s'affiche pas → vérifier que le fichier fait moins de
  5 Mo et qu'il est au format jpg, jpeg, png, gif ou webp.
- Mot de passe admin oublié → redéfinir ADMIN_PASSWORD_FIDELITE dans
  les variables d'environnement puis relancer le serveur.
