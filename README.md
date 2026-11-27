# PLANEDU-IA — prototype fonctionnel (TechVault Africa)

Prototype du CDC TV-PRJ-2026-001 v0.1 : prévision des départs d'enseignants, besoin par école, redéploiements validés par un gestionnaire.
**Données 100 % synthétiques** (bannière « Données de démonstration »). Aucune décision automatique : tout passe par une validation humaine journalisée.

## Lancer
```bash
pip install -r requirements.txt
python3 -m planedu            # http://127.0.0.1:8000 (1er démarrage : base + données de démo en ~3 s)
python3 -m planedu otp admin  # code TOTP courant d'un compte 2FA
python3 -m unittest discover -s tests   # 45 tests
```
Identifiants de démo générés dans `data/DEMO_CREDENTIALS.txt` (gitignoré). Comptes : `admin`, `gest_kc`, `gest_kw`, `decideur`, `chef_e01`…`chef_e60`.
Dépendances : Python 3.13, numpy, openpyxl, reportlab. Front vanilla, sans CDN.

## Hébergement gratuit (Render) et application de bureau
Voir `docs/DEPLOIEMENT.md`.

## Documentation
`docs/CONTRACT.md` : architecture, modèle de données, rôles, API.

## Limites du prototype
Modèles entraînés sur données synthétiques (à recalibrer sur les données réelles au jalon G1/G3) · alertes SMS simulées · cibles chiffrées à fixer au G2 · audit de sécurité et analyse de conformité non réalisés.
