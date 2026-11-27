# Déploiement du prototype PLANEDU-IA (démo, données synthétiques)

## A. Hébergement gratuit — Render
1. Créer un compte sur https://render.com (gratuit) et le relier à GitHub (accès au dépôt `PLANEDU-AI`).
2. **New → Blueprint** → choisir le dépôt et la branche → Render lit `render.yaml` et construit le `Dockerfile`.
3. Quand le service est « Live », l'URL `https://planedu-ia-demo.onrender.com` (ou voisine) est l'application.
4. Mot de passe commun des comptes de démo : Render → service → **Environment** → `PLANEDU_DEMO_PASSWORD`.
   Comptes : `admin`, `gest_kc`, `gest_kw`, `decideur`, `chef_e01`…`chef_e60`. La 2FA est désactivée en démo hébergée.
Limites du plan gratuit : mise en veille après ~15 min d'inactivité (réveil ~30 s), disque éphémère (données régénérées à chaque redémarrage). Ne jamais y importer de données réelles.

## B. Application de bureau (Windows / macOS / Linux)
Fenêtre native (pywebview) + serveur local 127.0.0.1, 100 % hors ligne, données dans `%APPDATA%\PLANEDU-IA`.
- Build automatique : GitHub → **Actions → Application bureau → Run workflow** ; récupérer l'artefact `PLANEDU-IA-windows-latest` (dossier `PLANEDU-IA` avec `PLANEDU-IA.exe`).
- Windows : nécessite WebView2 (préinstallé sur Windows 10/11 récents). Non signé : SmartScreen peut avertir (« Informations complémentaires » → « Exécuter quand même »).
- Connexion : comptes ci-dessus, mot de passe de démo affiché sur l'écran de connexion, 2FA désactivée.
- Lancement sans installeur : `pip install -r requirements.txt pywebview && python -m planedu.desktop`.
