# ProxiService — MVP Flask

Plateforme de mise en relation locale avec :
- recherche publique sans compte client ;
- inscription/connexion des professionnels ;
- publication de services et upload d'images ;
- contact WhatsApp si un numéro est renseigné, sinon formulaire de messagerie interne ;
- exactement deux emplacements de bannières publicitaires ;
- tableau de bord administrateur et statistiques de base;
- page publique de téléchargement de l’application, avec lien géré depuis l’administration;
- navigation publique sans bouton Administration et consultation du site sans compte visiteur.

## Installation locale

1. Installez Python 3.10 ou plus récent.
2. Créez un environnement virtuel :
   ```bash
   python -m venv .venv
   ```
3. Activez-le puis installez les dépendances :
   ```bash
   pip install -r requirements.txt
   ```
4. Copiez `.env.example` vers `.env`, puis configurez `SECRET_KEY`, `ADMIN_EMAIL` et `ADMIN_PASSWORD`. Pour un essai local, le code initial est `3004`; changez-le avant tout déploiement public.
5. Lancez :
   ```bash
   python app.py
   ```
6. Ouvrez `http://127.0.0.1:5000`.

L'administrateur est créé au premier démarrage si `ADMIN_EMAIL` et `ADMIN_PASSWORD` sont définis. Le mot de passe est stocké sous forme de hash, pas en clair.

## Déploiement

La base SQLite et les fichiers `static/uploads/` doivent être placés sur un disque persistant. Si l'hébergeur efface le système de fichiers lors d'un redéploiement, les images et la base peuvent être perdues. Pour un usage public durable, utilisez un stockage persistant ou adaptez le projet à une base de données et un stockage d'objets gérés.

Variables d'environnement à configurer :
- `SECRET_KEY` : longue valeur aléatoire secrète ;
- `ADMIN_EMAIL` : e-mail admin ;
- `ADMIN_PASSWORD` : mot de passe admin robuste ;
- `DATABASE_PATH` : facultatif, chemin vers la base SQLite persistante ;
- `PORT` : fourni par certains hébergeurs.

Page de téléchargement : `/application`. Pour définir son lien, connectez-vous à `/login` avec `ADMIN_EMAIL` et `ADMIN_PASSWORD`, ouvrez `/admin`, puis utilisez le panneau « Page de téléchargement ». Le bouton Administration n’est pas affiché sur la page publique.

**Sécurité :** `3004` est uniquement un code de démonstration et ne doit pas rester actif sur un site public.

## Images

Les images des services et des bannières sont téléversées dans `static/uploads/`. Formats autorisés : PNG, JPG/JPEG, WEBP et GIF ; taille maximale : 5 Mo par fichier. Une politique de sauvegarde et de suppression des images doit être définie avant un lancement public.

## Limites importantes avant lancement public

Cette version est un MVP de départ, pas encore une plateforme prête pour une forte audience :
- le formulaire de messagerie enregistre les messages pour le prestataire, mais ne fournit pas encore une conversation temps réel ni une réponse dans le fil ;
- les statistiques sont basiques ; les vues sont comptées sans déduplication avancée ;
- ajouter une protection anti-CSRF, une limitation de débit, une validation approfondie des URL publicitaires, une politique de confidentialité, des conditions d'utilisation et des sauvegardes avant production ;
- le système de géolocalisation réelle et la carte ne sont pas encore intégrés ;
- les annonces doivent être modérées et les données utilisateurs protégées ;
- la publicité ne génère pas automatiquement des revenus : il faut trouver des annonceurs et convenir de conditions de paiement.

## Structure

```text
proxiservice/
├── app.py
├── requirements.txt
├── Procfile
├── .env.example
├── README.md
├── templates/
└── static/
    ├── css/style.css
    ├── js/main.js
    └── uploads/
```


### Accès administrateur
Ouvrez `/admin/login` et saisissez uniquement le code défini par `ADMIN_PASSWORD` dans `.env` (valeur de démonstration : `3004`). Les professionnels conservent leur connexion séparée sur `/login`.
