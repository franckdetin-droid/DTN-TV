# VISION+ TV — Régie TV + stockage personnel

Cette version conserve l'interface et les couleurs existantes, mais arrondit les contrôles et ajoute un vrai conducteur de programmes ainsi qu'un stockage de fichiers configurable.

## Ajouts

- Fichiers locaux enregistrés avec un nom interne unique.
- Stockage configurable : disque local Docker pour les tests ou stockage objet S3-compatible pour la persistance en production.
- Métadonnées des fichiers dans SQLAlchemy/PostgreSQL en production.
- Dossiers, recherche, renommage, suppression et téléchargement.
- Liens privés temporaires avec révocation côté API.
- Upload multiple depuis Android.
- Correction de l'upload `/upload` : erreurs JSON explicites et champ `file` vérifié.
- Programmation par jours de la semaine.
- Durée détectée automatiquement pour un fichier vidéo local lorsque le navigateur peut lire ses métadonnées.
- Heure de fin calculée automatiquement à partir de l'heure de début + durée.
- Option pour commencer après la fin du programme précédent.
- Passage automatique au programme suivant quand une vidéo arrive à sa fin.
- Détection des liens YouTube et lecture via le lecteur YouTube officiel.
- Lecture HLS `.m3u8` lorsque le navigateur et HLS.js le permettent.
- Les liens externes qui ne sont pas des médias pris en charge peuvent être ouverts directement.
- Les chiffres de stockage sont calculés depuis les fichiers réels ou les métadonnées, pas inventés.

## Important pour les 2 To

Le nombre « 2048 Go » est une capacité configurée, pas 2 To physiques offerts par Railway.

Pour conserver les fichiers après un redéploiement Railway, configure `STORAGE_BACKEND=s3` avec un stockage objet réellement persistant et suffisamment dimensionné. Le disque local est adapté au développement et à Docker avec volume persistant, mais il ne doit pas être considéré comme un disque 2 To Railway.

## Installation locale

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/Android/Termux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Ouvrir `/` pour la TV et `/admin` pour la régie.

## PostgreSQL

Définir `DATABASE_URL` avec une URL PostgreSQL valide. L'application crée ses tables SQLAlchemy au démarrage. Les chaînes TV existantes restent dans `channels_db.json` afin de préserver la compatibilité avec le projet initial.

## Stockage objet S3-compatible

Définir :

```env
STORAGE_BACKEND=s3
STORAGE_ENDPOINT=https://...
STORAGE_BUCKET=...
STORAGE_ACCESS_KEY=...
STORAGE_SECRET_KEY=...
STORAGE_REGION=...
STORAGE_CAPACITY_GB=2048
```

Les clés restent côté serveur et ne sont jamais envoyées au navigateur.

## Programmation

Dans « Programmer une Vidéo / Lien » :

1. Choisir la chaîne.
2. Saisir le titre.
3. Choisir les jours.
4. Saisir l'heure de début.
5. Sélectionner le fichier vidéo. Sa durée est détectée par le navigateur lorsque possible.
6. « Calculer automatiquement l'heure de fin » utilise cette durée.
7. « Commencer après le programme précédent » reprend l'heure de fin du dernier programme.
8. « Passer automatiquement au programme suivant » permet l'enchaînement à la fin de la vidéo.

Pour YouTube, la durée ne peut pas être déduite de manière fiable uniquement à partir d'une URL de page YouTube sans utiliser l'API YouTube Data avec une clé. Dans ce cas, renseigner la durée si l'heure de fin automatique est nécessaire.

## Sécurité

- Les mutations de l'API admin nécessitent une session administrateur.
- Les requêtes mutantes utilisent un jeton CSRF.
- Les cookies de session sont HttpOnly et SameSite=Lax.
- Activez `SESSION_COOKIE_SECURE=1` sous HTTPS.
- Les mots de passe ne sont pas utilisés pour la régie actuelle : l'accès reste basé sur `ADMIN_PIN`, conservé côté serveur dans une variable d'environnement.
- Les chemins de stockage ne sont pas acceptés directement depuis le navigateur.
- Les noms internes sont générés avec UUID.
- Les liens de partage utilisent des tokens aléatoires dont seul le hash est stocké.

