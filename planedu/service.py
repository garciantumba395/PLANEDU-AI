"""Logique métier PLANEDU-IA : calculs, requêtes avec périmètre par rôle, décisions, import, administration."""
import base64
import csv
import hashlib
import io
import json
import math
import re
import shutil
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path

from . import audit, auth, db, models
from .db import BadRequest, Conflict, Forbidden, NotFound
from .seed import SCARCITE

NIVEAUX = ["primaire", "secondaire"]
ROLES = ("admin", "gestionnaire", "chef", "decideur")
BANNIERE = "Données de démonstration"
HORIZONS = (6, 12, 24)


# ================================================================== utilitaires
def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def _int(v, name, default=None):
    if v in (None, ""):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        raise BadRequest(f"paramètre invalide : {name}")


def _add_months(d, n):
    idx = d.year * 12 + d.month - 1 + n
    y, m = divmod(idx, 12)
    m += 1
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def params(conn):
    p = db.get_params(conn)
    try:
        ref = date.fromisoformat(p.get("date_reference", ""))
    except ValueError:
        ref = date.today()
    return {
        "legal": int(p.get("age_legal_retraite", 65)),
        "max_km": float(p.get("max_km", 150)),
        "annee": int(p.get("annee_cible", ref.year)),
        "ref": ref,
        "hb": int(p.get("horizon_besoin", 12)),
        "horizons": tuple(int(x) for x in p.get("horizons", "6,12,24").split(",")),
    }


# ================================================================== périmètre (ES-01, EF-12)
def require_role(user, *roles):
    if user["role"] not in roles:
        raise Forbidden("rôle non autorisé pour cette action", objet=f"rôle {user['role']}")


def scope_sql(user, c="c", sd="sd"):
    r = user["role"]
    if r in ("admin", "decideur"):
        return "1=1", []
    if r == "gestionnaire":
        return f"{sd}.province_id=?", [user["province_id"]]
    return f"{c}.id=?", [user["ecole_id"]]


def _school_geo(conn, ecole_id):
    r = conn.execute("SELECT c.id, c.sous_division_id AS sd, s.province_id AS pid FROM ecole c "
                     "JOIN sous_division s ON s.id=c.sous_division_id WHERE c.id=?", (ecole_id,)).fetchone()
    return dict(r) if r else None


def check_geo(conn, user, province_id=None, sous_division_id=None, ecole_id=None):
    """Refuse (403) tout filtre explicite hors du périmètre de l'utilisateur."""
    r = user["role"]
    if r in ("admin", "decideur"):
        return
    own = None
    if r == "chef":
        own = _school_geo(conn, user["ecole_id"])
    if province_id is not None:
        ok = (user["province_id"] == province_id) if r == "gestionnaire" else (own and own["pid"] == province_id)
        if not ok:
            raise Forbidden("province hors de votre périmètre", objet=f"province_id={province_id}")
    if sous_division_id is not None:
        row = conn.execute("SELECT province_id FROM sous_division WHERE id=?", (sous_division_id,)).fetchone()
        if row is None:
            raise NotFound("sous-division inconnue")
        ok = (row["province_id"] == user["province_id"]) if r == "gestionnaire" else (own and own["sd"] == sous_division_id)
        if not ok:
            raise Forbidden("sous-division hors de votre périmètre", objet=f"sous_division_id={sous_division_id}")
    if ecole_id is not None:
        g = _school_geo(conn, ecole_id)
        if g is None:
            raise NotFound("école inconnue")
        ok = (g["pid"] == user["province_id"]) if r == "gestionnaire" else (ecole_id == user["ecole_id"])
        if not ok:
            raise Forbidden("école hors de votre périmètre", objet=f"ecole_id={ecole_id}")


def geo_where(conn, user, q, c="c", sd="sd", niveau_col=None):
    pid = _int(q.get("province_id"), "province_id")
    sid = _int(q.get("sous_division_id"), "sous_division_id")
    check_geo(conn, user, pid, sid)
    w, a = [], []
    s, sa = scope_sql(user, c, sd)
    w.append(s)
    a += sa
    if pid:
        w.append(f"{sd}.province_id=?")
        a.append(pid)
    if sid:
        w.append(f"{c}.id IN (SELECT id FROM ecole WHERE sous_division_id=?)")
        a.append(sid)
    niv = q.get("niveau")
    if niv:
        if niv not in NIVEAUX:
            raise BadRequest("niveau invalide")
        w.append(f"{niveau_col or c + '.niveau'}=?")
        a.append(niv)
    return " AND ".join(w), a


def can_see_names(user):
    return user["role"] == "gestionnaire"


def _names(conn, user, ids):
    if not can_see_names(user) or not ids:
        return {}
    out = {}
    ids = list(set(ids))
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in conn.execute(f"SELECT enseignant_id, nom_chiffre FROM identite_enseignant WHERE enseignant_id IN ({','.join('?' * len(part))})", part):
            try:
                out[r["enseignant_id"]] = auth.decrypt_text(r["nom_chiffre"])
            except Exception:
                out[r["enseignant_id"]] = None
    return out


# ================================================================== chargement du monde pour les calculs
def _world(conn):
    schools = {r["id"]: r for r in _rows(conn, "SELECT c.id,c.nom,c.sous_division_id sd,s.province_id pid,c.niveau,c.lat,c.lon "
                                                "FROM ecole c JOIN sous_division s ON s.id=c.sous_division_id")}
    acc = {}
    for s in schools.values():
        if s["lat"] is not None:
            acc.setdefault(s["sd"], []).append((s["lat"], s["lon"]))
    cen = {k: (sum(x[0] for x in v) / len(v), sum(x[1] for x in v) / len(v)) for k, v in acc.items()}
    for s in schools.values():
        c = cen.get(s["sd"])
        s["remote"] = min(1.0, models.haversine_km(s["lat"], s["lon"], *c) / 40.0) if c and s["lat"] is not None else 0.0
    teachers = []
    for r in conn.execute("SELECT e.id,e.date_naissance,e.date_entree,e.niveau,e.matiere,e.statut,a.ecole_id FROM enseignant e "
                          "JOIN affectation a ON a.id=(SELECT MAX(id) FROM affectation WHERE enseignant_id=e.id)"):
        s = schools.get(r["ecole_id"])
        if not s:
            continue
        try:
            birth = date.fromisoformat(r["date_naissance"])
        except ValueError:
            continue
        entry = None
        try:
            entry = date.fromisoformat(r["date_entree"]) if r["date_entree"] else None
        except ValueError:
            pass
        teachers.append(dict(id=r["id"], birth_date=birth, date_entree=entry, niveau=r["niveau"], matiere=r["matiere"],
                             statut=r["statut"], ecole_id=r["ecole_id"], school_id=r["ecole_id"], province_id=s["pid"],
                             subject_scarcity=SCARCITE.get(r["matiere"], 0.3), posting_remote=s["remote"]))
    deps = _rows(conn, "SELECT enseignant_id AS teacher_id, date, motif FROM depart_historique")
    return schools, teachers, deps


