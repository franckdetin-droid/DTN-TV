#!/bin/bash
echo "=== DÉPLOIEMENT VISION+ TV (RÉGIE COMPLÈTE & STOCKAGE 2 To) ==="
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
echo "✓ Dépendances installées avec succès."
echo "Lancement du serveur sur http://localhost:3000"
python3 app.py
