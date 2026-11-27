"""PLANEDU-IA - modeles et calculs purs (numpy + stdlib, aucun acces DB).

Donnees de demonstration uniquement. Tout est deterministe (aucun alea).
Les signatures suivent docs/CONTRACT.md section 1.
"""
from __future__ import annotations

import calendar
import math
from datetime import date, timedelta
from fractions import Fraction

import numpy as np

# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------


def _to_date(v) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _add_months(d: date, n: int) -> date:
    """d + n mois calendaires, jour ramene a la fin du mois si necessaire."""
    idx = d.year * 12 + (d.month - 1) + n
    y, m = divmod(idx, 12)
    m += 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def retirement_date(birth: date, legal_age: int) -> date:
    """Date anniversaire des `legal_age` ans ; 29 fevrier -> 28 fevrier si l'annee cible n'est pas bissextile."""
    birth = _to_date(birth)
    y = birth.year + int(legal_age)
    if birth.month == 2 and birth.day == 29 and not calendar.isleap(y):
        return date(y, 2, 28)
    return date(y, birth.month, birth.day)


def months_between(d0: date, d1: date) -> float:
    """Mois calendaires entiers + fraction de mois ; negatif si d1 < d0."""
    d0, d1 = _to_date(d0), _to_date(d1)
    if d1 < d0:
        return -months_between(d1, d0)
    n = (d1.year - d0.year) * 12 + (d1.month - d0.month)
    if _add_months(d0, n) > d1:
        n -= 1
    a, b = _add_months(d0, n), _add_months(d0, n + 1)
    return n + (d1 - a).days / max(1, (b - a).days)


def retirement_flags(teachers: list[dict], legal_age: int, today: date, horizons=(6, 12, 24)) -> list[dict]:
    """[{teacher_id, retirement_date, months_to_retirement, h6, h12, h24, deja_retraite, raison}].

    h{n} vrai si la retraite tombe au plus tard dans n mois (retraite deja atteinte incluse).
    """
    today = _to_date(today)
    out = []
    for t in teachers:
        rd = retirement_date(_to_date(t["birth_date"]), legal_age)
        m = months_between(today, rd)
        row = {
            "teacher_id": t["id"],
            "retirement_date": rd,
            "months_to_retirement": round(m, 2),
            "deja_retraite": rd <= today,
        }
        for h in horizons:
            row[f"h{h}"] = rd <= _add_months(today, h)
        if rd <= today:
            row["raison"] = f"Age legal de {legal_age} ans atteint le {rd.isoformat()}"
        else:
            row["raison"] = f"Retraite le {rd.isoformat()} (age legal {legal_age} ans), dans {m:.1f} mois"
        out.append(row)
    return out


# --------------------------------------------------------------------------
# Attrition (abandon / absence prolongee)
# --------------------------------------------------------------------------

FEATURES = ["age", "seniority_years", "level", "subject_scarcity", "posting_remote"]
ATTRITION_MOTIFS = ("abandon", "absence_prolongee")


def _level_value(level) -> float:
    if isinstance(level, (int, float)):
        return float(level)
    return 1.0 if str(level).strip().lower().startswith("sec") else 0.0


def _matrix(rows: list[dict]) -> np.ndarray:
    return np.array(
        [
            [
                float(r["age"]),
                float(r["seniority_years"]),
                _level_value(r.get("level", "primaire")),
                float(r.get("subject_scarcity", 0.0)),
                float(r.get("posting_remote", 0.0)),
            ]
            for r in rows
        ],
        dtype=float,
    ).reshape(len(rows), len(FEATURES))


def _sigmoid(z):
    return 0.5 * (1.0 + np.tanh(0.5 * z))  # stable


