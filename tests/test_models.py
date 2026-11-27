import calendar
import math
import random
import unittest
from datetime import date, timedelta

import numpy as np

from planedu import models as M


def manual_retirement(birth, age):
    """Calcul manuel independant : annee cible, 29 fev -> 28 fev si non bissextile."""
    ty = birth.year + age
    day = birth.day
    if birth.month == 2 and day == 29 and not calendar.isleap(ty):
        day = 28
    return date(ty, birth.month, day)


def age_on(birth, d):
    """Age revolu a la date d (calcul independant)."""
    return d.year - birth.year - ((d.month, d.day) < (birth.month, birth.day))


class TestRetirement(unittest.TestCase):
    def test_ef03_100_teachers_exact(self):
        rnd = random.Random(7)
        births = [date(1960, 2, 29), date(1964, 2, 29), date(1990, 12, 31), date(1961, 1, 1)]
        while len(births) < 100:
            births.append(date(1958, 1, 1) + timedelta(days=rnd.randrange(0, 365 * 38)))
        teachers = [{"id": f"T-{i:08d}", "birth_date": b} for i, b in enumerate(births)]
        today = date(2026, 6, 15)
        for legal in (65, 64):
            flags = M.retirement_flags(teachers, legal, today)
            self.assertEqual(len(flags), 100)
            exact = 0
            for t, f in zip(teachers, flags):
                exp = manual_retirement(t["birth_date"], legal)
                if f["retirement_date"] == exp:
                    exact += 1
                # propriete independante : a la date, l'age est exactement legal ; la veille, legal-1 (hors 29 fev)
                if not (t["birth_date"].month == 2 and t["birth_date"].day == 29):
                    self.assertEqual(age_on(t["birth_date"], exp), legal)
                    self.assertEqual(age_on(t["birth_date"], exp - timedelta(days=1)), legal - 1)
                self.assertEqual(f["h12"], exp <= date(2027, 6, 15))
                self.assertEqual(f["h6"], exp <= date(2026, 12, 15))
                self.assertEqual(f["h24"], exp <= date(2028, 6, 15))
                self.assertEqual(f["teacher_id"], t["id"])
            self.assertEqual(exact, 100)

    def test_leap_day(self):
        self.assertEqual(M.retirement_date(date(1960, 2, 29), 65), date(2025, 2, 28))
        self.assertEqual(M.retirement_date(date(1960, 2, 29), 64), date(2024, 2, 29))
        self.assertEqual(M.retirement_date(date(1980, 3, 1), 65), date(2045, 3, 1))

    def test_months_between(self):
        self.assertEqual(M.months_between(date(2026, 1, 15), date(2026, 7, 15)), 6.0)
        self.assertAlmostEqual(M.months_between(date(2026, 1, 31), date(2026, 3, 1)), 1 + 1 / 31, places=6)
        self.assertEqual(M.months_between(date(2026, 7, 15), date(2026, 1, 15)), -6.0)
        self.assertEqual(M.months_between(date(2026, 1, 1), date(2026, 1, 1)), 0.0)


