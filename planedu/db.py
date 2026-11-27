"""Accès SQLite (WAL), schéma des 12 tables CDC §5 + identité isolée."""
import contextlib
import os
import sqlite3
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PLANEDU_DATA", str(ROOT / "data")))
_local = threading.local()


class ApiError(Exception):
    status = 400

    def __init__(self, msg, status=None, **extra):
        super().__init__(msg)
        if status:
            self.status = status
        self.msg = msg
        self.extra = extra


class BadRequest(ApiError):
    status = 400


class Unauthorized(ApiError):
    status = 401


class Forbidden(ApiError):
    status = 403


class NotFound(ApiError):
    status = 404


class Conflict(ApiError):
    status = 409


def configure(data_dir):
    global DATA_DIR
    DATA_DIR = Path(data_dir)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def db_path():
    return DATA_DIR / "planedu.db"


def connect():
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    p = str(db_path())
    c = conns.get(p)
    if c is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(p, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")
        conns[p] = c
    return c


def close_all():
    conns = getattr(_local, "conns", None) or {}
    for c in conns.values():
        try:
            c.close()
        except Exception:
            pass
    _local.conns = {}


@contextlib.contextmanager
def tx(conn=None):
    c = conn or connect()
    if c.in_transaction:
        yield c
        return
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


SCHEMA = """
CREATE TABLE IF NOT EXISTS province(id INTEGER PRIMARY KEY, nom TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sous_division(id INTEGER PRIMARY KEY, nom TEXT NOT NULL,
  province_id INTEGER NOT NULL REFERENCES province(id));
CREATE TABLE IF NOT EXISTS ecole(id INTEGER PRIMARY KEY, nom TEXT NOT NULL,
  sous_division_id INTEGER NOT NULL REFERENCES sous_division(id),
  niveau TEXT NOT NULL CHECK(niveau IN ('primaire','secondaire')), lat REAL, lon REAL);
CREATE TABLE IF NOT EXISTS effectif_eleves(id INTEGER PRIMARY KEY, ecole_id INTEGER NOT NULL REFERENCES ecole(id),
  annee INTEGER NOT NULL, niveau TEXT NOT NULL, nombre INTEGER NOT NULL, UNIQUE(ecole_id,annee,niveau));
CREATE TABLE IF NOT EXISTS norme(id INTEGER PRIMARY KEY, niveau TEXT NOT NULL, matiere TEXT NOT NULL,
  eleves_par_enseignant INTEGER NOT NULL, en_vigueur_depuis INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS enseignant(id TEXT PRIMARY KEY, date_naissance TEXT NOT NULL, date_entree TEXT,
  niveau TEXT NOT NULL, matiere TEXT NOT NULL, statut TEXT NOT NULL DEFAULT 'actif');
CREATE TABLE IF NOT EXISTS affectation(id INTEGER PRIMARY KEY, enseignant_id TEXT NOT NULL REFERENCES enseignant(id),
  ecole_id INTEGER NOT NULL REFERENCES ecole(id), debut TEXT, fin TEXT);
CREATE TABLE IF NOT EXISTS depart_historique(id INTEGER PRIMARY KEY, enseignant_id TEXT NOT NULL REFERENCES enseignant(id),
  date TEXT NOT NULL, motif TEXT NOT NULL CHECK(motif IN ('retraite','abandon','absence_prolongee','deces')));
CREATE TABLE IF NOT EXISTS prevision_depart(id INTEGER PRIMARY KEY, enseignant_id TEXT NOT NULL REFERENCES enseignant(id),
  horizon INTEGER NOT NULL, probabilite REAL NOT NULL, facteurs TEXT, version_modele TEXT, retraite_date TEXT,
  UNIQUE(enseignant_id,horizon));
CREATE TABLE IF NOT EXISTS besoin_ecole(id INTEGER PRIMARY KEY, ecole_id INTEGER NOT NULL REFERENCES ecole(id),
  annee INTEGER NOT NULL, niveau TEXT NOT NULL, matiere TEXT NOT NULL, requis INTEGER NOT NULL,
  prevu INTEGER NOT NULL, ecart INTEGER NOT NULL, actuel INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS recommandation(id INTEGER PRIMARY KEY, enseignant_id TEXT NOT NULL REFERENCES enseignant(id),
  ecole_depart_id INTEGER NOT NULL REFERENCES ecole(id), ecole_arrivee_id INTEGER NOT NULL REFERENCES ecole(id),
  gain_ecart REAL, rang INTEGER, raisons TEXT, km REAL, statut TEXT NOT NULL DEFAULT 'en_attente',
  niveau TEXT, matiere TEXT);
CREATE TABLE IF NOT EXISTS decision(id INTEGER PRIMARY KEY, recommandation_id INTEGER NOT NULL UNIQUE REFERENCES recommandation(id),
  utilisateur TEXT NOT NULL, choix TEXT NOT NULL CHECK(choix IN ('accepte','modifie','refuse')),
  motif TEXT NOT NULL, date TEXT NOT NULL, ecole_arrivee_modifiee_id INTEGER REFERENCES ecole(id));
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, utilisateur TEXT, action TEXT NOT NULL,
  objet TEXT, date TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT,'audit_log est en ajout seul (UPDATE interdit)'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT,'audit_log est en ajout seul (DELETE interdit)'); END;
CREATE TABLE IF NOT EXISTS parametre(cle TEXT PRIMARY KEY, valeur TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS utilisateur(id INTEGER PRIMARY KEY, login TEXT NOT NULL UNIQUE, hash_mdp TEXT NOT NULL,
  sel TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','gestionnaire','chef','decideur')),
  province_id INTEGER REFERENCES province(id), ecole_id INTEGER REFERENCES ecole(id), totp_secret TEXT,
  actif INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS session(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES utilisateur(id), expire REAL NOT NULL);
CREATE TABLE IF NOT EXISTS modele_perf(id INTEGER PRIMARY KEY, horizon INTEGER NOT NULL, methode TEXT, donnees TEXT,
  date TEXT, erreur_par_province TEXT, erreur_par_niveau TEXT, metriques TEXT, version_modele TEXT);
CREATE TABLE IF NOT EXISTS identite_enseignant(enseignant_id TEXT PRIMARY KEY REFERENCES enseignant(id), nom_chiffre TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_aff_ens ON affectation(enseignant_id, fin);
CREATE INDEX IF NOT EXISTS ix_aff_ec ON affectation(ecole_id);
CREATE INDEX IF NOT EXISTS ix_prev_h ON prevision_depart(horizon, probabilite);
CREATE INDEX IF NOT EXISTS ix_besoin ON besoin_ecole(ecole_id);
CREATE INDEX IF NOT EXISTS ix_reco ON recommandation(statut);
CREATE INDEX IF NOT EXISTS ix_ecole_sd ON ecole(sous_division_id);
"""


def init_schema(conn=None):
    c = conn or connect()
    c.executescript(SCHEMA)


def is_empty():
    c = connect()
    init_schema(c)
    return c.execute("SELECT COUNT(*) FROM utilisateur").fetchone()[0] == 0


def get_params(conn=None):
    c = conn or connect()
    return {r["cle"]: r["valeur"] for r in c.execute("SELECT cle,valeur FROM parametre")}