class AttritionModel:
    """Regression logistique L2, features standardisees, descente de gradient (numpy seul)."""

    version = "logreg-l2-v1"

    def __init__(self, l2: float = 0.01, lr: float = 0.5, n_iter: int = 800):
        self.l2, self.lr, self.n_iter = l2, lr, n_iter
        self.mu = np.zeros(len(FEATURES))
        self.sd = np.ones(len(FEATURES))
        self.w = np.zeros(len(FEATURES))
        self.b = 0.0
        self.horizon_months = None
        self.fitted = False

    def fit(self, rows: list[dict], horizon_months: int):
        self.horizon_months = horizon_months
        n = len(rows)
        if n == 0:
            self.fitted = True
            return self
        X = _matrix(rows)
        y = np.array([1.0 if r["left"] else 0.0 for r in rows])
        self.mu = X.mean(axis=0)
        sd = X.std(axis=0)
        self.sd = np.where(sd < 1e-12, 1.0, sd)
        Z = (X - self.mu) / self.sd
        p0 = min(max(y.mean(), 1e-6), 1 - 1e-6)
        w = np.zeros(Z.shape[1])
        b = math.log(p0 / (1 - p0))
        for _ in range(self.n_iter):
            p = _sigmoid(Z @ w + b)
            err = p - y
            w -= self.lr * (Z.T @ err / n + self.l2 * w)
            b -= self.lr * err.mean()
        self.w, self.b, self.fitted = w, float(b), True
        return self

    def predict_proba(self, rows) -> np.ndarray:
        if not rows:
            return np.zeros(0)
        Z = (_matrix(list(rows)) - self.mu) / self.sd
        return _sigmoid(Z @ self.w + self.b)

    def explain(self, row) -> list[tuple[str, float]]:
        """Facteurs lisibles (FR) tries par contribution absolue decroissante.

        Contribution = coefficient x valeur standardisee (log-odds) ; > 0 augmente le risque.
        """
        z = (_matrix([row])[0] - self.mu) / self.sd
        contrib = self.w * z
        niveau = str(row.get("level", "primaire"))
        labels = {
            "age": f"Age : {float(row['age']):.0f} ans",
            "seniority_years": f"Anciennete : {float(row['seniority_years']):.0f} ans",
            "level": f"Niveau d'enseignement : {niveau}",
            "subject_scarcity": f"Rarete de la matiere (indice {float(row.get('subject_scarcity', 0)):.2f})",
            "posting_remote": f"Eloignement de l'affectation (indice {float(row.get('posting_remote', 0)):.2f})",
        }
        out = []
        for i, f in enumerate(FEATURES):
            c = float(contrib[i])
            sens = "augmente le risque" if c > 0 else "reduit le risque"
            out.append((f"{labels[f]} - {sens}", c))
        out.sort(key=lambda t: (-abs(t[1]), t[0]))
        return out


def _teacher_id(t):
    return t["id"] if "id" in t else t["teacher_id"]


def _departure_index(departures):
    idx = {}
    for d in departures:
        tid = d.get("teacher_id", d.get("enseignant_id"))
        dt = _to_date(d["date"])
        if tid not in idx or dt < idx[tid][0]:
            idx[tid] = (dt, d.get("motif", "abandon"))
    return idx


def _entry(t):
    for k in ("date_entree", "hire_date", "entry_date"):
        if t.get(k):
            return _to_date(t[k])
    return None


def build_training_rows(teachers, departures, today, horizon) -> list[dict]:
    """Instantane a `today` : enseignants en poste ; left = abandon/absence dans (today, today+horizon].

    Les retraites/deces dans la fenetre sont censures (ligne exclue) : la retraite est deterministe (EF-03).
    """
    today = _to_date(today)
    end = _add_months(today, horizon)
    dep = _departure_index(departures)
    rows = []
    for t in teachers:
        tid = _teacher_id(t)
        entry = _entry(t)
        if entry is not None and entry > today:
            continue
        d = dep.get(tid)
        left = False
        if d is not None:
            if d[0] <= today:
                continue
            if d[0] <= end:
                if d[1] in ATTRITION_MOTIFS:
                    left = True
                else:
                    continue
        birth = _to_date(t["birth_date"])
        rows.append(
            {
                "teacher_id": tid,
                "age": (today - birth).days / 365.25,
                "seniority_years": max(0.0, (today - entry).days / 365.25) if entry else float(t.get("seniority_years", 0.0)),
                "level": t.get("niveau", t.get("level", "primaire")),
                "subject_scarcity": float(t.get("subject_scarcity", 0.0)),
                "posting_remote": float(t.get("posting_remote", 0.0)),
                "left": left,
            }
        )
    return rows


