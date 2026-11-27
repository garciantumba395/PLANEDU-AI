# PLANEDU-IA — Contrat technique du prototype (source de vérité des 3 chantiers)

Réf. CDC TV-PRJ-2026-001 v0.1. Prototype de démonstration : DONNÉES 100 % SYNTHÉTIQUES, bannière visible « Données de démonstration ».
Stack : Python 3.13 (stdlib `http.server` + `sqlite3`) + numpy + openpyxl + reportlab. Front = SPA vanilla JS/CSS sans build ni CDN (ET-02/ET-03).
Lancement : `python3 -m planedu` -> http://127.0.0.1:8000 . Tests : `python3 -m unittest discover -s tests`.

## Arborescence & propriété des fichiers (ne JAMAIS éditer hors de son périmètre)
- Chantier MODELS : `planedu/models.py`, `tests/test_models.py`
- Chantier BACKEND : `planedu/__init__.py __main__.py server.py db.py auth.py audit.py seed.py service.py exports.py`, `tests/test_api.py`, `.gitignore`
- Chantier FRONT : `planedu/static/index.html app.js style.css`

## 1. models.py — fonctions PURES (dicts/dates/numpy, aucun accès DB). Signatures exactes
```python
from datetime import date
def retirement_date(birth: date, legal_age: int) -> date            # EF-03 : date anniversaire des `legal_age` ans (29 fév -> 28 fév)
def months_between(d0: date, d1: date) -> float
def retirement_flags(teachers: list[dict], legal_age: int, today: date, horizons=(6,12,24)) -> list[dict]
    # teachers: {id, birth_date(date), ...}; retourne [{teacher_id, retirement_date, months_to_retirement, h6, h12, h24 (bool)}]
class AttritionModel:   # EF-04 abandon/absence prolongée, numpy seul (régression logistique L2 par descente de gradient, features standardisées)
    def fit(self, rows: list[dict], horizon_months: int): ...  # rows: {age, seniority_years, level, subject_scarcity(0-1), posting_remote(0-1), left(bool)}
    def predict_proba(self, rows) -> np.ndarray
    def explain(self, row) -> list[tuple[str,float]]  # facteurs explicatifs triés par contribution (EF-14/raisons)
def build_training_rows(teachers, departures, today, horizon) -> list[dict]
def backtest(teachers, departures, assignments_info, cutoff: date, horizons=(6,12,24)) -> dict
    # entraîne sur départs < cutoff, évalue sur [cutoff, cutoff+h]; renvoie {horizon:{auc, precision_at_k, recall_at_k, baseline_auc, baseline_precision_at_k, n_pos, n}} ;
    # baseline = taux de base (prédiction constante par niveau). AUC implémenté en numpy (rang).
def compute_need(school_enrollment: dict, norms: list[dict]) -> list[dict]
    # school_enrollment {school_id, year, levels:{niveau:nombre}}; norms [{niveau, matiere, eleves_par_enseignant, en_vigueur_depuis}]
    # retourne [{school_id, niveau, matiere, requis}] avec requis = ceil(nombre / eleves_par_enseignant) ; norme la plus récente <= année
def gap(required: list[dict], staff: list[dict], leaving: list[dict]) -> list[dict]
    # écart = requis - (effectif - partants) par (school_id, niveau, matiere): {..., requis, actuel, prevu, ecart}  (ecart>0 = manque)
def recommend_redeployments(gaps, teachers, distance_fn, max_km=150, top_per_surplus=3) -> list[dict]
    # affecte des enseignants des écoles en SURPLUS (ecart<0) vers écoles en MANQUE (ecart>0) même matière+niveau ;
    # glouton sous contraintes (distance<=max_km, matière, niveau), score = gain_ecart*10 - km/20 + ancienneté_bonus(<=1); 
    # sortie [{teacher_id, from_school_id, to_school_id, niveau, matiere, gain_ecart, km, rank, reasons:[str]}] triée par rank 1..n ; un enseignant au plus 1 fois.
def haversine_km(lat1, lon1, lat2, lon2) -> float
```
Tous les résultats incluent des `reasons` lisibles en français. Tests : 100 enseignants -> 100 % dates de retraite exactes (EF-03), 10 écoles -> besoin exact (EF-05), AUC > baseline sur données synthétiques avec signal.

