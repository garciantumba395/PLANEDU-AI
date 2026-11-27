"""Authentification : PBKDF2, TOTP RFC 6238 (stdlib), sessions, chiffrement des identités (clé hors code)."""
import base64
import hashlib
import hmac
import os
import secrets
import struct
import time

from . import db

PBKDF2_ITER = 200_000
SESSION_TTL = 8 * 3600
OTP_ROLES = ("admin", "gestionnaire")
_keycache = {}


# ---------------------------------------------------------------- clé maître
def master_key():
    env = os.environ.get("PLANEDU_KEY")
    if env:
        return hashlib.sha256(env.encode()).digest()
    p = db.DATA_DIR / ".key"
    k = _keycache.get(str(p))
    if k:
        return k
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if p.exists():
        k = bytes.fromhex(p.read_text().strip())
    else:
        k = secrets.token_bytes(32)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(k.hex())
    _keycache[str(p)] = k
    return k


def _sub(label):
    return hmac.new(master_key(), label.encode(), hashlib.sha256).digest()


def pseudonym(matricule):
    return "T-" + hmac.new(_sub("pseudo"), str(matricule).strip().encode(), hashlib.sha256).hexdigest()[:8]


# ---------------------------------------------------------------- chiffrement (flux HMAC-SHA256 + MAC)
def _stream(key, nonce, n):
    out = bytearray()
    c = 0
    while len(out) < n:
        out += hmac.new(key, nonce + struct.pack(">Q", c), hashlib.sha256).digest()
        c += 1
    return bytes(out[:n])


def encrypt_bytes(data: bytes) -> bytes:
    nonce = secrets.token_bytes(16)
    ct = bytes(a ^ b for a, b in zip(data, _stream(_sub("enc"), nonce, len(data))))
    mac = hmac.new(_sub("mac"), nonce + ct, hashlib.sha256).digest()
    return nonce + ct + mac


def decrypt_bytes(blob: bytes) -> bytes:
    nonce, ct, mac = blob[:16], blob[16:-32], blob[-32:]
    if not hmac.compare_digest(mac, hmac.new(_sub("mac"), nonce + ct, hashlib.sha256).digest()):
        raise ValueError("intégrité du chiffrement invalide")
    return bytes(a ^ b for a, b in zip(ct, _stream(_sub("enc"), nonce, len(ct))))


def encrypt_text(s: str) -> str:
    return base64.b64encode(encrypt_bytes(s.encode("utf-8"))).decode()


def decrypt_text(s: str) -> str:
    return decrypt_bytes(base64.b64decode(s)).decode("utf-8")


# ---------------------------------------------------------------- mots de passe
def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITER)
    return h.hex(), salt.hex()


def verify_password(password, hash_hex, salt_hex):
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), PBKDF2_ITER)
    return hmac.compare_digest(h.hex(), hash_hex)


def new_password():
    return secrets.token_urlsafe(12)


# ---------------------------------------------------------------- TOTP RFC 6238
def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret, t=None, step=30, digits=6):
    t = time.time() if t is None else t
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    counter = struct.pack(">Q", int(t // step))
    d = hmac.new(key, counter, hashlib.sha1).digest()
    o = d[-1] & 0x0F
    code = (struct.unpack(">I", d[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_verify(secret, code, t=None, window=1):
    t = time.time() if t is None else t
    code = str(code or "").strip()
    return any(hmac.compare_digest(totp_code(secret, t + w * 30), code) for w in range(-window, window + 1))


def user_secret(row):
    return decrypt_text(row["totp_secret"]) if row["totp_secret"] else None


def create_user(conn, login, role, province_id=None, ecole_id=None, password=None):
    """Crée un compte ; renvoie (id, mot de passe, secret TOTP|None)."""
    password = password or new_password()
    h, s = hash_password(password)
    secret = new_totp_secret() if role in OTP_ROLES else None
    cur = conn.execute(
        "INSERT INTO utilisateur(login,hash_mdp,sel,role,province_id,ecole_id,totp_secret,actif) VALUES(?,?,?,?,?,?,?,1)",
        (login, h, s, role, province_id, ecole_id, encrypt_text(secret) if secret else None),
    )
    return cur.lastrowid, password, secret


# ---------------------------------------------------------------- sessions
def create_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO session(token,user_id,expire) VALUES(?,?,?)", (token, user_id, time.time() + SESSION_TTL))
    return token


def user_from_token(conn, token):
    if not token:
        return None
    r = conn.execute(
        "SELECT u.*, p.nom AS province_nom FROM session s JOIN utilisateur u ON u.id=s.user_id "
        "LEFT JOIN province p ON p.id=u.province_id WHERE s.token=? AND s.expire>? AND u.actif=1",
        (token, time.time()),
    ).fetchone()
    return dict(r) if r else None


def drop_session(conn, token):
    conn.execute("DELETE FROM session WHERE token=?", (token,))
