"""Tests d'intégration de l'API PLANEDU-IA : serveur réel en thread (port éphémère), base temporaire."""
import base64
import io
import json
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from planedu import auth, db, server

TMP = None
SRV = None
BASE = None
CREDS = {}


def setUpModule():
    global TMP, SRV, BASE
    TMP = tempfile.mkdtemp(prefix="planedu-test-")
    db.close_all()
    db.configure(TMP)
    t0 = time.time()
    server.ensure_data(verbose=False)
    print(f"\n[tests] seed + calcul initial : {time.time() - t0:.1f} s")
    SRV = server.make_server("127.0.0.1", 0)
    BASE = f"http://127.0.0.1:{SRV.server_address[1]}"
    threading.Thread(target=SRV.serve_forever, daemon=True).start()
    for line in (Path(TMP) / "DEMO_CREDENTIALS.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or ";" not in line:
            continue
        login, pwd, sec, *_ = [x.strip() for x in line.split(";")]
        CREDS[login] = (pwd, None if sec == "-" else sec)


def tearDownModule():
    SRV.shutdown()
    SRV.server_close()
    db.close_all()
    shutil.rmtree(TMP, ignore_errors=True)


def call(method, path, body=None, token=None, raw_body=None, headers=None):
    data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def api(method, path, body=None, token=None):
    st, raw, _ = call(method, path, body, token)
    try:
        return st, json.loads(raw)
    except ValueError:
        return st, raw


_tokens = {}


def login(who):
    if who in _tokens:
        return _tokens[who]
    pwd, sec = CREDS[who]
    b = {"login": who, "password": pwd}
    if sec:
        b["otp"] = auth.totp_code(sec)
    st, r = api("POST", "/api/login", b)
    assert st == 200, (who, st, r)
    _tokens[who] = r["token"]
    return r["token"]


def csv_b64(text):
    return base64.b64encode(text.encode("utf-8")).decode()


class TestAuth(unittest.TestCase):
    def test_login_otp_obligatoire(self):
        pwd, sec = CREDS["admin"]
        st, r = api("POST", "/api/login", {"login": "admin", "password": pwd})
        self.assertEqual(st, 401)
        self.assertTrue(r.get("otp_required"))
        self.assertEqual(r["error"], "otp_required")
        st, r = api("POST", "/api/login", {"login": "admin", "password": pwd, "otp": "000000"})
        self.assertEqual(st, 401)
        st, r = api("POST", "/api/login", {"login": "admin", "password": "mauvais"})
        self.assertEqual(st, 401)
        self.assertNotIn("otp_required", r)
        st, r = api("POST", "/api/login", {"login": "admin", "password": pwd, "otp": auth.totp_code(sec)})
        self.assertEqual(st, 200)
        self.assertTrue(r["otp_required"])
        self.assertEqual(r["user"]["role"], "admin")
        st, me = api("GET", "/api/me", token=r["token"])
        self.assertEqual(me["login"], "admin")

    def test_totp_rfc6238_vecteur(self):
        # Vecteur de test RFC 6238 (SHA-1, secret ASCII "12345678901234567890", T=59 -> 94287082 sur 8 chiffres)
        import base64 as b
        sec = b.b32encode(b"12345678901234567890").decode()
        self.assertEqual(auth.totp_code(sec, t=59, digits=8), "94287082")

    def test_chef_sans_otp_et_sans_token(self):
        chef = next(k for k in CREDS if k.startswith("chef_"))
        self.assertEqual(api("GET", "/api/me")[0], 401)
        self.assertEqual(api("GET", "/api/me", token="bidon")[0], 401)
        tk = login(chef)
        self.assertEqual(api("GET", "/api/me", token=tk)[1]["role"], "chef")

    def test_comptes_demo_et_fichier(self):
        for l in ("admin", "gest_kc", "gest_kw", "decideur"):
            self.assertIn(l, CREDS)
        self.assertTrue(any(k.startswith("chef_") for k in CREDS))
        mode = (Path(TMP) / "DEMO_CREDENTIALS.txt").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        tok = api("POST", "/api/login", {"login": "decideur", "password": CREDS["decideur"][0]})
        self.assertEqual(tok[0], 200)
        self.assertFalse(tok[1]["otp_required"])


class TestPerimetre(unittest.TestCase):
    def test_acces_inter_province_refuse_et_audite(self):  # EF-12 / ES-01
        kc = login("gest_kc")
        adm = login("admin")
        n0 = api("GET", "/api/admin/audit?limit=1", token=adm)[1]["total"]
        st, _ = api("GET", "/api/previsions?province_id=2", token=kc)
        self.assertEqual(st, 403)
        sd_kw = api("GET", "/api/geo", token=adm)[1]["sous_divisions"]
        kw_sd = next(s["id"] for s in sd_kw if s["province_id"] == 2)
        self.assertEqual(api("GET", f"/api/besoins?sous_division_id={kw_sd}", token=kc)[0], 403)
        self.assertEqual(api("GET", f"/api/dashboard?province_id=2", token=kc)[0], 403)
        gkw = login("gest_kw")
        reco_kw = api("GET", "/api/recommandations?statut=en_attente", token=gkw)[1]["items"][0]["id"]
        st, _ = api("POST", f"/api/recommandations/{reco_kw}/decision", {"choix": "accepte", "motif": "x"}, kc)
        self.assertEqual(st, 403)
        a = api("GET", "/api/admin/audit?limit=20", token=adm)[1]
        self.assertGreaterEqual(a["total"] - n0, 4)
        self.assertTrue(any(i["action"] == "acces_refuse" for i in a["items"]))
        # le gestionnaire ne voit que sa province
        geo = api("GET", "/api/geo", token=kc)[1]
        self.assertEqual([p["id"] for p in geo["provinces"]], [1])
        for it in api("GET", "/api/besoins", token=kc)[1]["items"]:
            self.assertNotEqual(it["sous_division"], "Kikwit")

    def test_roles(self):
        dec, chef, kc, adm = login("decideur"), login(next(k for k in CREDS if k.startswith("chef_"))), login("gest_kc"), login("admin")
        self.assertEqual(api("GET", "/api/previsions", token=dec)[0], 403)
        self.assertEqual(api("GET", "/api/recommandations", token=dec)[0], 403)
        self.assertEqual(api("GET", "/api/dashboard", token=dec)[0], 200)
        self.assertEqual(api("GET", "/api/besoins", token=dec)[0], 200)
        self.assertEqual(api("PUT", "/api/normes", {"normes": []}, kc)[0], 403)
        self.assertEqual(api("GET", "/api/admin/users", token=kc)[0], 403)
        self.assertEqual(api("GET", "/api/admin/audit", token=chef)[0], 403)
        self.assertEqual(api("POST", "/api/calcul", {}, chef)[0], 403)
        self.assertEqual(api("POST", "/api/alertes/preview", {}, adm)[0], 403)
        r = api("GET", "/api/besoins", token=chef)[1]["items"]
        self.assertTrue(r and len({i["ecole_id"] for i in r}) == 1)

    def test_noms_visibles_seulement_gestionnaire(self):
        kc, adm, dec = login("gest_kc"), login("admin"), login("decideur")
        it = api("GET", "/api/previsions?limit=5", token=kc)[1]["items"]
        self.assertTrue(all(i["nom"] for i in it))
        it = api("GET", "/api/previsions?limit=5", token=adm)[1]["items"]
        self.assertTrue(all(i["nom"] is None for i in it))
        chef = login(next(k for k in CREDS if k.startswith("chef_")))
        it = api("GET", "/api/previsions?limit=5", token=chef)[1]["items"]
        self.assertTrue(all(i["nom"] is None for i in it))

    def test_rbac_test_admin(self):
        r = api("GET", "/api/admin/rbac-test", token=login("admin"))[1]
        self.assertTrue(r["ok"], r)
        self.assertGreaterEqual(len(r["tests"]), 6)


class TestImport(unittest.TestCase):
    HEAD = "matricule,nom,date_naissance,date_entree,niveau,matiere,ecole_id,statut\n"

    def _ecole_kc(self):
        b = api("GET", "/api/besoins?niveau=primaire", token=login("gest_kc"))[1]["items"]
        return b[0]["ecole_id"]

    def _imp(self, text, who="gest_kc", kind="enseignants", name="f.csv"):
        return api("POST", "/api/import", {"kind": kind, "filename": name, "content_base64": csv_b64(text)}, login(who))

    def test_fichier_non_conforme_rapport_ligne_champ(self):  # EF-01
        e = self._ecole_kc()
        txt = (self.HEAD + f"M1,Test Un,1985-03-14,2010-09-01,primaire,Polyvalent,{e},actif\n"
               f"M2,Test Deux,14/03/1985,2010-09-01,primaire,Polyvalent,{e},actif\n"
               f"M3,Test Trois,1980-01-01,2005-09-01,college,Polyvalent,{e},actif\n"
               f"M4,,1980-01-01,2005-09-01,primaire,Polyvalent,{e},actif\n"
               f"M5,Test Cinq,1980-01-01,2005-09-01,primaire,Polyvalent,999999,actif\n")
        st, r = self._imp(txt)
        self.assertEqual(st, 200)
        self.assertEqual(r["importes"], 1)
        self.assertEqual(r["rejetes"], 4)
        champs = {(x["ligne"], x["champ"]) for x in r["rapport"]}
        self.assertIn((3, "date_naissance"), champs)
        self.assertIn((4, "niveau"), champs)
        self.assertIn((5, "nom"), champs)
        self.assertIn((6, "ecole_id"), champs)
        self.assertTrue(all(x["erreur"] for x in r["rapport"]))

    def test_colonne_hors_liste_blanche_rejetee(self):  # ES-04
        e = self._ecole_kc()
        txt = "matricule,nom,date_naissance,niveau,matiere,ecole_id,empreinte_digitale\n" + f"M9,Bio Test,1980-01-01,primaire,Polyvalent,{e},ABCDEF\n"
        st, r = self._imp(txt)
        self.assertEqual(st, 200)
        self.assertEqual(r["importes"], 0)
        self.assertTrue(any(x["champ"] == "empreinte_digitale" for x in r["rapport"]))
        st, r = self._imp("matricule,nom\nM1,Test\n")
        self.assertEqual(r["importes"], 0)
        self.assertTrue(any("absente" in x["erreur"] for x in r["rapport"]))

    def test_ecole_hors_province_rejetee(self):
        kw_ecole = api("GET", "/api/besoins?niveau=primaire", token=login("gest_kw"))[1]["items"][0]["ecole_id"]
        st, r = self._imp(self.HEAD + f"M7,Hors Zone,1985-03-14,2010-09-01,primaire,Polyvalent,{kw_ecole},actif\n")
        self.assertEqual(r["importes"], 0)
        self.assertTrue(any("province" in x["erreur"] for x in r["rapport"]))

    def test_xlsx_et_aucun_nom_dans_tables_analyse(self):  # EF-01, EF-02
        from openpyxl import Workbook
        e = self._ecole_kc()
        wb = Workbook()
        ws = wb.active
        ws.append(["matricule", "nom", "date_naissance", "date_entree", "niveau", "matiere", "ecole_id", "statut"])
        ws.append(["MAT-XLSX-777", "Zébulon QWXZNOM", "1990-05-05", "2015-09-01", "primaire", "Polyvalent", e, "actif"])
        bio = io.BytesIO()
        wb.save(bio)
        st, r = api("POST", "/api/import", {"kind": "enseignants", "filename": "t.xlsx", "content_base64": base64.b64encode(bio.getvalue()).decode()}, login("gest_kc"))
        self.assertEqual((st, r["importes"], r["rejetes"]), (200, 1, 0), r)
        pseudo = auth.pseudonym("MAT-XLSX-777")
        con = sqlite3.connect(str(db.db_path()))
        # nom du gestionnaire visible via l'API
        found = api("GET", "/api/previsions?limit=100000", token=login("gest_kc"))[1]["items"]
        self.assertTrue(any(i["enseignant_id"] == pseudo and i["nom"] == "Zébulon QWXZNOM" for i in found))
        # aucun nom ni matricule en clair hors de identite_enseignant
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        blob = []
        for t in tables:
            if t == "identite_enseignant":
                continue
            for row in con.execute(f"SELECT * FROM {t}"):
                blob.append(" ".join(str(x) for x in row))
        blob = "\n".join(blob)
        self.assertNotIn("QWXZNOM", blob)
        self.assertNotIn("MAT-XLSX-777", blob)
        names = [auth.decrypt_text(r[0]) for r in con.execute("SELECT nom_chiffre FROM identite_enseignant LIMIT 60")]
        for n in names:
            self.assertNotIn(n.split()[-1], " ".join(x for x in blob.split() if x.isupper()), n)
            self.assertNotIn(n, blob)
        raw = " ".join(r[0] for r in con.execute("SELECT nom_chiffre FROM identite_enseignant"))
        self.assertNotIn("QWXZNOM", raw)
        con.close()

    def test_modele_csv(self):
        st, raw, h = call("GET", "/api/import/template/enseignants", token=login("gest_kc"))
        self.assertEqual(st, 200)
        self.assertTrue(raw.decode().startswith("matricule,nom,date_naissance"))
        self.assertEqual(call("GET", "/api/import/template/xxx", token=login("gest_kc"))[0], 404)


class TestDecisions(unittest.TestCase):
    def _pending(self, who="gest_kc"):
        return api("GET", "/api/recommandations?statut=en_attente&limit=50", token=login(who))[1]["items"]

    def test_decision_sans_motif_refusee(self):  # EF-08
        r = self._pending()[0]
        kc = login("gest_kc")
        for b in ({"choix": "accepte"}, {"choix": "accepte", "motif": "   "}, {"choix": "bidon", "motif": "x"}):
            self.assertEqual(api("POST", f"/api/recommandations/{r['id']}/decision", b, kc)[0], 400)
        self.assertEqual(api("POST", f"/api/recommandations/{r['id']}/decision", {"choix": "modifie", "motif": "x"}, kc)[0], 400)
        self.assertEqual(api("GET", f"/api/recommandations?statut=en_attente&limit=1000", token=kc)[1]["total"] >= 1, True)

    def test_double_decision_409_et_historique(self):
        kc = login("gest_kc")
        r = self._pending()[1]
        self.assertTrue(r["contraintes_respectees"] and r["raisons"])
        st, d = api("POST", f"/api/recommandations/{r['id']}/decision", {"choix": "refuse", "motif": "Enseignant indispensable sur place"}, kc)
        self.assertEqual(st, 200, d)
        self.assertEqual(d["choix"], "refuse")
        st, d = api("POST", f"/api/recommandations/{r['id']}/decision", {"choix": "accepte", "motif": "revirement"}, kc)
        self.assertEqual(st, 409)
        lst = api("GET", "/api/recommandations?statut=refuse", token=kc)[1]["items"]
        it = next(i for i in lst if i["id"] == r["id"])
        self.assertEqual(it["decision"]["motif"], "Enseignant indispensable sur place")
        self.assertEqual(it["decision"]["utilisateur"], "gest_kc")

    def test_modifie_et_accepte(self):
        kc = login("gest_kc")
        pend = self._pending()
        a, b = pend[2], pend[3]
        autre = next(i for i in api("GET", "/api/besoins?ecart=manque", token=kc)[1]["items"] if i["ecole_id"] != b["ecole_arrivee_id"])["ecole_id"]
        self.assertEqual(api("POST", f"/api/recommandations/{a['id']}/decision", {"choix": "accepte", "motif": "Conforme aux besoins"}, kc)[0], 200)
        st, d = api("POST", f"/api/recommandations/{b['id']}/decision", {"choix": "modifie", "motif": "Arrivée plus proche", "ecole_arrivee_id": autre}, kc)
        self.assertEqual(st, 200, d)
        self.assertEqual(d["ecole_arrivee_modifiee_id"], autre)
        kw_ecole = api("GET", "/api/besoins", token=login("gest_kw"))[1]["items"][0]["ecole_id"]
        c = self._pending()[0]
        st, _ = api("POST", f"/api/recommandations/{c['id']}/decision", {"choix": "modifie", "motif": "x", "ecole_arrivee_id": kw_ecole}, kc)
        self.assertEqual(st, 403)


class TestAudit(unittest.TestCase):
    def test_immutabilite_et_chaine(self):  # ES-05
        adm = login("admin")
        a = api("GET", "/api/admin/audit?limit=5", token=adm)[1]
        self.assertTrue(a["chain_ok"])
        self.assertGreater(a["total"], 0)
        con = sqlite3.connect(str(db.db_path()))
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute("UPDATE audit_log SET action='x' WHERE id=1")
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute("DELETE FROM audit_log WHERE id=1")
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute("DELETE FROM audit_log")
        con.close()
        self.assertTrue(api("GET", "/api/admin/audit?limit=1", token=adm)[1]["chain_ok"])

    def test_chaine_detecte_alteration(self):
        from planedu import audit
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        db.init_schema(con)
        con.isolation_level = None
        audit.log(con, "u", "a", "1")
        audit.log(con, "u", "b", "2")
        self.assertTrue(audit.verify_chain(con)[0])
        con.execute("DROP TRIGGER audit_no_update")
        con.execute("UPDATE audit_log SET objet='falsifié' WHERE id=1")
        self.assertFalse(audit.verify_chain(con)[0])


class TestCalcul(unittest.TestCase):
    def test_modification_norme_change_besoin(self):  # EF-13
        adm, dec = login("admin"), login("decideur")

        def tot():
            return sum(i["requis"] for i in api("GET", "/api/besoins?niveau=primaire", token=dec)[1]["items"])

        normes = api("GET", "/api/normes", token=adm)[1]["normes"]
        prim = next(n for n in normes if n["niveau"] == "primaire")
        avant = tot()
        st, r = api("PUT", "/api/normes", {"normes": [dict(prim, eleves_par_enseignant=30)]}, adm)
        self.assertEqual(st, 200, r)
        apres = tot()
        self.assertGreater(apres, avant)
        api("PUT", "/api/normes", {"normes": [prim]}, adm)
        self.assertEqual(tot(), avant)
        self.assertEqual(api("PUT", "/api/normes", {"normes": [dict(prim, eleves_par_enseignant=0)]}, adm)[0], 400)

    def test_parametre_age_retraite_change_previsions(self):
        adm = login("admin")
        dec = login("decideur")

        def ret():
            return api("GET", "/api/dashboard?horizon=12", token=dec)[1]["serie_departs"][1]["retraite"]

        avant = ret()
        self.assertEqual(api("PUT", "/api/parametres", {"age_legal_retraite": 60}, adm)[0], 200)
        self.assertGreater(ret(), avant)
        self.assertEqual(api("PUT", "/api/parametres", {"age_legal_retraite": 65}, adm)[0], 200)
        self.assertEqual(ret(), avant)
        self.assertEqual(api("PUT", "/api/parametres", {"age_legal_retraite": 5}, adm)[0], 400)

    def test_calcul_gestionnaire(self):
        r = api("POST", "/api/calcul", {}, login("gest_kc"))[1]
        self.assertGreater(r["previsions"], 0)
        self.assertGreater(r["besoins"], 0)
        self.assertIn("recommandations", r)
        self.assertLess(r["duree_ms"], 20000)

    def test_dashboard_moins_de_5s(self):  # EF-09
        for who in ("decideur", "gest_kc", "admin"):
            t = time.time()
            st, d = api("GET", "/api/dashboard?horizon=12", token=login(who))
            self.assertEqual(st, 200)
            self.assertLess(time.time() - t, 5)
            self.assertLess(d["calcul_ms"], 5000)
            self.assertEqual(d["banniere"], "Données de démonstration")
        k = d["kpis"]
        for key in ("enseignants", "departs_prevus", "ecart_total", "ecoles_en_manque", "qualite_donnees_pct", "reco_traitees_pct"):
            self.assertIn(key, k)
        self.assertGreater(k["enseignants"], 800)
        st, d2 = api("GET", "/api/dashboard?province_id=1&niveau=primaire&horizon=6", token=login("decideur"))
        self.assertEqual(st, 200)
        self.assertLess(d2["kpis"]["enseignants"], k["enseignants"])
        self.assertTrue(all(s["province_id"] == 1 for s in d2["par_sous_division"]))

    def test_donnees_demo(self):
        adm = login("admin")
        self.assertEqual(len(api("GET", "/api/geo", token=adm)[1]["provinces"]), 2)
        b = api("GET", "/api/besoins", token=adm)[1]["items"]
        self.assertTrue(any(i["ecart"] > 0 for i in b) and any(i["ecart"] < 0 for i in b))
        p = api("GET", "/api/previsions?horizon=12&limit=10", token=login("gest_kc"))[1]
        self.assertGreater(p["total"], 300)
        probs = [i["probabilite_totale"] for i in p["items"]]
        self.assertEqual(probs, sorted(probs, reverse=True))
        self.assertTrue(p["items"][0]["raisons"])
        con = sqlite3.connect(str(db.db_path()))
        self.assertGreater(con.execute("SELECT COUNT(*) FROM enseignant WHERE statut='actif'").fetchone()[0], 850)
        con.close()

    def test_fiches_performance(self):  # EF-14
        r = api("GET", "/api/modeles/performance?horizon=12", token=login("decideur"))[1]
        self.assertEqual(r["horizon"], 12)
        self.assertGreater(r["metriques"]["auc"], r["baseline"]["auc"])
        self.assertIn("avertissement", r)
        self.assertTrue(r["erreur_par_province"] and r["erreur_par_niveau"])

    def test_alertes_simulees(self):  # EF-10
        kc = login("gest_kc")
        r = api("POST", "/api/alertes/preview", {}, kc)[1]
        self.assertTrue(r["items"])
        self.assertTrue(all(i["longueur"] <= 320 for i in r["items"]))
        s = api("POST", "/api/alertes/envoyer", {}, kc)[1]
        self.assertTrue(s["simulation"])
        self.assertEqual(s["envoyes"], len(r["items"]))


class TestExports(unittest.TestCase):
    def test_exports_valides(self):  # EF-11 / ET-03
        from openpyxl import load_workbook
        kc = login("gest_kc")
        for table in ("previsions", "besoins", "recommandations", "dashboard"):
            st, raw, h = call("GET", f"/api/export/{table}.xlsx?horizon=12", token=kc)
            self.assertEqual(st, 200, table)
            ws = load_workbook(io.BytesIO(raw)).active
            self.assertGreater(ws.max_row, 4, table)
            self.assertIn("Données de démonstration", str(ws["A2"].value))
            st, raw, h = call("GET", f"/api/export/{table}.pdf", token=kc)
            self.assertEqual(st, 200)
            self.assertTrue(raw.startswith(b"%PDF-") and b"%%EOF" in raw[-1024:], table)
            st, raw, h = call("GET", f"/api/export/{table}.csv", token=kc)
            self.assertEqual(st, 200)
            self.assertGreater(len(raw.decode("utf-8-sig").splitlines()), 1)
        self.assertEqual(call("GET", "/api/export/inconnu.csv", token=kc)[0], 404)
        self.assertEqual(call("GET", "/api/export/besoins.csv")[0], 401)

    def test_exports_sans_noms_pour_non_gestionnaires(self):
        adm = login("admin")
        raw = call("GET", "/api/export/previsions.csv", token=adm)[1].decode("utf-8-sig")
        self.assertNotIn(",nom,", raw.splitlines()[0])
        raw = call("GET", "/api/export/previsions.csv", token=login("gest_kc"))[1].decode("utf-8-sig")
        self.assertIn(",nom,", raw.splitlines()[0])
        self.assertEqual(call("GET", "/api/export/previsions.csv", token=login("decideur"))[0], 403)
        self.assertEqual(call("GET", "/api/export/previsions.csv?province_id=2", token=login("gest_kc"))[0], 403)

    def test_neutralisation_formules(self):
        from planedu import exports
        self.assertIn("'=CMD", exports.csv_bytes(["a"], [["=CMD()"]]).decode())


class TestAdmin(unittest.TestCase):
    def test_creation_utilisateur_et_desactivation(self):
        adm = login("admin")
        st, r = api("POST", "/api/admin/users", {"login": "chef_test", "role": "chef", "ecole_id": 1}, adm)
        self.assertEqual(st, 200, r)
        self.assertTrue(r["mot_de_passe_temporaire"])
        self.assertIsNone(r["totp_secret"])
        st, g = api("POST", "/api/admin/users", {"login": "gest_test", "role": "gestionnaire", "province_id": 1}, adm)
        self.assertTrue(g["totp_secret"])
        self.assertEqual(api("POST", "/api/admin/users", {"login": "gest_test", "role": "decideur"}, adm)[0], 409)
        self.assertEqual(api("POST", "/api/admin/users", {"login": "x", "role": "chef"}, adm)[0], 400)
        st, lg = api("POST", "/api/login", {"login": "chef_test", "password": r["mot_de_passe_temporaire"]})
        self.assertEqual(st, 200)
        users = api("GET", "/api/admin/users", token=adm)[1]["items"]
        self.assertNotIn("hash_mdp", users[0])
        uid = next(u["id"] for u in users if u["login"] == "chef_test")
        self.assertEqual(api("PATCH", f"/api/admin/users/{uid}", {"actif": False}, adm)[0], 200)
        self.assertEqual(api("GET", "/api/me", token=lg["token"])[0], 401)
        self.assertEqual(api("POST", "/api/login", {"login": "chef_test", "password": r["mot_de_passe_temporaire"]})[0], 401)

    def test_statique_et_sante(self):
        st, r = api("GET", "/api/health")
        self.assertEqual(st, 200)
        self.assertTrue(r["ok"])
        self.assertEqual(call("GET", "/")[0], 200)
        self.assertNotEqual(call("GET", "/../planedu/db.py")[1][:3], b'"""')
        self.assertNotIn(b"CREATE TABLE", call("GET", "/%2e%2e/planedu/db.py")[1])


class TestZBackupRestore(unittest.TestCase):
    def test_backup_puis_restore(self):  # ET-06
        adm = login("admin")
        st, b = api("POST", "/api/admin/backup", {}, adm)
        self.assertEqual(st, 200, b)
        self.assertEqual(len(b["sha256"]), 64)
        self.assertTrue(b["fichier"].endswith(".enc"))
        raw = (Path(TMP) / "backups" / b["fichier"]).read_bytes()
        self.assertNotIn(b"SQLite format", raw[:64])  # chiffrée
        avant = api("GET", "/api/parametres", token=adm)[1]["max_km"]
        self.assertEqual(api("PUT", "/api/parametres", {"max_km": 10}, adm)[1]["max_km"], 10)
        st, r = api("POST", "/api/admin/restore", {"fichier": b["fichier"]}, adm)
        self.assertEqual(st, 200, r)
        self.assertTrue(r["restaure"] and r["chain_ok"] and r["integrite"] == "ok")
        self.assertEqual(api("GET", "/api/parametres", token=adm)[1]["max_km"], avant)
        self.assertEqual(api("GET", "/api/me", token=adm)[0], 200)
        self.assertTrue(api("GET", "/api/admin/audit", token=adm)[1]["chain_ok"])
        self.assertEqual(api("POST", "/api/admin/restore", {"fichier": "../../etc/passwd"}, adm)[0], 404)
        self.assertEqual(api("POST", "/api/admin/restore", {"fichier": "planedu-0.db.enc"}, adm)[0], 404)
        for k in [k for k in _tokens if k != "admin"]:
            del _tokens[k]  # sessions créées après la sauvegarde : perdues par la restauration (comportement attendu)


