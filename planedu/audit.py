"""Journal d'audit : chaîne de hachage SHA-256, en ajout seul (triggers SQLite, ES-05)."""
import hashlib
from datetime import datetime, timezone

from . import db

GENESIS = "0" * 64


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def compute_hash(prev_hash, utilisateur, action, objet, date):
    payload = "|".join([prev_hash, utilisateur or "", action or "", objet or "", date])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def log(conn, utilisateur, action, objet=""):
    """Ajoute une entrée. Utilise une transaction d'écriture (sérialise la chaîne)."""
    with db.tx(conn):
        row = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else GENESIS
        date = _now()
        h = compute_hash(prev, utilisateur, action, str(objet), date)
        conn.execute(
            "INSERT INTO audit_log(utilisateur,action,objet,date,prev_hash,hash) VALUES(?,?,?,?,?,?)",
            (utilisateur, action, str(objet), date, prev, h),
        )


def verify_chain(conn):
    prev = GENESIS
    n = 0
    for r in conn.execute("SELECT * FROM audit_log ORDER BY id"):
        if r["prev_hash"] != prev:
            return False, n
        if compute_hash(prev, r["utilisateur"], r["action"], r["objet"], r["date"]) != r["hash"]:
            return False, n
        prev = r["hash"]
        n += 1
    return True, n