class TestNeedGap(unittest.TestCase):
    def test_ef05_10_schools_exact(self):
        norms = [
            {"niveau": "primaire", "matiere": "toutes", "eleves_par_enseignant": 50, "en_vigueur_depuis": 2015},
            {"niveau": "primaire", "matiere": "toutes", "eleves_par_enseignant": 45, "en_vigueur_depuis": 2022},
            {"niveau": "primaire", "matiere": "toutes", "eleves_par_enseignant": 40, "en_vigueur_depuis": 2030},  # futur
            {"niveau": "secondaire", "matiere": "maths", "eleves_par_enseignant": 35, "en_vigueur_depuis": "2020-01-01"},
            {"niveau": "secondaire", "matiere": "francais", "eleves_par_enseignant": 35, "en_vigueur_depuis": 2020},
        ]
        # (ecole, annee, primaire, secondaire) -> attendu (primaire, secondaire par matiere) calcule a la main
        cases = [
            ("E1", 2025, 450, 0, 10, 0),     # 450/45 = 10 pile
            ("E2", 2025, 451, 35, 11, 1),    # 451/45 -> 11 ; 35/35 = 1
            ("E3", 2025, 1, 36, 1, 2),
            ("E4", 2025, 0, 70, 0, 2),
            ("E5", 2025, 89, 105, 2, 3),
            ("E6", 2021, 100, 0, 2, 0),      # norme 2015 (50) : 100/50 = 2
            ("E7", 2021, 101, 0, 3, 0),      # 101/50 -> 3
            ("E8", 2025, 900, 350, 20, 10),
            ("E9", 2025, 46, 34, 2, 1),
            ("E10", 2025, 225, 71, 5, 3),
        ]
        for sid, yr, prim, sec, exp_p, exp_s in cases:
            levels = {"primaire": prim}
            if sec:
                levels["secondaire"] = sec
            res = M.compute_need({"school_id": sid, "year": yr, "levels": levels}, norms)
            by = {(r["niveau"], r["matiere"]): r["requis"] for r in res}
            self.assertEqual(by[("primaire", "toutes")], exp_p, sid)
            if sec:
                self.assertEqual(by[("secondaire", "maths")], exp_s, sid)
                self.assertEqual(by[("secondaire", "francais")], exp_s, sid)
            for r in res:
                self.assertEqual(r["school_id"], sid)
                self.assertTrue(r["reasons"] and isinstance(r["reasons"][0], str))

    def test_gap(self):
        req = [{"school_id": "A", "niveau": "primaire", "matiere": "toutes", "requis": 5},
               {"school_id": "B", "niveau": "primaire", "matiere": "toutes", "requis": 2}]
        staff = [{"school_id": "A", "niveau": "primaire", "matiere": "toutes"}] * 4 + \
                [{"school_id": "B", "niveau": "primaire", "matiere": "toutes"}] * 4 + \
                [{"school_id": "C", "niveau": "primaire", "matiere": "toutes"}]
        leaving = [{"school_id": "A", "niveau": "primaire", "matiere": "toutes"}]
        g = {r["school_id"]: r for r in M.gap(req, staff, leaving)}
        self.assertEqual((g["A"]["actuel"], g["A"]["prevu"], g["A"]["ecart"]), (4, 3, 2))
        self.assertEqual((g["B"]["prevu"], g["B"]["ecart"]), (4, -2))
        self.assertEqual((g["C"]["requis"], g["C"]["ecart"]), (0, -1))
        self.assertIn("Manque", g["A"]["reasons"][0])