## 2. Base de données (sqlite `data/planedu.db`, WAL) — 12 tables CDC §5 + identité isolée
province(id,nom) ; sous_division(id,nom,province_id) ; ecole(id,nom,sous_division_id,niveau,lat,lon) ; effectif_eleves(id,ecole_id,annee,niveau,nombre) ;
norme(id,niveau,matiere,eleves_par_enseignant,en_vigueur_depuis) ; enseignant(id TEXT pseudo 'T-xxxxxxxx',date_naissance,date_entree,niveau,matiere,statut) ;
affectation(id,enseignant_id,ecole_id,debut,fin) ; depart_historique(id,enseignant_id,date,motif ['retraite','abandon','absence_prolongee','deces']) ;
prevision_depart(id,enseignant_id,horizon,probabilite,facteurs JSON,version_modele,retraite_date) ; besoin_ecole(id,ecole_id,annee,niveau,matiere,requis,prevu,ecart) ;
recommandation(id,enseignant_id,ecole_depart_id,ecole_arrivee_id,gain_ecart,rang,raisons JSON,km,statut) ; decision(id,recommandation_id UNIQUE,utilisateur,choix['accepte','modifie','refuse'],motif,date, ecole_arrivee_modifiee_id NULL) ;
audit_log(id,utilisateur,action,objet,date,prev_hash,hash) — chaîne SHA-256 append-only, triggers SQLite BEFORE UPDATE/DELETE -> RAISE(ABORT) (ES-05) ;
parametre(cle,valeur) (age_legal_retraite=65, horizons, max_km, annee_cible) ; utilisateur(id,login,hash_mdp(PBKDF2-SHA256 200k),sel,role,province_id,ecole_id,totp_secret,actif) ; session(token,user_id,expire) ;
modele_perf(id,horizon,methode,donnees,date,erreur_par_province JSON,erreur_par_niveau JSON,metriques JSON,version_modele) ;
**identite_enseignant(enseignant_id,nom_chiffre)** : table à part, nom chiffré (XOR-keystream HMAC-SHA256 ou Fernet-like stdlib) avec clé PLANEDU_KEY (env ; si absente génère `data/.key` chmod 600, jamais dans le code), lisible UNIQUEMENT par rôle `gestionnaire` habilité ou `admin_identite`… -> simplification : seuls gestionnaires de la province concernée (EF-02). Pseudonyme = 'T-' + HMAC-SHA256(clé, matricule)[:8].
Aucun champ biométrique n'est importé (ES-04) : l'import REJETTE toute colonne hors liste blanche.