# ================================================================== calcul (EF-03/04/05/06/07)
def calculer(conn, province_id, user_login, prevision=True):
    t0 = time.time()
    p = params(conn)
    schools, teachers, deps = _world(conn)
    n_prev = 0
    if prevision:
        active = [t for t in teachers if t["statut"] == "actif"]
        scope = [t for t in active if province_id is None or t["province_id"] == province_id]
        flags = {f["teacher_id"]: f for f in models.retirement_flags(scope, p["legal"], p["ref"], p["horizons"])}
        preds = {}
        for h in p["horizons"]:
            train = []
            for k in range(4):
                train += models.build_training_rows(teachers, deps, _add_months(p["ref"], -h - 6 * k), h)
            model = models.AttritionModel().fit(train, h)
            rows = models.build_training_rows(scope, [], p["ref"], h)
            probs = model.predict_proba(rows)
            for r, pr in zip(rows, probs):
                ex = model.explain(r)[:3]
                preds.setdefault(r["teacher_id"], {})[h] = (float(pr), ex)
        out = []
        for t in scope:
            fl = flags[t["id"]]
            for h in p["horizons"]:
                p_att, ex = preds.get(t["id"], {}).get(h, (0.0, []))
                p_ret = 1.0 if fl[f"h{h}"] else 0.0
                total = 1 - (1 - p_ret) * (1 - p_att)
                raisons = []
                if p_ret:
                    raisons.append(fl["raison"])
                raisons += [f"{lab} ({c:+.2f})" for lab, c in ex if c > 0.05][:2]
                if not raisons and total < 0.2:
                    raisons.append("Risque de départ faible sur l'horizon")
                fact = {"retraite": bool(p_ret), "p_attrition": round(p_att, 4),
                        "facteurs": [{"facteur": lab, "poids": round(c, 3)} for lab, c in ex], "raisons": raisons}
                out.append((t["id"], h, round(total, 4), json.dumps(fact, ensure_ascii=False), models.AttritionModel.version,
                            fl["retirement_date"].isoformat()))
        with db.tx(conn):
            conn.executemany("INSERT OR REPLACE INTO prevision_depart(enseignant_id,horizon,probabilite,facteurs,version_modele,retraite_date) "
                             "VALUES(?,?,?,?,?,?)", out)
        n_prev = len(out)
    n_b, n_r = recalc_besoins(conn, province_id, schools, teachers, p)
    dur = int((time.time() - t0) * 1000)
    return {"previsions": n_prev, "besoins": n_b, "recommandations": n_r, "duree_ms": dur}


def recalc_besoins(conn, province_id=None, schools=None, teachers=None, p=None):
    p = p or params(conn)
    if schools is None:
        schools, teachers, _ = _world(conn)
    pids = [province_id] if province_id else [r["id"] for r in conn.execute("SELECT id FROM province")]
    norms = _rows(conn, "SELECT niveau,matiere,eleves_par_enseignant,en_vigueur_depuis FROM norme")
    enr = {}
    for r in conn.execute("SELECT ecole_id,annee,niveau,nombre FROM effectif_eleves WHERE annee<=? ORDER BY annee", (p["annee"],)):
        enr.setdefault(r["ecole_id"], {})[r["niveau"]] = r["nombre"]  # dernière année <= cible
    leaving_p = {}
    for r in conn.execute("SELECT enseignant_id,probabilite FROM prevision_depart WHERE horizon=?", (p["hb"],)):
        leaving_p[r["enseignant_id"]] = r["probabilite"]
    decided = _rows(conn, "SELECT r.enseignant_id,r.ecole_depart_id,r.ecole_arrivee_id,r.niveau,r.matiere,r.statut,"
                          "d.ecole_arrivee_modifiee_id m FROM recommandation r LEFT JOIN decision d ON d.recommandation_id=r.id "
                          "WHERE r.statut!='en_attente'")
    nb = nr = 0
    for pid in pids:
        sch = {k: v for k, v in schools.items() if v["pid"] == pid}
        required = []
        for sid, s in sch.items():
            if sid in enr:
                required += models.compute_need({"school_id": sid, "year": p["annee"], "levels": enr[sid]}, norms)
        active = [t for t in teachers if t["province_id"] == pid and t["statut"] == "actif"]
        staff = [{"school_id": t["ecole_id"], "niveau": t["niveau"], "matiere": t["matiere"]} for t in active]
        acc = {}
        for t in active:
            k = (t["ecole_id"], t["niveau"], t["matiere"])
            acc[k] = acc.get(k, 0.0) + leaving_p.get(t["id"], 0.0)
        leaving = [{"school_id": k[0], "niveau": k[1], "matiere": k[2], "nombre": int(round(v))} for k, v in acc.items() if round(v) > 0]
        gaps = models.gap(required, staff, leaving)
        with db.tx(conn):
            conn.executemany("DELETE FROM besoin_ecole WHERE ecole_id=?", [(s,) for s in sch])
            conn.executemany("INSERT INTO besoin_ecole(ecole_id,annee,niveau,matiere,requis,prevu,ecart,actuel) VALUES(?,?,?,?,?,?,?,?)",
                             [(g["school_id"], p["annee"], g["niveau"], g["matiere"], g["requis"], g["prevu"], g["ecart"], g["actuel"])
                              for g in gaps if g["school_id"] in sch])
        nb += len(gaps)
        # recommandations : on tient compte des mouvements déjà acceptés
        gidx = {(g["school_id"], g["niveau"], g["matiere"]): g for g in gaps}
        done_teachers = set()
        for d in decided:
            done_teachers.add(d["enseignant_id"])
            if d["statut"] in ("accepte", "modifie"):
                dest = d["m"] or d["ecole_arrivee_id"]
                if (dest, d["niveau"], d["matiere"]) in gidx:
                    gidx[(dest, d["niveau"], d["matiere"])]["ecart"] -= 1
                if (d["ecole_depart_id"], d["niveau"], d["matiere"]) in gidx:
                    gidx[(d["ecole_depart_id"], d["niveau"], d["matiere"])]["ecart"] += 1
        cands = []
        for t in active:
            if t["id"] in done_teachers or leaving_p.get(t["id"], 0) >= 0.5:
                continue
            sen = (p["ref"] - t["date_entree"]).days / 365.25 if t["date_entree"] else 0.0
            cands.append({"id": t["id"], "ecole_id": t["ecole_id"], "niveau": t["niveau"], "matiere": t["matiere"], "seniority_years": sen})
        dcache = {}

        def dist(a, b):
            k = (a, b)
            if k not in dcache:
                sa, sb = schools[a], schools[b]
                dcache[k] = models.haversine_km(sa["lat"], sa["lon"], sb["lat"], sb["lon"]) if sa["lat"] is not None and sb["lat"] is not None else float("inf")
            return dcache[k]

        recs = models.recommend_redeployments(list(gidx.values()), cands, dist, max_km=p["max_km"], today=p["ref"])
        with db.tx(conn):
            conn.executemany("DELETE FROM recommandation WHERE statut='en_attente' AND ecole_depart_id=?", [(s,) for s in sch])
            conn.executemany(
                "INSERT INTO recommandation(enseignant_id,ecole_depart_id,ecole_arrivee_id,gain_ecart,rang,raisons,km,statut,niveau,matiere) "
                "VALUES(?,?,?,?,?,?,?,'en_attente',?,?)",
                [(r["teacher_id"], r["from_school_id"], r["to_school_id"], r["gain_ecart"], r["rank"],
                  json.dumps(r["reasons"], ensure_ascii=False), r["km"], r["niveau"], r["matiere"]) for r in recs])
        nr += len(recs)
    return nb, nr


def generer_fiches(conn):
    """Fiches de performance EF-14 par rétro-test (cutoff = date de référence - 24 mois)."""
    p = params(conn)
    schools, teachers, deps = _world(conn)
    info = {t["id"]: {"subject_scarcity": t["subject_scarcity"], "posting_remote": t["posting_remote"]} for t in teachers}
    cutoff = _add_months(p["ref"], -24)
    horizons = p["horizons"]
    bt = models.backtest(teachers, deps, info, cutoff, horizons)
    tp = {t["id"]: t for t in teachers}
    pname = {r["id"]: r["nom"] for r in conn.execute("SELECT id,nom FROM province")}
    known = [d for d in deps if d["date"] <= cutoff.isoformat()]
    tt = [dict(t, **info[t["id"]]) for t in teachers]
    with db.tx(conn):
        conn.execute("DELETE FROM modele_perf")
        for h in horizons:
            train = []
            for k in range(4):
                train += models.build_training_rows(tt, known, _add_months(cutoff, -h - 6 * k), h)
            test = models.build_training_rows(tt, deps, cutoff, h)
            err_p, err_n = {}, {}
            if train and test:
                m = models.AttritionModel().fit(train, h)
                pr = m.predict_proba(test)
                for key, getter, store in (("prov", lambda t: pname.get(t["province_id"]), err_p), ("niv", lambda t: t["niveau"], err_n)):
                    grp = {}
                    for r, q in zip(test, pr):
                        g = getter(tp[r["teacher_id"]])
                        grp.setdefault(g, []).append((q, 1.0 if r["left"] else 0.0))
                    for g, v in grp.items():
                        pm = sum(x[0] for x in v) / len(v)
                        om = sum(x[1] for x in v) / len(v)
                        store[g] = {"n": len(v), "taux_predit": round(pm, 4), "taux_observe": round(om, 4), "erreur_abs": round(abs(pm - om), 4)}
            met = {k: v for k, v in bt[h].items()}
            conn.execute("INSERT INTO modele_perf(horizon,methode,donnees,date,erreur_par_province,erreur_par_niveau,metriques,version_modele) VALUES(?,?,?,?,?,?,?,?)",
                         (h, "Régression logistique L2 (numpy) + règle légale de retraite",
                          f"Départs historiques synthétiques {len(deps)} ; retro-test coupure {cutoff.isoformat()} ; {met.get('n', 0)} enseignants évalués",
                          _now(), json.dumps(err_p, ensure_ascii=False), json.dumps(err_n, ensure_ascii=False),
                          json.dumps(met, ensure_ascii=False), models.AttritionModel.version))