def _auc(y: np.ndarray, s: np.ndarray):
    """AUC de Mann-Whitney par rangs moyens (gere les ex aequo). None si une seule classe."""
    y = np.asarray(y).astype(bool)
    s = np.asarray(s, dtype=float)
    npos, nneg = int(y.sum()), int((~y).sum())
    if npos == 0 or nneg == 0:
        return None
    _, inv, cnt = np.unique(s, return_inverse=True, return_counts=True)
    upper = np.cumsum(cnt)
    avg_rank = upper - (cnt - 1) / 2.0  # rang moyen (1-base) du groupe
    ranks = avg_rank[inv]
    return float((ranks[y].sum() - npos * (npos + 1) / 2.0) / (npos * nneg))


def _topk(y: np.ndarray, s: np.ndarray, k: int):
    """(precision@k, recall@k) ; les ex aequo sont traites en esperance (pas d'ordre arbitraire favorable)."""
    y = np.asarray(y).astype(bool)
    npos = int(y.sum())
    vals = np.unique(s)[::-1]
    remaining, tp = k, 0.0
    for v in vals:
        if remaining <= 0:
            break
        m = s == v
        size = int(m.sum())
        take = min(remaining, size)
        tp += take * float(y[m].sum()) / size
        remaining -= take
    return tp / k, (tp / npos if npos else None)


def backtest(teachers, departures, assignments_info, cutoff: date, horizons=(6, 12, 24)) -> dict:
    """Retro-test honnete : entrainement sur departs <= cutoff, evaluation sur (cutoff, cutoff+h].

    Entrainement : plusieurs instantanes (cutoff-h-6k mois) dont les fenetres d'observation se terminent <= cutoff
    (aucune information posterieure au cutoff). Test : instantane a `cutoff`.
    Baseline = taux de base par niveau (prediction constante par niveau).
    assignments_info : {teacher_id: {subject_scarcity, posting_remote}} fusionne dans les enseignants.
    """
    cutoff = _to_date(cutoff)
    info = assignments_info or {}
    tt = []
    for t in teachers:
        t2 = dict(t)
        t2.update(info.get(_teacher_id(t), {}))
        tt.append(t2)
    known = [d for d in departures if _to_date(d["date"]) <= cutoff]
    result = {}
    for h in horizons:
        train = []
        for k in range(4):
            train += build_training_rows(tt, known, _add_months(cutoff, -h - 6 * k), h)
        test = build_training_rows(tt, departures, cutoff, h)
        n = len(test)
        entry = {"n": n, "n_train": len(train), "auc": None, "precision_at_k": None, "recall_at_k": None,
                 "baseline_auc": None, "baseline_precision_at_k": None, "baseline_recall_at_k": None,
                 "n_pos": sum(1 for r in test if r["left"]), "k": 0}
        if n == 0 or not train:
            entry["lecture"] = "Donnees insuffisantes pour ce horizon."
            result[h] = entry
            continue
        y = np.array([r["left"] for r in test], dtype=bool)
        model = AttritionModel().fit(train, h)
        s = model.predict_proba(test)
        ytr = np.array([r["left"] for r in train], dtype=float)
        glob = float(ytr.mean())
        rate = {}
        for lv in {str(r["level"]) for r in train}:
            vals = [r["left"] for r in train if str(r["level"]) == lv]
            rate[lv] = float(np.mean(vals))
        sb = np.array([rate.get(str(r["level"]), glob) for r in test])
        k = max(1, math.ceil(0.10 * n))
        entry.update(k=k, auc=_auc(y, s), baseline_auc=_auc(y, sb))
        entry["precision_at_k"], entry["recall_at_k"] = _topk(y, s, k)
        entry["baseline_precision_at_k"], entry["baseline_recall_at_k"] = _topk(y, sb, k)
        a, b = entry["auc"], entry["baseline_auc"]
        if a is not None and b is not None:
            verdict = "meilleur" if a > b else "pas meilleur"
            entry["lecture"] = (
                f"Horizon {h} mois : le modele est {verdict} que le taux de base par niveau "
                f"(AUC {a:.2f} contre {b:.2f}, {entry['n_pos']} departs sur {n} enseignants)."
            )
        else:
            entry["lecture"] = f"Horizon {h} mois : pas assez de departs pour evaluer ({entry['n_pos']} sur {n})."
        result[h] = entry
    return result