## Déploiement

Pour Railway ou un autre hébergeur, utilisez Gunicorn :

```bash
gunicorn --bind 0.0.0.0:$PORT app:app
```

Configurez obligatoirement un stockage objet persistant pour une vraie conservation des gros fichiers. Une base PostgreSQL est également recommandée en production.

## Test de bout en bout

Vérifier après déploiement :

- `/admin` demande le PIN.
- Un programme peut être ajouté avec un fichier.
- L'upload affiche une erreur explicite au lieu d'un 400 générique.
- La vidéo apparaît après actualisation.
- La durée et l'heure de fin sont calculées pour les fichiers locaux compatibles.
- Le jour de diffusion est visible dans la grille.
- Une vidéo terminée passe au programme suivant.
- Un lien YouTube est ouvert dans le lecteur YouTube.
- Un fichier peut être renommé, téléchargé, supprimé et partagé.
- Après redémarrage, les métadonnées PostgreSQL et les objets du stockage persistant restent disponibles.

## Limite à ne pas confondre

L'application peut gérer une capacité configurée à 2048 Go et être conçue pour davantage, mais elle ne crée pas physiquement ces 2 To. La capacité réelle dépend du fournisseur de stockage configuré.


## Programmation automatique — version corrigée

La régie calcule maintenant le programme à l'antenne selon le jour et l'heure du serveur. Pour une vidéo enregistrée, l'heure de fin peut être calculée automatiquement à partir de la durée détectée lors de la sélection du fichier. Pour un flux en direct, l'option « Flux en direct sans heure de fin » laisse `endTime` vide : le flux reste à l'antenne jusqu'au prochain créneau planifié qui commence.

La page publique interroge régulièrement le serveur pour changer automatiquement de programme lorsque le créneau actif change. La fin naturelle d'un fichier vidéo déclenche également le passage au programme suivant lorsque « Passer automatiquement au programme suivant » est activé.

Le stockage local utilise `STORAGE_FOLDER` et fonctionne avec un volume Docker. Sur un hébergeur dont le disque local est éphémère, il faut obligatoirement monter un volume persistant ou configurer `STORAGE_BACKEND=s3` avec un stockage objet persistant. Le chiffre « 2 To » est une capacité configurée, pas une capacité physique créée par l'application.

## Accès multi-chaînes et robot

- `/robot` est une interface publique de création guidée.
- Une personne peut écrire `créer une chaîne`, puis choisir le nom et son mot de passe, ou utiliser une commande en une ligne : `créer une chaîne Nom: Ma TV | Mot de passe: MonPass123`.
- Le robot crée la chaîne, son compte administrateur séparé et renvoie l'URL d'administration ainsi que le lien M3U propre à la chaîne.
- Chaque compte de chaîne est isolé des autres chaînes côté API. Les fichiers envoyés depuis cet espace sont associés à la chaîne.
- Le lien `/live/<channel_id>/playlist.m3u` est prévu pour être ouvert dans VLC et les lecteurs IPTV compatibles. Il contient le programme courant puis les programmes à venir du jour lorsqu'ils sont disponibles.
- Pour un vrai flux HLS continu produit à partir de vidéos MP4, il faut un transcodage/serveur média. Le projet ne prétend pas transformer magiquement un MP4 en HLS sans moteur de transcodage.

## Déploiement Railway à 0 €

Le code fonctionne sur Railway, mais le stockage local du service est éphémère. Pour conserver les vidéos après un redéploiement, il faut un stockage persistant. La documentation Railway indique actuellement 1 Go d'espace éphémère sur le plan Free et 0,5 Go de volume sur Free/Trial ; les volumes sont facturés selon leur utilisation. Pour rester à 0 €, ce projet peut être configuré avec un stockage externe disposant d'une offre gratuite, en renseignant les variables S3 compatibles, mais les limites du fournisseur s'appliquent.
