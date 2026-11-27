"""Données de démonstration 100 % synthétiques, déterministes (seed=42)."""
import math
import os
import random
import stat
import time
from datetime import date, timedelta

from . import audit, auth, db

SEED = 42
REF_DATE = date(2026, 9, 1)
ANNEE_CIBLE = 2026
LEGAL_AGE = 65
HIST_YEARS = 4

PROVINCES = [(1, "Kongo-Central"), (2, "Kwilu")]
SOUS_DIVISIONS = [
    (1, "Matadi", 1, -5.82, 13.46), (2, "Boma", 1, -5.85, 13.06), (3, "Mbanza-Ngungu", 1, -5.25, 14.86),
    (4, "Bandundu-ville", 2, -3.32, 17.38), (5, "Bulungu", 2, -4.55, 18.60), (6, "Kikwit", 2, -5.04, 18.82),
]
MATIERES_SEC = ["Mathématiques", "Français", "Sciences", "Anglais"]
SCARCITE = {"Polyvalent": 0.1, "Français": 0.2, "Anglais": 0.5, "Sciences": 0.7, "Mathématiques": 0.85}
NORMES = [("primaire", "Polyvalent", 45)] + [("secondaire", m, 35) for m in MATIERES_SEC]

PRENOMS = ["Jean", "Pierre", "Marie", "Joséphine", "Patrick", "Christelle", "Blaise", "Espérance", "Dieudonné", "Prosper",
           "Nadine", "Gloire", "Fiston", "Chancelle", "Trésor", "Médard", "Rachel", "Albertine", "Célestin", "Bienvenu",
           "Henriette", "Léonie", "Gédéon", "Mireille", "Pascal", "Sylvie", "Guy", "Odette", "Éric", "Francine",
           "Alphonse", "Berthe", "Cyrille", "Désiré", "Eugénie", "Faustin", "Godelive", "Héritier", "Isidore", "Jocelyne"]
NOMS = ["Mukendi", "Kabongo", "Mbuyi", "Ngoma", "Kasongo", "Nsimba", "Lukusa", "Mavungu", "Kiala", "Mputu", "Tshimanga",
        "Bakala", "Makiese", "Matondo", "Nkosi", "Mvemba", "Kimbuta", "Landu", "Mabiala", "Zola", "Kitoko", "Nzuzi",
        "Lumbu", "Pembe", "Mayala", "Muteba", "Mbala", "Kwese", "Mwanza", "Ngandu", "Kapinga", "Kiese", "Lusamba", "Nlandu"]
NOMS_ECOLES = ["Saint-Joseph", "Sainte-Anne", "Les Lumières", "Mont-Amba", "Notre-Dame", "La Providence", "Le Savoir",
               "Kimbanguiste", "Bon Berger", "Espoir", "Saint-Pierre", "Sainte-Marie", "L'Avenir", "Réveil", "Horizon",
               "Saint-Paul", "Bonne Nouvelle", "Cité Verte", "Nsona", "Lukunga", "Kwango", "Inkisi", "Fleuve", "Savane",
               "Mbanza", "Tuendi", "Moyo", "Elikya", "Lisanga", "Bomoko", "Mapendo", "Umoja", "Amani", "Tumaini", "Kitoko",
               "Mwinda", "Nzambe", "Boyoka", "Mokili", "Bolingo", "Salongo", "Kimia", "Lola", "Mosolo", "Ekolo", "Likambo",
               "Nzoto", "Sika", "Bisso", "Nkento", "Mobali", "Mwana", "Libala", "Esengo", "Bokeli", "Likolo", "Mayele",
               "Makasi", "Kobeta", "Nzela"]


def _sigmoid(x):
    return 1 / (1 + math.exp(-x))


