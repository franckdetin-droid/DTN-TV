# VISION+ TV — Plateforme de Diffusion 24/7 & Régie TV Complète

Bienvenue sur le pack de déploiement autonome **VISION+ TV**.

## Fonctionnalités de la Régie & de l'Application :
1. **Console Régie Complète (Code PIN 3004)** :
   - Création et gestion illimitée de chaînes TV (Nom, numéro, logo, genre, ticker défilant).
   - Programmation d'émissions et téléversement de fichiers vidéos réels (MP4, MKV, WebM) dans le stockage dédié 2 To.
   - Intégration de flux directs (liens MP4 direct, HLS .m3u8, YouTube, Facebook).
   - Grille des programmes 24/7 avec bouton "Passer à l'antenne" immédiat.
   - Studio Caméra en direct avec micro et filtres broadcast en temps réel.
   - Générateur d'URLs de flux (HLS .m3u8, MP4 Direct 4K, Playlist IPTV M3U, RTMP).
2. **Lecteur Style YouTube Moderne** :
   - Thème blanc épuré avec bandeau de chaîne et logo en haut à droite.
   - Barre de zapping instantané en haut du lecteur.
   - Contrôles complets : Lecture/Pause, Volume, Vitesse, Sous-titres (CC), Téléchargement Direct, Mode Théâtre et Plein Écran.
3. **Serveur Haute Capacité Python Flask** :
   - Support streaming partiel HTTP 206 pour des vidéos fluides sans coupure.
   - Base de données JSON persistante (`channels_db.json`).

## Démarrage Rapide :
```bash
pip install -r requirements.txt
python app.py
```
Puis ouvrez votre navigateur sur : **http://localhost:3000**
Accédez à la régie sur : **http://localhost:3000/admin** (PIN : **3004**)