def performance(conn, user, q):
    h = _int(q.get("horizon"), "horizon")
    sql = "SELECT * FROM modele_perf" + (" WHERE horizon=?" if h else "") + " ORDER BY horizon"
    out = []
    for r in conn.execute(sql, (h,) if h else ()):
        met = json.loads(r["metriques"] or "{}")
        out.append({"horizon": r["horizon"], "methode": r["methode"], "donnees": r["donnees"], "date": r["date"],
                    "metriques": met, "erreur_par_province": json.loads(r["erreur_par_province"] or "{}"),
                    "erreur_par_niveau": json.loads(r["erreur_par_niveau"] or "{}"),
                    "baseline": {"auc": met.get("baseline_auc"), "precision_at_k": met.get("baseline_precision_at_k"),
                                 "recall_at_k": met.get("baseline_recall_at_k")},
                    "version_modele": r["version_modele"],
                    "avertissement": "Données synthétiques : ces performances ne préjugent pas des résultats sur données réelles. "
                                     "Le modèle est un outil d'aide à la décision, jamais une décision automatique."})
    return {"items": out} if not h else (out[0] if out else {})


# ================================================================== lectures
def geo(conn, user):
    r = user["role"]
    if r in ("admin", "decideur"):
        pw, sw, a = "1=1", "1=1", []
    elif r == "gestionnaire":
        pw, sw, a = "id=?", "province_id=?", [user["province_id"]]
    else:
        g = _school_geo(conn, user["ecole_id"])
        pw, sw, a = "id=?", "id=?", None
        return {"provinces": _rows(conn, "SELECT id,nom FROM province WHERE id=?", (g["pid"],)),
                "sous_divisions": _rows(conn, "SELECT id,nom,province_id FROM sous_division WHERE id=?", (g["sd"],)), "niveaux": NIVEAUX}
    return {"provinces": _rows(conn, f"SELECT id,nom FROM province WHERE {pw}", a if r == "gestionnaire" else []),
            "sous_divisions": _rows(conn, f"SELECT id,nom,province_id FROM sous_division WHERE {sw}", a if r == "gestionnaire" else []),
            "niveaux": NIVEAUX}


def dashboard(conn, user, q):
    t0 = time.time()
    p = params(conn)
    h = _int(q.get("horizon"), "horizon", 12)
    where, a = geo_where(conn, user, q)
    lim = p["ref"].isoformat()
    base_t = ("FROM enseignant e JOIN affectation af ON af.enseignant_id=e.id AND af.fin IS NULL JOIN ecole c ON c.id=af.ecole_id "
              "JOIN sous_division sd ON sd.id=c.sous_division_id WHERE e.statut='actif' AND " + where)
    nens = conn.execute("SELECT COUNT(*) " + base_t, a).fetchone()[0]
    dp = conn.execute("SELECT COALESCE(SUM(pd.probabilite),0) " + base_t.replace("WHERE", "JOIN prevision_depart pd ON pd.enseignant_id=e.id AND pd.horizon=? WHERE", 1),
                      [h] + a).fetchone()[0]
    bw = "FROM besoin_ecole b JOIN ecole c ON c.id=b.ecole_id JOIN sous_division sd ON sd.id=c.sous_division_id WHERE " + where
    ecart_total, ecoles_manque = conn.execute("SELECT COALESCE(SUM(CASE WHEN b.ecart>0 THEN b.ecart END),0), "
                                              "COUNT(DISTINCT CASE WHEN b.ecart>0 THEN b.ecole_id END) " + bw, a).fetchone()
    ok = conn.execute("SELECT COUNT(*) " + base_t + " AND length(e.date_naissance)=10 AND e.date_entree IS NOT NULL AND length(e.date_entree)=10 "
                      "AND e.matiere!='' AND e.niveau!=''", a).fetchone()[0]
    qual = round(100.0 * ok / nens, 1) if nens else 100.0
    rw = ("FROM recommandation r JOIN ecole c ON c.id=r.ecole_depart_id JOIN sous_division sd ON sd.id=c.sous_division_id WHERE " + where)
    tot, tr = conn.execute("SELECT COUNT(*), COALESCE(SUM(CASE WHEN r.statut!='en_attente' THEN 1 ELSE 0 END),0) " + rw, a).fetchone()
    reco_pct = round(100.0 * tr / tot, 1) if tot else 0.0
    # par sous-division
    sds = {}
    for r in conn.execute("SELECT sd.id,sd.nom,sd.province_id,AVG(c.lat) lat,AVG(c.lon) lon,COUNT(*) n FROM ecole c JOIN sous_division sd ON sd.id=c.sous_division_id WHERE "
                          + where + " GROUP BY sd.id", a):
        sds[r["id"]] = {"id": r["id"], "nom": r["nom"], "province_id": r["province_id"], "lat": r["lat"], "lon": r["lon"],
                        "departs": 0.0, "ecart": 0, "ecoles": r["n"], "manque": 0}
    for r in conn.execute("SELECT sd.id sid, SUM(pd.probabilite) dep " + base_t.replace("WHERE", "JOIN prevision_depart pd ON pd.enseignant_id=e.id AND pd.horizon=? WHERE", 1) + " GROUP BY sd.id", [h] + a):
        if r["sid"] in sds:
            sds[r["sid"]]["departs"] = round(r["dep"], 1)
    for r in conn.execute("SELECT sd.id sid, COALESCE(SUM(CASE WHEN b.ecart>0 THEN b.ecart END),0) ec, COUNT(DISTINCT CASE WHEN b.ecart>0 THEN b.ecole_id END) m " + bw + " GROUP BY sd.id", a):
        if r["sid"] in sds:
            sds[r["sid"]]["ecart"] = r["ec"]
            sds[r["sid"]]["manque"] = r["m"]
    par_niv = []
    for niv in NIVEAUX:
        d = conn.execute("SELECT COALESCE(SUM(pd.probabilite),0) " + base_t.replace("WHERE", "JOIN prevision_depart pd ON pd.enseignant_id=e.id AND pd.horizon=? WHERE", 1) + " AND e.niveau=?", [h] + a + [niv]).fetchone()[0]
        e = conn.execute("SELECT COALESCE(SUM(CASE WHEN b.ecart>0 THEN b.ecart END),0) " + bw + " AND b.niveau=?", a + [niv]).fetchone()[0]
        par_niv.append({"niveau": niv, "departs": round(d, 1), "ecart": e})
    serie = []
    for hh in p["horizons"]:
        lim_h = _add_months(p["ref"], hh).isoformat()
        r = conn.execute("SELECT COALESCE(SUM(CASE WHEN pd.retraite_date<=? THEN 1 ELSE 0 END),0) ret, "
                         "COALESCE(SUM(CASE WHEN pd.retraite_date<=? THEN 0 ELSE pd.probabilite END),0) att "
                         + base_t.replace("WHERE", "JOIN prevision_depart pd ON pd.enseignant_id=e.id AND pd.horizon=? WHERE", 1), [lim_h, lim_h, hh] + a).fetchone()
        serie.append({"horizon": hh, "retraite": r["ret"], "abandon_absence": round(r["att"], 1)})
    return {"kpis": {"enseignants": nens, "departs_prevus": int(round(dp)), "ecart_total": ecart_total, "ecoles_en_manque": ecoles_manque,
                     "qualite_donnees_pct": qual, "reco_traitees_pct": reco_pct},
            "par_sous_division": list(sds.values()), "par_niveau": par_niv, "serie_departs": serie, "banniere": BANNIERE,
            "date_reference": lim, "calcul_ms": int((time.time() - t0) * 1000)}