class TestAttrition(unittest.TestCase):
    @staticmethod
    def synthetic(seed, signal=True, n=4000):
        rnd = np.random.default_rng(seed)
        start = date(2019, 1, 1)
        teachers, deps, info = [], [], {}
        for i in range(n):
            age = rnd.uniform(25, 62)
            sen = rnd.uniform(0, min(30, age - 22))
            birth = start - timedelta(days=int(age * 365.25))
            entry = start - timedelta(days=int(sen * 365.25))
            scar = float(rnd.uniform(0, 1))
            rem = float(rnd.uniform(0, 1))
            lvl = "primaire" if rnd.random() < 0.6 else "secondaire"
            tid = f"T-{i:05d}"
            teachers.append({"id": tid, "birth_date": birth, "date_entree": entry, "niveau": lvl})
            info[tid] = {"subject_scarcity": scar, "posting_remote": rem}
            if signal:
                lin = -2.9 + 2.2 * rem + 1.6 * scar - 0.06 * (age - 40)
            else:
                lin = -2.0
            rate = math.exp(lin)  # departs / an
            t = rnd.exponential(1 / rate)
            if t < 6.0:
                deps.append({"teacher_id": tid, "date": start + timedelta(days=int(t * 365.25)), "motif": "abandon"})
        return teachers, deps, info

    def test_auc_function(self):
        y = np.array([1, 0, 1, 0, 0])
        self.assertAlmostEqual(M._auc(y, np.array([0.9, 0.1, 0.8, 0.3, 0.2])), 1.0)
        self.assertAlmostEqual(M._auc(y, np.array([0.5] * 5)), 0.5)
        # verification par comptage de paires
        s = np.array([0.7, 0.4, 0.4, 0.4, 0.9])
        pos, neg = s[y == 1], s[y == 0]
        exp = np.mean([(p > q) + 0.5 * (p == q) for p in pos for q in neg])
        self.assertAlmostEqual(M._auc(y, s), float(exp))
        self.assertIsNone(M._auc(np.zeros(3), np.arange(3.0)))

    def test_backtest_beats_baseline_with_signal(self):
        teachers, deps, info = self.synthetic(1, signal=True)
        cutoff = date(2022, 1, 1)
        bt = M.backtest(teachers, deps, info, cutoff, horizons=(6, 12, 24))
        self.assertEqual(set(bt), {6, 12, 24})
        for h, r in bt.items():
            for key in ("auc", "precision_at_k", "recall_at_k", "baseline_auc",
                        "baseline_precision_at_k", "n_pos", "n"):
                self.assertIn(key, r)
            self.assertGreater(r["n_pos"], 20)
            self.assertGreater(r["auc"], r["baseline_auc"] + 0.08, (h, r))
            self.assertGreater(r["auc"], 0.62)
            self.assertGreater(r["precision_at_k"], r["baseline_precision_at_k"])
            self.assertIn("meilleur", r["lecture"])

    def test_backtest_is_honest_on_noise(self):
        teachers, deps, info = self.synthetic(2, signal=False)
        bt = M.backtest(teachers, deps, info, date(2022, 1, 1), horizons=(12,))
        self.assertLess(bt[12]["auc"], 0.62)
        self.assertGreater(bt[12]["auc"], 0.38)

    def test_backtest_no_leakage_and_deterministic(self):
        teachers, deps, info = self.synthetic(3, signal=True, n=1500)
        cutoff = date(2022, 1, 1)
        a = M.backtest(teachers, deps, info, cutoff, horizons=(12,))
        b = M.backtest(teachers, deps, info, cutoff, horizons=(12,))
        self.assertEqual(a, b)
        # les departs posterieurs au cutoff ne servent qu'a l'evaluation : modifier leurs motifs/dates
        # ne change pas le nombre de lignes d'entrainement.
        known = [d for d in deps if d["date"] <= cutoff]
        self.assertEqual(a[12]["n_train"], M.backtest(teachers, known, info, cutoff, (12,))[12]["n_train"])

    def test_model_explain_and_proba(self):
        teachers, deps, info = self.synthetic(4, signal=True, n=3000)
        tt = [dict(t, **info[t["id"]]) for t in teachers]
        rows = M.build_training_rows(tt, deps, date(2021, 1, 1), 12)
        m = M.AttritionModel().fit(rows, 12)
        p = m.predict_proba(rows)
        self.assertEqual(p.shape, (len(rows),))
        self.assertTrue(np.all((p > 0) & (p < 1)))
        hi = {"age": 30, "seniority_years": 3, "level": "primaire", "subject_scarcity": 0.95, "posting_remote": 0.95}
        lo = dict(hi, subject_scarcity=0.05, posting_remote=0.05, age=55)
        self.assertGreater(m.predict_proba([hi])[0], m.predict_proba([lo])[0])
        ex = m.explain(hi)
        self.assertEqual(len(ex), 5)
        self.assertTrue(all(isinstance(l, str) and isinstance(c, float) for l, c in ex))
        self.assertEqual([abs(c) for _, c in ex], sorted([abs(c) for _, c in ex], reverse=True))
        self.assertIn("augmente le risque", ex[0][0])