# --------------------------------------------------------------------------
# Besoins, ecarts, redeploiements
# --------------------------------------------------------------------------


def _year_of(v) -> int:
    if isinstance(v, date):
        return v.year
    if isinstance(v, (int, float)):
        return int(v)
    return int(str(v)[:4])


def _ceil_div(n, ratio) -> int:
    r = Fraction(str(ratio))
    if r <= 0:
        raise ValueError("eleves_par_enseignant doit etre > 0")
    return math.ceil(Fraction(str(n)) / r)


def compute_need(school_enrollment: dict, norms: list[dict]) -> list[dict]:
    """requis = ceil(nombre / eleves_par_enseignant), norme la plus recente dont en_vigueur_depuis <= annee.

    Un niveau sans norme applicable ne produit aucune ligne.
    """
    sid = school_enrollment["school_id"]
    year = _year_of(school_enrollment["year"])
    out = []
    for niveau, nombre in school_enrollment["levels"].items():
        best = {}
        for nm in norms:
            if nm["niveau"] != niveau or _year_of(nm["en_vigueur_depuis"]) > year:
                continue
            cur = best.get(nm["matiere"])
            if cur is None or _year_of(nm["en_vigueur_depuis"]) >= _year_of(cur["en_vigueur_depuis"]):
                best[nm["matiere"]] = nm
        for matiere in sorted(best, key=str):
            nm = best[matiere]
            req = _ceil_div(nombre, nm["eleves_par_enseignant"])
            out.append(
                {
                    "school_id": sid,
                    "niveau": niveau,
                    "matiere": matiere,
                    "requis": req,
                    "reasons": [
                        f"{nombre} eleves / {nm['eleves_par_enseignant']} eleves par enseignant "
                        f"(norme en vigueur depuis {_year_of(nm['en_vigueur_depuis'])}) = {req} enseignant(s) requis"
                    ],
                }
            )
    return out


def _count(rows):
    c = {}
    for r in rows or []:
        k = (r["school_id"], r["niveau"], r["matiere"])
        c[k] = c.get(k, 0) + int(r.get("nombre", r.get("count", 1)))
    return c


def gap(required: list[dict], staff: list[dict], leaving: list[dict]) -> list[dict]:
    """ecart = requis - (actuel - partants) par (school_id, niveau, matiere) ; ecart > 0 = manque.

    staff / leaving : une ligne par enseignant (ou avec 'nombre'), cles school_id, niveau, matiere.
    """
    req = {}
    for r in required:
        k = (r["school_id"], r["niveau"], r["matiere"])
        req[k] = req.get(k, 0) + int(r["requis"])
    cs, cl = _count(staff), _count(leaving)
    out = []
    for k in sorted(set(req) | set(cs) | set(cl), key=lambda t: tuple(map(str, t))):
        requis, actuel, part = req.get(k, 0), cs.get(k, 0), cl.get(k, 0)
        prevu = actuel - part
        ecart = requis - prevu
        if ecart > 0:
            why = f"Manque de {ecart} enseignant(s) : {requis} requis, {prevu} prevus ({actuel} en poste - {part} depart(s))"
        elif ecart < 0:
            why = f"Surplus de {-ecart} enseignant(s) : {requis} requis, {prevu} prevus ({actuel} en poste - {part} depart(s))"
        else:
            why = f"Effectif prevu conforme a la norme ({requis} requis)"
        out.append({"school_id": k[0], "niveau": k[1], "matiere": k[2], "requis": requis,
                    "actuel": actuel, "prevu": prevu, "ecart": ecart, "reasons": [why]})
    return out


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