def previsions(conn, user, q):
    if user["role"] == "decideur":
        raise Forbidden("le décideur n'accède qu'aux données agrégées", objet="previsions individuelles")
    h = _int(q.get("horizon"), "horizon", 12)
    if h not in (6, 12, 24):
        raise BadRequest("horizon invalide (6, 12 ou 24)")
    lim = min(_int(q.get("limit"), "limit", 100), 100000)
    off = _int(q.get("offset"), "offset", 0)
    where, a = geo_where(conn, user, q, niveau_col="e.niveau")
    base = ("FROM enseignant e JOIN affectation af ON af.enseignant_id=e.id AND af.fin IS NULL JOIN ecole c ON c.id=af.ecole_id "
            "JOIN sous_division sd ON sd.id=c.sous_division_id JOIN prevision_depart p ON p.enseignant_id=e.id AND p.horizon=? "
            "LEFT JOIN prevision_depart p6 ON p6.enseignant_id=e.id AND p6.horizon=6 LEFT JOIN prevision_depart p12 ON p12.enseignant_id=e.id AND p12.horizon=12 "
            "LEFT JOIN prevision_depart p24 ON p24.enseignant_id=e.id AND p24.horizon=24 WHERE e.statut='actif' AND " + where)
    total = conn.execute("SELECT COUNT(*) " + base, [h] + a).fetchone()[0]
    rows = _rows(conn, "SELECT e.id,e.niveau,e.matiere,c.nom ecole_nom,p.retraite_date,p.probabilite pt,p.facteurs,p6.probabilite p6,p12.probabilite p12,p24.probabilite p24 "
                       + base + " ORDER BY p.probabilite DESC, e.id LIMIT ? OFFSET ?", [h] + a + [lim, off])
    names = _names(conn, user, [r["id"] for r in rows])
    items = []
    for r in rows:
        f = json.loads(r["facteurs"] or "{}")
        items.append({"enseignant_id": r["id"], "nom": names.get(r["id"]), "niveau": r["niveau"], "matiere": r["matiere"],
                      "ecole_nom": r["ecole_nom"], "retraite_date": r["retraite_date"], "p6": r["p6"], "p12": r["p12"], "p24": r["p24"],
                      "probabilite_totale": r["pt"], "facteurs": f.get("facteurs", []), "raisons": f.get("raisons", [])})
    return {"total": total, "items": items}


def besoins(conn, user, q):
    where, a = geo_where(conn, user, q, niveau_col="b.niveau")
    ec = q.get("ecart", "tous")
    extra = {"manque": " AND b.ecart>0", "surplus": " AND b.ecart<0", "tous": ""}.get(ec)
    if extra is None:
        raise BadRequest("ecart doit valoir manque, surplus ou tous")
    rows = _rows(conn, "SELECT b.ecole_id,c.nom ecole_nom,sd.nom sous_division,b.niveau,b.matiere,b.requis,b.actuel,b.prevu,b.ecart,b.annee "
                       "FROM besoin_ecole b JOIN ecole c ON c.id=b.ecole_id JOIN sous_division sd ON sd.id=c.sous_division_id WHERE "
                       + where + extra + " ORDER BY b.ecart DESC, c.nom, b.matiere", a)
    return {"items": rows}


def _reco_item(r, names, max_km):
    contr = [f"Même matière : {r['matiere']}", f"Même niveau : {r['niveau']}", f"Distance {r['km']:.0f} km ≤ {max_km:.0f} km",
             "Proposition uniquement : aucun effet sur la paie ni la carrière"]
    dec = None
    if r["d_choix"]:
        dec = {"choix": r["d_choix"], "motif": r["d_motif"], "utilisateur": r["d_user"], "date": r["d_date"],
               "ecole_arrivee_modifiee_id": r["d_mod"]}
    return {"id": r["id"], "rang": r["rang"], "enseignant_id": r["enseignant_id"], "nom": names.get(r["enseignant_id"]),
            "matiere": r["matiere"], "niveau": r["niveau"], "ecole_depart": r["dep_nom"], "ecole_depart_id": r["ecole_depart_id"],
            "ecole_arrivee": r["arr_nom"], "ecole_arrivee_id": r["ecole_arrivee_id"], "gain_ecart": r["gain_ecart"], "km": r["km"],
            "raisons": json.loads(r["raisons"] or "[]"), "contraintes_respectees": contr, "statut": r["statut"], "decision": dec}


def _reco_query(conn, user, q, limit, offset):
    if user["role"] == "decideur":
        raise Forbidden("le décideur n'accède qu'aux données agrégées", objet="recommandations individuelles")
    st = q.get("statut", "tous")
    if st not in ("en_attente", "accepte", "modifie", "refuse", "tous"):
        raise BadRequest("statut invalide")
    pid = _int(q.get("province_id"), "province_id")
    sid = _int(q.get("sous_division_id"), "sous_division_id")
    check_geo(conn, user, pid, sid)
    w, a = [], []
    if user["role"] == "gestionnaire":
        w.append("sd.province_id=?")
        a.append(user["province_id"])
    elif user["role"] == "chef":
        w.append("(r.ecole_depart_id=? OR r.ecole_arrivee_id=?)")
        a += [user["ecole_id"], user["ecole_id"]]
    if pid:
        w.append("sd.province_id=?")
        a.append(pid)
    if sid:
        w.append("sd.id=?")
        a.append(sid)
    if q.get("niveau"):
        w.append("r.niveau=?")
        a.append(q["niveau"])
    if st != "tous":
        w.append("r.statut=?")
        a.append(st)
    wh = " AND ".join(w) or "1=1"
    base = ("FROM recommandation r JOIN ecole c ON c.id=r.ecole_depart_id JOIN sous_division sd ON sd.id=c.sous_division_id "
            "JOIN ecole ea ON ea.id=r.ecole_arrivee_id LEFT JOIN decision d ON d.recommandation_id=r.id WHERE " + wh)
    total = conn.execute("SELECT COUNT(*) " + base, a).fetchone()[0]
    rows = _rows(conn, "SELECT r.*, c.nom dep_nom, ea.nom arr_nom, d.choix d_choix, d.motif d_motif, d.utilisateur d_user, d.date d_date, "
                       "d.ecole_arrivee_modifiee_id d_mod " + base + " ORDER BY (r.statut='en_attente') DESC, r.rang, r.id LIMIT ? OFFSET ?",
                 a + [limit, offset])
    return total, rows


def recommandations(conn, user, q):
    lim = min(_int(q.get("limit"), "limit", 50), 100000)
    off = _int(q.get("offset"), "offset", 0)
    total, rows = _reco_query(conn, user, q, lim, off)
    names = _names(conn, user, [r["enseignant_id"] for r in rows])
    mk = params(conn)["max_km"]
    return {"total": total, "items": [_reco_item(r, names, mk) for r in rows]}