## 3. Rôles (ES-01) et périmètre (EF-12)
`admin` (comptes, audit, sauvegarde, normes — pas de noms) · `gestionnaire` (province_id ; voit noms, décide) · `chef` (ecole_id ; vue de son école, lecture) · `decideur` (lecture agrégée, jamais de noms ni d'enseignant individuel).
Toute requête hors périmètre -> HTTP 403 + audit. Auth : login + mot de passe + TOTP RFC6238 (stdlib hmac, 30 s, tolérance ±1) obligatoire pour admin & gestionnaire (ES-02). Session = token opaque en header `Authorization: Bearer <token>`, expiration 8 h.
Comptes de démo créés au 1er démarrage avec mots de passe aléatoires écrits dans `data/DEMO_CREDENTIALS.txt` (gitignoré, chmod 600) avec secrets TOTP ; `python3 -m planedu otp <login>` affiche le code courant. Aucun secret dans le code (ES-06).
Comptes : admin, gest_kc (Kongo-Central), gest_kw (Kwilu), chef_<ecole>, decideur.

## 4. API REST JSON (préfixe /api, erreurs {"error": "msg"} + code HTTP)
Tous les endpoints sauf login exigent Bearer. Réponses 200 JSON sauf mention.
- POST /api/login {login,password,otp?} -> {token,user:{id,login,role,province_id,ecole_id,province_nom},otp_required:bool}. Si OTP requis et absent -> 401 {"error":"otp_required","otp_required":true}.
- POST /api/logout ; GET /api/me
- GET /api/geo -> {provinces:[{id,nom}],sous_divisions:[{id,nom,province_id}]} (filtré par périmètre) ; niveaux = ["primaire","secondaire"]
- GET /api/dashboard?province_id=&sous_division_id=&niveau=&horizon=12 -> {kpis:{enseignants,departs_prevus,ecart_total,ecoles_en_manque,qualite_donnees_pct,reco_traitees_pct}, par_sous_division:[{id,nom,province_id,lat,lon,departs,ecart,ecoles,manque}], par_niveau:[{niveau,departs,ecart}], serie_departs:[{horizon,retraite,abandon_absence}], banniere:"Données de démonstration", calcul_ms}  (EF-09, <5 s)
- GET /api/previsions?horizon=12&niveau=&sous_division_id=&limit=100&offset=0 -> {total, items:[{enseignant_id,nom(null sauf gestionnaire),niveau,matiere,ecole_nom,retraite_date,p6,p12,p24,probabilite_totale,facteurs:[{facteur,poids}],raisons:[str]}]}  (EF-03/04, tri proba décroissante)
- GET /api/besoins?sous_division_id=&niveau=&ecart=manque|surplus|tous -> {items:[{ecole_id,ecole_nom,sous_division,niveau,matiere,requis,actuel,prevu,ecart}]} (EF-05/06)
- POST /api/calcul {} (gestionnaire/admin) : recalcule prévisions + besoins + recommandations pour le périmètre ; renvoie {previsions,besoins,recommandations,duree_ms} ; audit.
- GET /api/recommandations?statut=en_attente|accepte|modifie|refuse|tous&limit&offset -> {total,items:[{id,rang,enseignant_id,nom(null sauf gest.),matiere,niveau,ecole_depart,ecole_arrivee,gain_ecart,km,raisons,contraintes_respectees:[str],statut,decision:{choix,motif,utilisateur,date}|null}]} (EF-07)
- POST /api/recommandations/{id}/decision {choix:'accepte'|'modifie'|'refuse', motif (obligatoire non vide), ecole_arrivee_id? (obligatoire si 'modifie')} (gestionnaire de la province) -> decision ; 409 si déjà décidée ; audit (EF-08). RIEN ne modifie paie/carrière.
- POST /api/import (multipart ou JSON {kind:'enseignants'|'ecoles'|'effectifs', filename, content_base64}) CSV ou XLSX -> {importes, rejetes, rapport:[{ligne,champ,erreur}]} ; pseudonymise matricule+nom à l'import (EF-01/02, ES-04). GET /api/import/template/{kind} -> CSV modèle.
  Colonnes enseignants : matricule,nom,date_naissance(YYYY-MM-DD),date_entree,niveau,matiere,ecole_id,statut ; écoles : nom,sous_division_id,niveau,lat,lon ; effectifs : ecole_id,annee,niveau,nombre.
- GET/PUT /api/normes (admin ; GET aussi gestionnaire/decideur) normes [{id,niveau,matiere,eleves_par_enseignant,en_vigueur_depuis}] ; GET/PUT /api/parametres {age_legal_retraite,max_km,annee_cible} (EF-13) — modif => prochain /api/calcul (ou calcul auto) change le résultat sans redéploiement.
- GET /api/modeles/performance?horizon= -> fiches EF-14 {horizon,methode,donnees,date,metriques,erreur_par_province,erreur_par_niveau,baseline,version_modele,avertissement}
- GET /api/export/{table}.{xlsx|csv|pdf} avec mêmes filtres que l'écran ; table ∈ previsions|besoins|recommandations|dashboard (EF-11, ET-03) ; PDF reportlab avec en-tête+bannière+date ; XLSX openpyxl ; sans noms pour non-gestionnaires.
- Admin : GET /api/admin/users ; POST /api/admin/users {login,role,province_id?,ecole_id?} (renvoie mdp temporaire + secret TOTP UNE fois) ; PATCH /api/admin/users/{id} {actif}; GET /api/admin/audit?limit&offset (+ verif chaîne `chain_ok`) ; POST /api/admin/backup -> {fichier,sha256,taille} (copie `sqlite3.backup` chiffrée dans data/backups/) ; POST /api/admin/restore {fichier} -> restauration testée (ET-06) ; GET /api/admin/rbac-test -> exécute le test négatif d'accès inter-province et renvoie le résultat.
- Alertes EF-10 (S) : POST /api/alertes/preview (gestionnaire) -> par école, note courte (≤ 320 car., style SMS) ; POST /api/alertes/envoyer -> simulation journalisée (aucun envoi réel ; canal « à confirmer »).
- GET /api/health -> {ok,version}

## 5. Données de démonstration (seed.py, déterministe seed=42)
2 provinces (Kongo-Central, Kwilu) · 6 sous-divisions · ~60 écoles (primaire/secondaire, coords GPS plausibles) · ~900 enseignants avec noms fictifs congolais · effectifs élèves 3 années · normes (primaire 45 él./ens., secondaire 35 él./ens./matière) · 4 ans de depart_historique avec signal réaliste (âge/ancienneté/éloignement/matière rare) · affectations. Écarts volontaires (écoles en manque ET en surplus) pour que le redéploiement soit parlant. Modèles entraînés au seed, fiches EF-14 générées par rétro-test.

## 6. Front (planedu/static) — SPA hash-router, FR, responsive téléphone, poids total < 150 Ko, aucune dépendance externe
Écrans : Login (+OTP) · Tableau de bord (KPIs, carte SVG schématique des sous-divisions par coordonnées, filtres province/sous-division/niveau/horizon, barres SVG) · Prévisions (table + raisons + facteurs) · Besoins/écarts · Redéploiements (carte de proposition, boutons Accepter/Modifier/Refuser + motif obligatoire, historique) · Données (import CSV/XLSX avec rapport d'erreurs, modèles CSV) · Modèles (fiches EF-14) · Administration (users, audit + indicateur chaîne, backup/restore, test RBAC, normes/paramètres) · Alertes. Menu filtré par rôle. Bannière démo permanente. Exports via boutons (fetch blob + Authorization). Mode « léger » (ET-02) : pas d'animations, pagination, cache localStorage des derniers exports pour hors-ligne. Accessibilité (labels, contraste AA, focus).