def recommend_redeployments(gaps, teachers, distance_fn, max_km=150, top_per_surplus=3, today: date | None = None) -> list[dict]:
    """Affectation gloutonne surplus -> manque (meme niveau et meme matiere).

    Contraintes : distance <= max_km, matiere, niveau, un enseignant au plus une fois, au plus
    min(surplus, top_per_surplus) enseignants par ecole en surplus, jamais plus que le manque de la cible.
    score = gain_ecart*10 - km/20 + bonus_anciennete (min(1, anciennete/20)).
    teachers : {id, ecole_id|school_id, niveau, matiere, seniority_years|date_entree (avec today)}.
    """
    need, surplus = {}, {}
    for g in gaps:
        k = (g["school_id"], g["niveau"], g["matiere"])
        if g["ecart"] > 0:
            need[k] = g["ecart"]
        elif g["ecart"] < 0:
            surplus[k] = -g["ecart"]
    cap = {k: min(v, top_per_surplus) for k, v in surplus.items()}

    def seniority(t):
        if t.get("seniority_years") is not None:
            return float(t["seniority_years"])
        if today is not None and t.get("date_entree"):
            return max(0.0, (_to_date(today) - _to_date(t["date_entree"])).days / 365.25)
        return 0.0

    cands = []
    for t in teachers:
        src = t.get("ecole_id", t.get("school_id"))
        ks = (src, t["niveau"], t["matiere"])
        if ks not in surplus:
            continue
        sen = seniority(t)
        for kt in need:
            if kt[1] != t["niveau"] or kt[2] != t["matiere"] or kt[0] == src:
                continue
            km = distance_fn(src, kt[0])
            if km is None or (isinstance(km, float) and math.isnan(km)) or km > max_km:
                continue
            gain = 1
            score = gain * 10 - km / 20.0 + min(1.0, sen / 20.0)
            cands.append((-score, str(_teacher_id(t)), str(kt[0]), t, ks, kt, km, gain, score, sen))
    cands.sort(key=lambda c: c[:3])
    used, out = set(), []
    for _, _, _, t, ks, kt, km, gain, score, sen in cands:
        tid = _teacher_id(t)
        if tid in used or cap[ks] <= 0 or need[kt] <= 0 or surplus[ks] <= 0:
            continue
        reasons = [
            f"Surplus de {surplus[ks]} enseignant(s) en {t['matiere']} ({t['niveau']}) a l'ecole de depart",
            f"Manque de {need[kt]} enseignant(s) en {t['matiere']} ({t['niveau']}) a l'ecole d'arrivee",
            f"Meme matiere ({t['matiere']}) et meme niveau ({t['niveau']})",
            f"Distance {km:.0f} km (limite {max_km} km)",
        ]
        if sen > 0:
            reasons.append(f"Anciennete de {sen:.0f} an(s) (bonus d'experience pour l'ecole d'arrivee)")
        used.add(tid)
        cap[ks] -= 1
        surplus[ks] -= 1
        need[kt] -= 1
        out.append({"teacher_id": tid, "from_school_id": ks[0], "to_school_id": kt[0], "niveau": t["niveau"],
                    "matiere": t["matiere"], "gain_ecart": gain, "km": round(float(km), 1),
                    "score": round(score, 3), "rank": len(out) + 1, "reasons": reasons})
    return out