def decider(conn, user, reco_id, body):
    require_role(user, "gestionnaire")
    r = conn.execute("SELECT r.*, sd.province_id pid FROM recommandation r JOIN ecole c ON c.id=r.ecole_depart_id "
                     "JOIN sous_division sd ON sd.id=c.sous_division_id WHERE r.id=?", (reco_id,)).fetchone()
    if r is None:
        raise NotFound("recommandation inconnue")
    if r["pid"] != user["province_id"]:
        raise Forbidden("recommandation hors de votre province", objet=f"recommandation {reco_id}")
    choix = body.get("choix")
    if choix not in ("accepte", "modifie", "refuse"):
        raise BadRequest("choix doit valoir accepte, modifie ou refuse")
    motif = str(body.get("motif") or "").strip()
    if not motif:
        raise BadRequest("le motif est obligatoire")
    if len(motif) > 1000:
        raise BadRequest("motif trop long")
    new_ec = None
    if choix == "modifie":
        new_ec = _int(body.get("ecole_arrivee_id"), "ecole_arrivee_id")
        if not new_ec:
            raise BadRequest("ecole_arrivee_id obligatoire pour 'modifie'")
        g = _school_geo(conn, new_ec)
        if g is None:
            raise NotFound("école d'arrivée inconnue")
        if g["pid"] != user["province_id"]:
            raise Forbidden("école d'arrivée hors de votre province", objet=f"ecole_id={new_ec}")
    now = _now()
    try:
        with db.tx(conn):
            if conn.execute("SELECT 1 FROM decision WHERE recommandation_id=?", (reco_id,)).fetchone():
                raise Conflict("cette recommandation a déjà fait l'objet d'une décision")
            conn.execute("INSERT INTO decision(recommandation_id,utilisateur,choix,motif,date,ecole_arrivee_modifiee_id) VALUES(?,?,?,?,?,?)",
                         (reco_id, user["login"], choix, motif, now, new_ec))
            conn.execute("UPDATE recommandation SET statut=? WHERE id=?", (choix, reco_id))
            audit.log(conn, user["login"], f"decision_{choix}", f"recommandation {reco_id} enseignant {r['enseignant_id']}")
    except sqlite3.IntegrityError:
        raise Conflict("cette recommandation a déjà fait l'objet d'une décision")
    return {"recommandation_id": reco_id, "choix": choix, "motif": motif, "utilisateur": user["login"], "date": now,
            "ecole_arrivee_modifiee_id": new_ec}


# ================================================================== normes & paramètres (EF-13)
def get_normes(conn):
    return {"normes": _rows(conn, "SELECT id,niveau,matiere,eleves_par_enseignant,en_vigueur_depuis FROM norme ORDER BY niveau,matiere,en_vigueur_depuis")}


def put_normes(conn, user, body):
    require_role(user, "admin")
    items = body if isinstance(body, list) else body.get("normes")
    if not isinstance(items, list) or not items:
        raise BadRequest("liste 'normes' attendue")
    clean = []
    for it in items:
        try:
            niv, mat = str(it["niveau"]), str(it["matiere"]).strip()
            ratio, yr = int(it["eleves_par_enseignant"]), int(it.get("en_vigueur_depuis", 2020))
        except (KeyError, TypeError, ValueError):
            raise BadRequest("norme invalide : niveau, matiere, eleves_par_enseignant requis")
        if niv not in NIVEAUX or not mat or ratio < 1 or ratio > 500 or not (1990 <= yr <= 2100):
            raise BadRequest("norme invalide (niveau, matière, ratio 1..500, année)")
        clean.append((it.get("id"), niv, mat, ratio, yr))
    with db.tx(conn):
        for i, niv, mat, ratio, yr in clean:
            if i:
                if conn.execute("UPDATE norme SET niveau=?,matiere=?,eleves_par_enseignant=?,en_vigueur_depuis=? WHERE id=?", (niv, mat, ratio, yr, i)).rowcount == 0:
                    raise NotFound(f"norme {i} inconnue")
            else:
                conn.execute("INSERT INTO norme(niveau,matiere,eleves_par_enseignant,en_vigueur_depuis) VALUES(?,?,?,?)", (niv, mat, ratio, yr))
        audit.log(conn, user["login"], "normes_modifiees", json.dumps(clean, ensure_ascii=False)[:500])
    res = recalc_besoins(conn)
    out = get_normes(conn)
    out["recalcul"] = {"besoins": res[0], "recommandations": res[1]}
    return out


def get_parametres(conn):
    p = params(conn)
    return {"age_legal_retraite": p["legal"], "max_km": p["max_km"], "annee_cible": p["annee"], "horizons": list(p["horizons"]),
            "date_reference": p["ref"].isoformat()}


def put_parametres(conn, user, body):
    require_role(user, "admin")
    upd, old = {}, params(conn)
    try:
        if "age_legal_retraite" in body:
            v = int(body["age_legal_retraite"])
            if not 50 <= v <= 75:
                raise ValueError
            upd["age_legal_retraite"] = v
        if "max_km" in body:
            v = float(body["max_km"])
            if not 0 < v <= 2000:
                raise ValueError
            upd["max_km"] = v
        if "annee_cible" in body:
            v = int(body["annee_cible"])
            if not 2000 <= v <= 2100:
                raise ValueError
            upd["annee_cible"] = v
        if "date_reference" in body:
            upd["date_reference"] = date.fromisoformat(str(body["date_reference"])).isoformat()
    except (TypeError, ValueError):
        raise BadRequest("paramètre invalide")
    if not upd:
        raise BadRequest("aucun paramètre à modifier")
    with db.tx(conn):
        for k, v in upd.items():
            conn.execute("INSERT INTO parametre(cle,valeur) VALUES(?,?) ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur", (k, str(v)))
        audit.log(conn, user["login"], "parametres_modifies", json.dumps(upd))
    full = "age_legal_retraite" in upd or "date_reference" in upd
    res = calculer(conn, None, user["login"], prevision=full)
    out = get_parametres(conn)
    out["recalcul"] = res
    return out


def run_calcul(conn, user):
    require_role(user, "gestionnaire", "admin")
    pid = user["province_id"] if user["role"] == "gestionnaire" else None
    res = calculer(conn, pid, user["login"])
    audit.log(conn, user["login"], "calcul", json.dumps(res))
    return res


# ================================================================== alertes simulées (EF-10)
def _alert_items(conn, user, ecole_ids=None):
    require_role(user, "gestionnaire")
    p = params(conn)
    rows = _rows(conn, "SELECT b.ecole_id, c.nom, b.matiere, b.ecart, b.prevu FROM besoin_ecole b JOIN ecole c ON c.id=b.ecole_id "
                       "JOIN sous_division sd ON sd.id=c.sous_division_id WHERE sd.province_id=? AND b.ecart>0 ORDER BY b.ecole_id", (user["province_id"],))
    by = {}
    for r in rows:
        by.setdefault((r["ecole_id"], r["nom"]), []).append(r)
    items = []
    for (eid, nom), rs in by.items():
        if ecole_ids and eid not in ecole_ids:
            continue
        tot = sum(r["ecart"] for r in rs)
        det = ", ".join(f"{r['matiere']} -{r['ecart']}" for r in rs)
        msg = (f"PLANEDU - {nom} : manque prévu de {tot} enseignant(s) pour {p['annee']} ({det}). "
               f"Départs probables sous {p['hb']} mois pris en compte. Contactez votre gestionnaire pour le redéploiement.")
        if len(msg) > 320:
            msg = msg[:317] + "..."
        items.append({"ecole_id": eid, "ecole_nom": nom, "message": msg, "longueur": len(msg)})
    return items


def alertes_preview(conn, user):
    return {"items": _alert_items(conn, user), "simulation": True, "canal": "à confirmer"}


def alertes_envoyer(conn, user, body):
    ids = body.get("ecole_ids")
    ids = {int(i) for i in ids} if isinstance(ids, list) and ids else None
    if ids:
        for i in ids:
            check_geo(conn, user, ecole_id=i)
    items = _alert_items(conn, user, ids)
    audit.log(conn, user["login"], "alerte_simulee", f"{len(items)} message(s) simulés, aucun envoi réel")
    return {"simulation": True, "canal": "à confirmer", "envoyes": len(items), "items": items,
            "avertissement": "Simulation journalisée : aucun SMS ni courriel n'a été envoyé."}


