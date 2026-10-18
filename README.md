# ProxiService — version corrigée

Application Flask de mise en relation locale. Cette version conserve les routes et tables existantes et ajoute les conversations privées, les profils professionnels, les avis, une protection CSRF sur les formulaires POST et des contrôles de sécurité complémentaires.

## Fonctionnalités

- Recherche publique de services, catégories, ville et quartiers.
- Comptes professionnels existants et gestion des services.
- Profils publics `/professionnel/<id>` avec prestations, photo de profil, coordonnées, notes et avis.
- Comptes clients sur `/client/register` pour conserver leurs conversations.
- Messagerie bidirectionnelle persistante : `/inbox`, puis ouverture d'une conversation. Les nouveaux messages sont récupérés périodiquement côté navigateur.
- Autorisation vérifiée côté serveur pour chaque conversation et chaque message.
- Une note par client et par prestation, de 1 à 5 étoiles. Le modèle existant ne contient pas de réservation/prestation confirmée : l'avis n'est donc pas encore conditionné à une confirmation de réalisation.
- Administration par code sur `/admin/login`, sans bouton public. Limitation de 5 tentatives par session sur 15 minutes.
- Deux emplacements publicitaires, paramètres du lien de téléchargement et statistiques existants.
- Protection CSRF des formulaires POST et validation des signatures de fichiers image.

## Installation locale

Python 3.10 ou plus récent est recommandé.

```bash
python -m venv .venv
# Windows : .venv\\Scripts\\activate
# Linux/macOS : source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Ouvrir `http://127.0.0.1:5000`. Le premier démarrage crée les tables manquantes. Les tables et données déjà présentes ne sont pas supprimées. Le code administrateur par défaut est `3004` si `ADMIN_PASSWORD` n'est pas défini.

## Déploiement Render

- **Build Command** : `pip install -r requirements.txt`
- **Start Command** : `gunicorn app:app`
- **Variables d'environnement** :
  - `SECRET_KEY` : valeur aléatoire longue et privée, obligatoire en production.
  - `ADMIN_PASSWORD` : remplacer `3004` par un code fort avant l'ouverture publique.
  - `ADMIN_EMAIL` : adresse du compte administrateur interne créé par le système (valeur par défaut `admin@proxiservice.com`). L'écran de déverrouillage ne demande que le code.
  - `DATABASE_PATH` : chemin absolu vers le fichier SQLite, par exemple `/var/data/proxiservice.db` si un disque Render est monté sur `/var/data`.
  - `COOKIE_SECURE=true` en HTTPS.
  - `PORT` est fourni automatiquement par Render ; localement, le défaut est `5000`.

### Persistance Render

Le système de fichiers d'un service Render sans disque persistant peut être éphémère. Pour conserver les comptes, conversations et avis SQLite, attacher un disque persistant et configurer `DATABASE_PATH` dans ce disque. Les photos sont enregistrées dans `static/uploads` ; ce dossier doit lui aussi être placé sur un stockage persistant pour conserver les fichiers après redéploiement. Pour une architecture à plusieurs instances, utiliser plutôt PostgreSQL et un stockage d'images durable : SQLite et le dossier local ne sont pas adaptés au partage entre plusieurs instances.

### Préserver les données existantes

1. Faire une sauvegarde du fichier `.db` existant avant déploiement.
2. Configurer `DATABASE_PATH` pour pointer vers ce fichier existant (ou restaurer la sauvegarde sur le disque persistant).
3. Déployer l'application. `CREATE TABLE IF NOT EXISTS` et l'ajout conditionnel de `users.profile_photo` complètent le schéma sans supprimer les données existantes.
4. Vérifier les comptes, les prestations et les images après déploiement.

Ne changez pas `DATABASE_PATH` vers un fichier vide si vous voulez retrouver les comptes existants. Les anciens messages anonymes sont conservés dans la table `messages`; la nouvelle messagerie privée utilise `conversations` et `conversation_messages`.

## Notes de sécurité et limites connues

- Les conversations exigent un compte client et une connexion. Les clients anonymes ne peuvent pas consulter un historique privé.
- La protection CSRF s'applique aux formulaires POST. Les sessions nécessitent un `SECRET_KEY` stable entre redémarrages.
- Le code `3004` est un code par défaut demandé pour le développement : il est trop faible pour rester en production.
- La limitation admin est stockée dans la session du navigateur et ne remplace pas un rate-limit partagé par IP derrière un proxy.
- Les avis sont uniques par client et prestation, mais le projet ne possède pas encore de flux de réservation/confirmation qui prouve qu'une prestation a été réalisée.
- Les images sont validées par extension et signature de fichier, limitées à 5 Mo par requête.
- Les dépendances et la syntaxe Python peuvent être vérifiées localement avec `python -m py_compile app.py`. Exécutez les tests fonctionnels dans un environnement où les dépendances du `requirements.txt` sont installées.