class TestRedeploy(unittest.TestCase):
    def setUp(self):
        self.coords = {"S1": (-5.0, 15.0), "S2": (-5.1, 15.1), "M1": (-5.2, 15.2), "M2": (-8.0, 18.0),
                       "FAR": (4.0, 30.0)}
        self.dist = lambda a, b: M.haversine_km(*self.coords[a], *self.coords[b])
        P = "primaire"
        self.gaps = [
            {"school_id": "S1", "niveau": P, "matiere": "toutes", "requis": 2, "actuel": 6, "prevu": 5, "ecart": -3},
            {"school_id": "S2", "niveau": "secondaire", "matiere": "maths", "requis": 1, "actuel": 3, "prevu": 3, "ecart": -2},
            {"school_id": "M1", "niveau": P, "matiere": "toutes", "requis": 5, "actuel": 3, "prevu": 3, "ecart": 2},
            {"school_id": "M2", "niveau": P, "matiere": "toutes", "requis": 4, "actuel": 3, "prevu": 3, "ecart": 1},
            {"school_id": "FAR", "niveau": P, "matiere": "toutes", "requis": 9, "actuel": 0, "prevu": 0, "ecart": 9},
            {"school_id": "M1", "niveau": "secondaire", "matiere": "francais", "requis": 2, "actuel": 0, "prevu": 0, "ecart": 2},
        ]
        self.teachers = [
            {"id": f"P{i}", "ecole_id": "S1", "niveau": P, "matiere": "toutes", "seniority_years": i * 3}
            for i in range(6)
        ] + [
            {"id": f"X{i}", "ecole_id": "S2", "niveau": "secondaire", "matiere": "maths", "seniority_years": 4}
            for i in range(3)
        ]

    def test_haversine(self):
        self.assertAlmostEqual(M.haversine_km(0, 0, 0, 1), 111.195, delta=0.2)
        self.assertEqual(M.haversine_km(-5, 15, -5, 15), 0.0)

    def test_constraints(self):
        recs = M.recommend_redeployments(self.gaps, self.teachers, self.dist, max_km=150, top_per_surplus=3)
        self.assertGreater(len(recs), 0)
        tmap = {t["id"]: t for t in self.teachers}
        ids = [r["teacher_id"] for r in recs]
        self.assertEqual(len(ids), len(set(ids)), "un enseignant au plus une fois")
        self.assertEqual([r["rank"] for r in recs], list(range(1, len(recs) + 1)))
        need = {(g["school_id"], g["niveau"], g["matiere"]): g["ecart"] for g in self.gaps}
        used_to, used_from = {}, {}
        for r in recs:
            t = tmap[r["teacher_id"]]
            self.assertEqual(r["from_school_id"], t["ecole_id"])
            self.assertEqual(r["niveau"], t["niveau"])
            self.assertEqual(r["matiere"], t["matiere"])
            self.assertLessEqual(r["km"], 150)
            self.assertLessEqual(self.dist(r["from_school_id"], r["to_school_id"]), 150.05)
            self.assertNotEqual(r["to_school_id"], "FAR")
            self.assertGreater(need[(r["to_school_id"], r["niveau"], r["matiere"])], 0)
            self.assertLess(need[(r["from_school_id"], r["niveau"], r["matiere"])], 0)
            self.assertTrue(r["reasons"] and all(isinstance(x, str) for x in r["reasons"]))
            k = (r["to_school_id"], r["niveau"], r["matiere"])
            used_to[k] = used_to.get(k, 0) + 1
            k = (r["from_school_id"], r["niveau"], r["matiere"])
            used_from[k] = used_from.get(k, 0) + 1
        for k, n in used_to.items():
            self.assertLessEqual(n, need[k])
        for k, n in used_from.items():
            self.assertLessEqual(n, -need[k])
            self.assertLessEqual(n, 3)
        # M2 est a ~370 km : jamais cible avec max_km=150 ; M1 (proche) est servi en priorite
        self.assertEqual(recs[0]["to_school_id"], "M1")
        # secondaire/maths : pas de manque correspondant -> aucune reco pour X*
        self.assertFalse(any(i.startswith("X") for i in ids))
        # M1 primaire manque 2 : pas plus de 2 arrivees
        self.assertEqual(used_to[("M1", "primaire", "toutes")], 2)

    def test_top_per_surplus_and_distance_widening(self):
        recs = M.recommend_redeployments(self.gaps, self.teachers, self.dist, max_km=1000, top_per_surplus=2)
        n_from_s1 = sum(1 for r in recs if r["from_school_id"] == "S1")
        self.assertEqual(n_from_s1, 2)
        recs0 = M.recommend_redeployments(self.gaps, self.teachers, self.dist, max_km=1, top_per_surplus=3)
        self.assertEqual(recs0, [])

    def test_seniority_bonus_and_determinism(self):
        a = M.recommend_redeployments(self.gaps, self.teachers, self.dist)
        b = M.recommend_redeployments(self.gaps, list(reversed(self.teachers)), self.dist)
        self.assertEqual(a, b)
        # a score egal en distance, le plus ancien passe d'abord
        self.assertEqual(a[0]["teacher_id"], "P5")


if __name__ == "__main__":
    unittest.main()