# ================================================================== import CSV / XLSX (EF-01, EF-02, ES-04)
IMPORT_SPEC = {
    "enseignants": {"allowed": ["matricule", "nom", "date_naissance", "date_entree", "niveau", "matiere", "ecole_id", "statut"],
                    "required": ["matricule", "nom", "date_naissance", "niveau", "matiere", "ecole_id"],
                    "example": ["MAT-000001", "Prénom NOM", "1985-03-14", "2010-09-01", "primaire", "Polyvalent", "1", "actif"]},
    "ecoles": {"allowed": ["nom", "sous_division_id", "niveau", "lat", "lon"], "required": ["nom", "sous_division_id", "niveau"],
               "example": ["EP Exemple", "1", "primaire", "-5.82", "13.46"]},
    "effectifs": {"allowed": ["ecole_id", "annee", "niveau", "nombre"], "required": ["ecole_id", "annee", "niveau", "nombre"],
                  "example": ["1", "2026", "primaire", "520"]},
}
MAX_IMPORT_BYTES = 20 * 1024 * 1024
MAX_IMPORT_ROWS = 50000


def import_template(kind):
    if kind not in IMPORT_SPEC:
        raise NotFound("type d'import inconnu")
    sp = IMPORT_SPEC[kind]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(sp["allowed"])
    w.writerow(sp["example"])
    return out.getvalue().encode("utf-8")


def _parse_table(filename, raw):
    """Retourne (en-têtes, [(n° ligne, dict)])."""
    name = (filename or "").lower()
    if name.endswith(".xlsx") or raw[:2] == b"PK":
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        except Exception:
            raise BadRequest("fichier XLSX illisible")
        ws = wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        try:
            head = [str(c).strip().lower() if c is not None else "" for c in next(it)]
        except StopIteration:
            raise BadRequest("fichier vide")
        rows = []
        for n, r in enumerate(it, start=2):
            if all(c is None or str(c).strip() == "" for c in r):
                continue
            rows.append((n, {head[i]: r[i] for i in range(min(len(head), len(r))) if head[i]}))
        return [h for h in head if h], rows
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    first = text.split("\n", 1)[0]
    delim = max([",", ";", "\t"], key=first.count)
    rd = csv.reader(io.StringIO(text), delimiter=delim)
    try:
        head = [h.strip().lower() for h in next(rd)]
    except StopIteration:
        raise BadRequest("fichier vide")
    rows = []
    for n, r in enumerate(rd, start=2):
        if not r or all(not c.strip() for c in r):
            continue
        rows.append((n, {head[i]: r[i].strip() for i in range(min(len(head), len(r))) if head[i]}))
    return [h for h in head if h], rows