def _add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def _haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def run(conn, verbose=False):
    t0 = time.time()
    rng = random.Random(SEED)
    db.init_schema(conn)
    names_pool = NOMS_ECOLES[:]
    rng.shuffle(names_pool)

    with db.tx(conn):
        conn.executemany("INSERT INTO province VALUES(?,?)", PROVINCES)
        conn.executemany("INSERT INTO sous_division VALUES(?,?,?)", [(i, n, p) for i, n, p, _, _ in SOUS_DIVISIONS])
        conn.executemany("INSERT INTO norme(niveau,matiere,eleves_par_enseignant,en_vigueur_depuis) VALUES(?,?,?,2020)", NORMES)
        for k, v in {"age_legal_retraite": 65, "horizons": "6,12,24", "max_km": 150, "annee_cible": ANNEE_CIBLE,
                     "date_reference": REF_DATE.isoformat(), "horizon_besoin": 12}.items():
            conn.execute("INSERT INTO parametre VALUES(?,?)", (k, str(v)))

        # ---- écoles + effectifs
        ecoles = []
        eid = 0
        for sid, sname, pid, clat, clon in SOUS_DIVISIONS:
            for k in range(10):
                eid += 1
                niveau = "primaire" if k < 6 else "secondaire"
                lat = clat + rng.uniform(-0.28, 0.28)
                lon = clon + rng.uniform(-0.28, 0.28)
                pre = "EP" if niveau == "primaire" else rng.choice(["Institut", "Lycée", "CS"])
                nom = f"{pre} {names_pool[(eid - 1) % len(names_pool)]}"
                if any(e["nom"] == nom for e in ecoles):
                    nom += f" {sname}"
                n26 = rng.randint(450, 790) if niveau == "primaire" else rng.randint(100, 172)
                ecoles.append(dict(id=eid, nom=nom, sd=sid, pid=pid, niveau=niveau, lat=lat, lon=lon, n=n26,
                                   dist=_haversine(lat, lon, clat, clon)))
                conn.execute("INSERT INTO ecole VALUES(?,?,?,?,?,?)", (eid, nom, sid, niveau, round(lat, 5), round(lon, 5)))
                for yr, f in ((2024, 0.93), (2025, 0.965), (2026, 1.0)):
                    conn.execute("INSERT INTO effectif_eleves(ecole_id,annee,niveau,nombre) VALUES(?,?,?,?)",
                                 (eid, yr, niveau, int(n26 * f * rng.uniform(0.985, 1.015))))

        # ---- enseignants (+ départs historiques avec signal)
        start = _add_years(REF_DATE, -HIST_YEARS)
        names_seen = set()
        counters = {1: 0, 2: 0}
        prefix = {1: "KC", 2: "KW"}
        teachers, departs, affs, idents = [], [], [], []
        delta_choices = [-2] * 10 + [-1] * 20 + [0] * 25 + [1] * 25 + [2] * 15 + [3] * 5

        def mk_name():
            for _ in range(50):
                nm = f"{rng.choice(PRENOMS)} {rng.choice(NOMS).upper()}"
                if nm not in names_seen:
                    names_seen.add(nm)
                    return nm
            return nm

        for e in ecoles:
            matieres = ["Polyvalent"] if e["niveau"] == "primaire" else MATIERES_SEC
            for m in matieres:
                ratio = 45 if e["niveau"] == "primaire" else 35
                requis = math.ceil(e["n"] / ratio)
                target = max(1, requis + rng.choice(delta_choices))
                active = 0
                while active < target:
                    counters[e["pid"]] += 1
                    matricule = f"{prefix[e['pid']]}-{counters[e['pid']]:06d}"
                    u = rng.random()
                    if u < 0.15:
                        age_ref = rng.uniform(57.5, 68.5)
                    else:
                        age_ref = rng.triangular(23, 62, 41)
                    birth = REF_DATE - timedelta(days=int(age_ref * 365.25))
                    entry_age = rng.uniform(22, 31)
                    entry = birth + timedelta(days=int(entry_age * 365.25))
                    if entry >= REF_DATE - timedelta(days=200):
                        entry = REF_DATE - timedelta(days=rng.randint(300, 2500))
                    seniority = (REF_DATE - entry).days / 365.25
                    remote = min(1.0, e["dist"] / 38.0)
                    scarcity = SCARCITE[m]
                    p_att = _sigmoid(-2.55 + 1.7 * remote + 1.1 * scarcity + (0.9 if seniority < 4 else 0)
                                     - 0.025 * (seniority - 8) + (0.5 if e["niveau"] == "secondaire" else 0))
                    ret_date = _add_years(birth, LEGAL_AGE)
                    tid = auth.pseudonym(matricule)
                    nom = mk_name()
                    leave = None
                    if start <= ret_date < REF_DATE and entry < ret_date:
                        leave = (ret_date, "retraite")
                    elif ret_date < start:
                        continue
                    elif rng.random() < p_att:
                        d = start + timedelta(days=rng.randint(0, (REF_DATE - start).days - 1))
                        if d > entry + timedelta(days=180) and (ret_date >= REF_DATE or d < ret_date):
                            motif = rng.choices(["abandon", "absence_prolongee", "deces"], [0.72, 0.24, 0.04])[0]
                            leave = (d, motif)
                        else:
                            continue
                    if leave is None and ret_date < REF_DATE:
                        continue
                    teachers.append((tid, birth.isoformat(), entry.isoformat(), e["niveau"], m,
                                     "actif" if leave is None else "parti"))
                    idents.append((tid, auth.encrypt_text(nom)))
                    deb = max(entry, start - timedelta(days=rng.randint(0, 900)))
                    if leave is None:
                        affs.append((tid, e["id"], deb.isoformat(), None))
                        active += 1
                    else:
                        affs.append((tid, e["id"], deb.isoformat(), leave[0].isoformat()))
                        departs.append((tid, leave[0].isoformat(), leave[1]))
        conn.executemany("INSERT INTO enseignant VALUES(?,?,?,?,?,?)", teachers)
        conn.executemany("INSERT INTO identite_enseignant VALUES(?,?)", idents)
        conn.executemany("INSERT INTO affectation(enseignant_id,ecole_id,debut,fin) VALUES(?,?,?,?)", affs)
        conn.executemany("INSERT INTO depart_historique(enseignant_id,date,motif) VALUES(?,?,?)", departs)

    creds = _create_users(conn, ecoles)
    audit.log(conn, "système", "seed", f"{len(teachers)} enseignants, {len(ecoles)} écoles")
    from . import service
    service.calculer(conn, None, "système")
    service.generer_fiches(conn)
    audit.log(conn, "système", "calcul_initial", "prévisions, besoins, recommandations, fiches EF-14")
    if verbose:
        print(f"[seed] terminé en {time.time() - t0:.1f}s ; identifiants : {creds}")
    return creds


def _create_users(conn, ecoles):
    lines = ["# PLANEDU-IA : comptes de DÉMONSTRATION (données synthétiques). Fichier confidentiel, ne pas versionner.",
             "# login ; mot de passe ; secret TOTP (base32, si 2FA) ; rôle ; périmètre"]
    specs = [("admin", "admin", None, None, "global"), ("gest_kc", "gestionnaire", 1, None, "Kongo-Central"),
             ("gest_kw", "gestionnaire", 2, None, "Kwilu"), ("decideur", "decideur", None, None, "agrégé")]
    for e in ecoles:
        specs.append((f"chef_e{e['id']:02d}", "chef", None, e["id"], e["nom"]))
    with db.tx(conn):
        for login, role, pid, eid, perim in specs:
            _, pwd, sec = auth.create_user(conn, login, role, pid, eid)
            lines.append(f"{login} ; {pwd} ; {sec or '-'} ; {role} ; {perim}")
    p = db.DATA_DIR / "DEMO_CREDENTIALS.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    return len(specs)