def _s(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def import_data(conn, user, kind, filename, raw):
    require_role(user, "gestionnaire", "admin")
    if kind not in IMPORT_SPEC:
        raise BadRequest("kind doit valoir enseignants, ecoles ou effectifs")
    if len(raw) > MAX_IMPORT_BYTES:
        raise BadRequest("fichier trop volumineux (20 Mo max)")
    sp = IMPORT_SPEC[kind]
    head, rows = _parse_table(filename, raw)
    if len(rows) > MAX_IMPORT_ROWS:
        raise BadRequest("trop de lignes (50 000 max)")
    rapport = []
    for h in head:
        if h not in sp["allowed"]:
            rapport.append({"ligne": 1, "champ": h, "erreur": "colonne non autorisée (liste blanche ; aucune donnée biométrique ou hors modèle n'est importée)"})
    for h in sp["required"]:
        if h not in head:
            rapport.append({"ligne": 1, "champ": h, "erreur": "colonne obligatoire absente"})
    if rapport:
        audit.log(conn, user["login"], "import_rejete", f"{kind} {filename} : structure non conforme")
        return {"importes": 0, "rejetes": len(rows), "rapport": rapport, "fichier_rejete": True}
    p = params(conn)
    geo_cache = {}

    def school(v, field, ligne, err):
        try:
            i = int(float(v))
        except ValueError:
            err.append({"ligne": ligne, "champ": field, "erreur": "identifiant d'école invalide"})
            return None
        if i not in geo_cache:
            geo_cache[i] = conn.execute("SELECT c.id, c.niveau, c.sous_division_id sd, s.province_id pid FROM ecole c JOIN sous_division s ON s.id=c.sous_division_id WHERE c.id=?", (i,)).fetchone()
        g = geo_cache[i]
        if g is None:
            err.append({"ligne": ligne, "champ": field, "erreur": f"école {i} inconnue"})
            return None
        if user["role"] == "gestionnaire" and g["pid"] != user["province_id"]:
            err.append({"ligne": ligne, "champ": field, "erreur": "école hors de votre province"})
            return None
        return g

    ok_rows, seen = [], set()
    for n, r in rows:
        err = []
        g = {k: _s(r.get(k)) for k in sp["allowed"]}
        if kind == "enseignants":
            if not g["matricule"]:
                err.append({"ligne": n, "champ": "matricule", "erreur": "obligatoire"})
            elif len(g["matricule"]) > 40:
                err.append({"ligne": n, "champ": "matricule", "erreur": "trop long"})
            elif g["matricule"] in seen:
                err.append({"ligne": n, "champ": "matricule", "erreur": "doublon dans le fichier"})
            if not g["nom"]:
                err.append({"ligne": n, "champ": "nom", "erreur": "obligatoire"})
            bd = ed = None
            try:
                bd = date.fromisoformat(g["date_naissance"])
                if not (date(1940, 1, 1) <= bd <= _add_months(p["ref"], -18 * 12)):
                    err.append({"ligne": n, "champ": "date_naissance", "erreur": "date de naissance invraisemblable"})
            except ValueError:
                err.append({"ligne": n, "champ": "date_naissance", "erreur": "format attendu AAAA-MM-JJ"})
            if g["date_entree"]:
                try:
                    ed = date.fromisoformat(g["date_entree"])
                    if ed > p["ref"] or (bd and ed < _add_months(bd, 16 * 12)):
                        err.append({"ligne": n, "champ": "date_entree", "erreur": "date d'entrée incohérente avec la naissance ou la date de référence"})
                except ValueError:
                    err.append({"ligne": n, "champ": "date_entree", "erreur": "format attendu AAAA-MM-JJ"})
            if g["niveau"] not in NIVEAUX:
                err.append({"ligne": n, "champ": "niveau", "erreur": "doit valoir primaire ou secondaire"})
            if not g["matiere"] or len(g["matiere"]) > 60:
                err.append({"ligne": n, "champ": "matiere", "erreur": "obligatoire (60 caractères max)"})
            if g["statut"] and g["statut"] not in ("actif", "suspendu", "parti"):
                err.append({"ligne": n, "champ": "statut", "erreur": "doit valoir actif, suspendu ou parti"})
            sc = school(g["ecole_id"], "ecole_id", n, err) if g["ecole_id"] else (err.append({"ligne": n, "champ": "ecole_id", "erreur": "obligatoire"}) or None)
            if sc is not None and g["niveau"] in NIVEAUX and sc["niveau"] != g["niveau"]:
                err.append({"ligne": n, "champ": "niveau", "erreur": f"incohérent avec le niveau de l'école ({sc['niveau']})"})
            if not err:
                seen.add(g["matricule"])
                ok_rows.append((g, sc["id"]))
        elif kind == "ecoles":
            if not g["nom"]:
                err.append({"ligne": n, "champ": "nom", "erreur": "obligatoire"})
            sdid = None
            try:
                sdid = int(float(g["sous_division_id"]))
                row = conn.execute("SELECT province_id FROM sous_division WHERE id=?", (sdid,)).fetchone()
                if row is None:
                    err.append({"ligne": n, "champ": "sous_division_id", "erreur": "sous-division inconnue"})
                elif user["role"] == "gestionnaire" and row["province_id"] != user["province_id"]:
                    err.append({"ligne": n, "champ": "sous_division_id", "erreur": "sous-division hors de votre province"})
            except ValueError:
                err.append({"ligne": n, "champ": "sous_division_id", "erreur": "entier attendu"})
            if g["niveau"] not in NIVEAUX:
                err.append({"ligne": n, "champ": "niveau", "erreur": "doit valoir primaire ou secondaire"})
            lat = lon = None
            try:
                lat = float(g["lat"].replace(",", ".")) if g["lat"] else None
                if lat is not None and not -90 <= lat <= 90:
                    raise ValueError
            except ValueError:
                err.append({"ligne": n, "champ": "lat", "erreur": "latitude invalide"})
            try:
                lon = float(g["lon"].replace(",", ".")) if g["lon"] else None
                if lon is not None and not -180 <= lon <= 180:
                    raise ValueError
            except ValueError:
                err.append({"ligne": n, "champ": "lon", "erreur": "longitude invalide"})
            if not err:
                ok_rows.append(((g["nom"], sdid, g["niveau"], lat, lon), None))
        else:
            sc = school(g["ecole_id"], "ecole_id", n, err) if g["ecole_id"] else (err.append({"ligne": n, "champ": "ecole_id", "erreur": "obligatoire"}) or None)
            yr = nb = None
            try:
                yr = int(float(g["annee"]))
                if not 2000 <= yr <= 2100:
                    raise ValueError
            except ValueError:
                err.append({"ligne": n, "champ": "annee", "erreur": "année invalide"})
            try:
                nb = int(float(g["nombre"]))
                if not 0 <= nb <= 100000:
                    raise ValueError
            except ValueError:
                err.append({"ligne": n, "champ": "nombre", "erreur": "entier positif attendu"})
            if g["niveau"] not in NIVEAUX:
                err.append({"ligne": n, "champ": "niveau", "erreur": "doit valoir primaire ou secondaire"})
            if not err:
                ok_rows.append(((sc["id"], yr, g["niveau"], nb), None))
        rapport += err
    with db.tx(conn):
        for item, sid in ok_rows:
            if kind == "enseignants":
                g = item
                tid = auth.pseudonym(g["matricule"])  # le matricule n'est jamais stocké
                conn.execute("INSERT INTO enseignant(id,date_naissance,date_entree,niveau,matiere,statut) VALUES(?,?,?,?,?,?) "
                             "ON CONFLICT(id) DO UPDATE SET date_naissance=excluded.date_naissance,date_entree=excluded.date_entree,"
                             "niveau=excluded.niveau,matiere=excluded.matiere,statut=excluded.statut",
                             (tid, g["date_naissance"], g["date_entree"] or None, g["niveau"], g["matiere"], g["statut"] or "actif"))
                conn.execute("INSERT OR REPLACE INTO identite_enseignant VALUES(?,?)", (tid, auth.encrypt_text(g["nom"])))
                cur = conn.execute("SELECT id,ecole_id FROM affectation WHERE enseignant_id=? AND fin IS NULL", (tid,)).fetchone()
                if cur is None or cur["ecole_id"] != sid:
                    if cur:
                        conn.execute("UPDATE affectation SET fin=? WHERE id=?", (p["ref"].isoformat(), cur["id"]))
                    conn.execute("INSERT INTO affectation(enseignant_id,ecole_id,debut,fin) VALUES(?,?,?,NULL)", (tid, sid, g["date_entree"] or p["ref"].isoformat()))
            elif kind == "ecoles":
                conn.execute("INSERT INTO ecole(nom,sous_division_id,niveau,lat,lon) VALUES(?,?,?,?,?)", item)
            else:
                conn.execute("INSERT INTO effectif_eleves(ecole_id,annee,niveau,nombre) VALUES(?,?,?,?) "
                             "ON CONFLICT(ecole_id,annee,niveau) DO UPDATE SET nombre=excluded.nombre", item)
        audit.log(conn, user["login"], "import", f"{kind} {filename} : {len(ok_rows)} importé(s), {len(rows) - len(ok_rows)} rejeté(s)")
    res = {"importes": len(ok_rows), "rejetes": len(rows) - len(ok_rows), "rapport": rapport}
    if ok_rows:
        res["recalcul"] = calculer(conn, user["province_id"] if user["role"] == "gestionnaire" else None, user["login"])
    return res


# ================================================================== exports (EF-11)
def export_table(conn, user, table, q):
    """Retourne (titre, colonnes, lignes) avec les mêmes filtres que l'écran ; noms uniquement pour le gestionnaire."""
    q = dict(q, limit="100000", offset="0")
    if table == "previsions":
        d = previsions(conn, user, q)
        cols = ["enseignant_id"] + (["nom"] if can_see_names(user) else []) + ["niveau", "matiere", "ecole", "retraite_date", "p6", "p12", "p24", "raisons"]
        rows = [[i["enseignant_id"]] + ([i["nom"]] if can_see_names(user) else []) +
                [i["niveau"], i["matiere"], i["ecole_nom"], i["retraite_date"], i["p6"], i["p12"], i["p24"], " ; ".join(i["raisons"])] for i in d["items"]]
        return "Prévisions de départs", cols, rows
    if table == "besoins":
        d = besoins(conn, user, q)
        cols = ["ecole", "sous_division", "niveau", "matiere", "requis", "actuel", "prevu", "ecart"]
        return "Besoins et écarts", cols, [[i["ecole_nom"], i["sous_division"], i["niveau"], i["matiere"], i["requis"], i["actuel"], i["prevu"], i["ecart"]] for i in d["items"]]
    if table == "recommandations":
        d = recommandations(conn, user, q)
        cols = ["rang", "enseignant_id"] + (["nom"] if can_see_names(user) else []) + ["niveau", "matiere", "ecole_depart", "ecole_arrivee", "km", "gain_ecart", "statut", "motif_decision", "raisons"]
        rows = [[i["rang"], i["enseignant_id"]] + ([i["nom"]] if can_see_names(user) else []) +
                [i["niveau"], i["matiere"], i["ecole_depart"], i["ecole_arrivee"], i["km"], i["gain_ecart"], i["statut"],
                 (i["decision"] or {}).get("motif", ""), " ; ".join(i["raisons"])] for i in d["items"]]
        return "Recommandations de redéploiement", cols, rows
    if table == "dashboard":
        d = dashboard(conn, user, q)
        rows = [["indicateur", k, v] for k, v in d["kpis"].items()]
        rows += [["sous_division", s["nom"], f"départs={s['departs']} ; écart={s['ecart']} ; écoles={s['ecoles']} ; en manque={s['manque']}"] for s in d["par_sous_division"]]
        rows += [["niveau", n["niveau"], f"départs={n['departs']} ; écart={n['ecart']}"] for n in d["par_niveau"]]
        rows += [["série départs", f"{s['horizon']} mois", f"retraite={s['retraite']} ; abandon/absence={s['abandon_absence']}"] for s in d["serie_departs"]]
        return "Tableau de bord", ["type", "libellé", "valeur"], rows
    raise NotFound("table d'export inconnue")


def export_file(conn, user, table, fmt, q):
    from . import exports
    if fmt not in ("xlsx", "csv", "pdf"):
        raise NotFound("format inconnu")
    title, cols, rows = export_table(conn, user, table, q)
    audit.log(conn, user["login"], "export", f"{table}.{fmt} ({len(rows)} lignes)")
    meta = f"{BANNIERE} - généré le {datetime.now().strftime('%d/%m/%Y %H:%M')} - {user['login']} ({user['role']})"
    if fmt == "xlsx":
        return exports.CONTENT_TYPES[fmt], exports.xlsx_bytes(title, cols, rows, meta)
    if fmt == "csv":
        return exports.CONTENT_TYPES[fmt], exports.csv_bytes(cols, rows)
    return exports.CONTENT_TYPES[fmt], exports.pdf_bytes(title, cols, rows, meta)


# ================================================================== administration
def list_users(conn, user):
    require_role(user, "admin")
    return {"items": _rows(conn, "SELECT u.id,u.login,u.role,u.province_id,u.ecole_id,u.actif,p.nom province_nom FROM utilisateur u "
                                 "LEFT JOIN province p ON p.id=u.province_id ORDER BY u.id")}


def create_user(conn, user, body):
    require_role(user, "admin")
    login = str(body.get("login") or "").strip()
    role = body.get("role")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", login):
        raise BadRequest("login invalide (3-40 caractères alphanumériques, _ . -)")
    if role not in ROLES:
        raise BadRequest("rôle invalide")
    pid, eid = _int(body.get("province_id"), "province_id"), _int(body.get("ecole_id"), "ecole_id")
    if role == "gestionnaire" and not (pid and conn.execute("SELECT 1 FROM province WHERE id=?", (pid,)).fetchone()):
        raise BadRequest("province_id valide obligatoire pour un gestionnaire")
    if role == "chef" and not (eid and conn.execute("SELECT 1 FROM ecole WHERE id=?", (eid,)).fetchone()):
        raise BadRequest("ecole_id valide obligatoire pour un chef d'établissement")
    if role not in ("gestionnaire",):
        pid = None
    if role != "chef":
        eid = None
    try:
        with db.tx(conn):
            uid, pwd, sec = auth.create_user(conn, login, role, pid, eid)
            audit.log(conn, user["login"], "utilisateur_cree", f"{login} ({role})")
    except sqlite3.IntegrityError:
        raise Conflict("ce login existe déjà")
    return {"id": uid, "login": login, "role": role, "mot_de_passe_temporaire": pwd, "totp_secret": sec,
            "avertissement": "Ces secrets ne sont affichés qu'une seule fois."}


def patch_user(conn, user, uid, body):
    require_role(user, "admin")
    if "actif" not in body:
        raise BadRequest("champ 'actif' attendu")
    actif = 1 if body["actif"] else 0
    if uid == user["id"] and not actif:
        raise BadRequest("vous ne pouvez pas désactiver votre propre compte")
    with db.tx(conn):
        if conn.execute("UPDATE utilisateur SET actif=? WHERE id=?", (actif, uid)).rowcount == 0:
            raise NotFound("utilisateur inconnu")
        if not actif:
            conn.execute("DELETE FROM session WHERE user_id=?", (uid,))
        audit.log(conn, user["login"], "utilisateur_active" if actif else "utilisateur_desactive", f"id {uid}")
    return {"id": uid, "actif": bool(actif)}


def audit_list(conn, user, q):
    require_role(user, "admin")
    lim = min(_int(q.get("limit"), "limit", 50), 1000)
    off = _int(q.get("offset"), "offset", 0)
    total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    ok, n = audit.verify_chain(conn)
    items = _rows(conn, "SELECT id,utilisateur,action,objet,date,hash FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?", (lim, off))
    return {"total": total, "chain_ok": ok, "chain_verifiees": n, "items": items}


def _backup_dir():
    d = db.DATA_DIR / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def backup(conn, user):
    require_role(user, "admin")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    bd = _backup_dir()
    tmp = bd / f".tmp-{stamp}.db"
    dst = sqlite3.connect(str(tmp))
    try:
        conn.backup(dst)
    finally:
        dst.close()
    blob = auth.encrypt_bytes(tmp.read_bytes())
    tmp.unlink()
    f = bd / f"planedu-{stamp}.db.enc"
    f.write_bytes(b"PLANEDUBK1" + blob)
    f.chmod(0o600)
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    (bd / (f.name + ".sha256")).write_text(sha)
    audit.log(conn, user["login"], "sauvegarde", f"{f.name} sha256={sha[:16]}")
    return {"fichier": f.name, "sha256": sha, "taille": f.stat().st_size, "chiffre": True}


def list_backups(conn, user):
    require_role(user, "admin")
    return {"items": [{"fichier": f.name, "taille": f.stat().st_size, "date": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()}
                      for f in sorted(_backup_dir().glob("planedu-*.db.enc"), reverse=True)]}


def restore(conn, user, body, token):
    require_role(user, "admin")
    name = Path(str(body.get("fichier") or "")).name
    f = _backup_dir() / name
    if not re.fullmatch(r"planedu-[0-9TZ]+\.db\.enc", name) or not f.exists():
        raise NotFound("sauvegarde introuvable")
    raw = f.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    side = _backup_dir() / (name + ".sha256")
    if side.exists() and side.read_text().strip() != sha:
        raise BadRequest("somme de contrôle invalide : sauvegarde altérée")
    if not raw.startswith(b"PLANEDUBK1"):
        raise BadRequest("format de sauvegarde inconnu")
    try:
        plain = auth.decrypt_bytes(raw[10:])
    except ValueError:
        raise BadRequest("déchiffrement impossible (clé différente ou fichier altéré)")
    tmp = _backup_dir() / f".restore-{int(time.time() * 1000)}.db"
    tmp.write_bytes(plain)
    sess = conn.execute("SELECT token,user_id,expire FROM session WHERE token=?", (token,)).fetchone()
    try:
        src = sqlite3.connect(str(tmp))
        try:
            if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BadRequest("sauvegarde corrompue")
            dst = sqlite3.connect(str(db.db_path()), timeout=30)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    finally:
        tmp.unlink(missing_ok=True)
    conn = db.connect()
    if sess:  # conserve la session de l'administrateur courant
        conn.execute("INSERT OR REPLACE INTO session(token,user_id,expire) VALUES(?,?,?)", (sess["token"], sess["user_id"], sess["expire"]))
    integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
    ok, n = audit.verify_chain(conn)
    audit.log(conn, user["login"], "restauration", f"{name} integrite={integ} chain_ok={ok}")
    return {"restaure": True, "fichier": name, "sha256_verifie": True, "integrite": integ, "chain_ok": ok, "entrees_audit": n}


def rbac_test(conn, user):
    """Test négatif d'accès inter-province, exécuté en direct avec des profils simulés."""
    require_role(user, "admin")
    provs = _rows(conn, "SELECT id,nom FROM province ORDER BY id")
    sds = {pr["id"]: conn.execute("SELECT id FROM sous_division WHERE province_id=? LIMIT 1", (pr["id"],)).fetchone()["id"] for pr in provs}
    ecoles = {pr["id"]: conn.execute("SELECT c.id FROM ecole c JOIN sous_division s ON s.id=c.sous_division_id WHERE s.province_id=? LIMIT 1", (pr["id"],)).fetchone()["id"] for pr in provs}
    tests = []

    def attempt(label, fn):
        try:
            fn()
            tests.append({"test": label, "attendu": "403", "obtenu": "accès accordé", "ok": False})
        except Forbidden:
            tests.append({"test": label, "attendu": "403", "obtenu": "403", "ok": True})

    for me in provs:
        for other in provs:
            if me["id"] == other["id"]:
                continue
            g = {"id": 0, "login": f"simul_gest_{me['id']}", "role": "gestionnaire", "province_id": me["id"], "ecole_id": None}
            attempt(f"gestionnaire {me['nom']} -> province {other['nom']}", lambda: previsions(conn, g, {"province_id": other["id"]}))
            attempt(f"gestionnaire {me['nom']} -> sous-division de {other['nom']}", lambda: besoins(conn, g, {"sous_division_id": sds[other["id"]]}))
            attempt(f"gestionnaire {me['nom']} -> alerte école de {other['nom']}", lambda: check_geo(conn, g, ecole_id=ecoles[other["id"]]))
    ch = {"id": 0, "login": "simul_chef", "role": "chef", "province_id": None, "ecole_id": ecoles[provs[0]["id"]]}
    attempt("chef -> école d'une autre province", lambda: check_geo(conn, ch, ecole_id=ecoles[provs[-1]["id"]]))
    dc = {"id": 0, "login": "simul_decideur", "role": "decideur", "province_id": None, "ecole_id": None}
    attempt("décideur -> prévisions individuelles", lambda: previsions(conn, dc, {}))
    attempt("décideur -> recommandations individuelles", lambda: recommandations(conn, dc, {}))
    ok = all(t["ok"] for t in tests)
    audit.log(conn, user["login"], "rbac_test", f"{'réussi' if ok else 'ECHEC'} ({len(tests)} cas)")
    return {"ok": ok, "tests": tests}
