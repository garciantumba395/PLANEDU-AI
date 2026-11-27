"""Serveur HTTP (stdlib) : API REST JSON /api + fichiers statiques de planedu/static."""
import base64
import json
import mimetypes
import re
import sys
import threading
import time
import traceback
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import VERSION, audit, auth, db, service
from .db import ApiError, BadRequest, Forbidden, NotFound, Unauthorized

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 30 * 1024 * 1024
_fails = {}
_fails_lock = threading.Lock()


class Raw:
    def __init__(self, body, ctype, filename=None, status=200):
        self.body, self.ctype, self.filename, self.status = body, ctype, filename, status


# ---------------------------------------------------------------- handlers : f(ctx) -> dict | Raw
class Ctx:
    def __init__(self, conn, user, q, body, args, headers, raw, token):
        self.conn, self.user, self.q, self.body, self.args = conn, user, q, body, args
        self.headers, self.raw, self.token = headers, raw, token


def h_login(c):
    login, pwd, otp = str(c.body.get("login") or "").strip(), str(c.body.get("password") or ""), c.body.get("otp")
    now = time.time()
    with _fails_lock:
        n, until = _fails.get(login, (0, 0))
        if until > now:
            raise ApiError("trop de tentatives, réessayez dans une minute", 429)
    u = c.conn.execute("SELECT u.*, p.nom province_nom FROM utilisateur u LEFT JOIN province p ON p.id=u.province_id WHERE login=? AND actif=1", (login,)).fetchone()
    good = bool(u) and auth.verify_password(pwd, u["hash_mdp"], u["sel"])
    if not u:
        auth.hash_password(pwd)  # temps constant approximatif
    if good and u["role"] in auth.OTP_ROLES:
        if not otp:
            raise ApiError("otp_required", 401, otp_required=True)
        if not auth.totp_verify(auth.user_secret(u), otp):
            good = False
    if not good:
        with _fails_lock:
            n += 1
            _fails[login] = (n, now + 60 if n >= 8 else 0)
        audit.log(c.conn, login or "?", "login_echec", "identifiants ou code OTP invalides")
        raise Unauthorized("identifiants ou code OTP invalides")
    with _fails_lock:
        _fails.pop(login, None)
    with db.tx(c.conn):
        c.conn.execute("DELETE FROM session WHERE expire<?", (time.time(),))
        token = auth.create_session(c.conn, u["id"])
        audit.log(c.conn, u["login"], "login", u["role"])
    return {"token": token, "otp_required": u["role"] in auth.OTP_ROLES, "user": public_user(dict(u))}


def public_user(u):
    return {"id": u["id"], "login": u["login"], "role": u["role"], "province_id": u["province_id"], "ecole_id": u["ecole_id"],
            "province_nom": u.get("province_nom")}


def h_logout(c):
    auth.drop_session(c.conn, c.token)
    audit.log(c.conn, c.user["login"], "logout", "")
    return {"ok": True}


def mkdecision(c):
    return service.decider(c.conn, c.user, int(c.args[0]), c.body)


def h_import(c):
    ctype = c.headers.get("Content-Type", "")
    if ctype.startswith("multipart/form-data"):
        msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + c.raw)
        kind, fname, data = None, "import", None
        for part in msg.iter_parts():
            nm = part.get_param("name", header="content-disposition")
            if nm == "kind":
                kind = part.get_content().strip() if part.get_content_type().startswith("text") else part.get_payload(decode=True).decode().strip()
            elif nm == "file":
                fname = part.get_filename() or fname
                data = part.get_payload(decode=True)
        if data is None:
            raise BadRequest("champ 'file' manquant")
        kind = kind or c.q.get("kind")
    else:
        kind, fname = c.body.get("kind"), c.body.get("filename") or "import"
        try:
            data = base64.b64decode(c.body.get("content_base64") or "", validate=True)
        except Exception:
            raise BadRequest("content_base64 invalide")
    return service.import_data(c.conn, c.user, kind, fname, data)


def h_export(c):
    table, fmt = c.args
    ctype, body = service.export_file(c.conn, c.user, table, fmt, c.q)
    return Raw(body, ctype, f"planedu_{table}.{fmt}")


def h_template(c):
    return Raw(service.import_template(c.args[0]), "text/csv; charset=utf-8", f"modele_{c.args[0]}.csv")


ROUTES = [
    ("GET", r"/api/health", lambda c: {"ok": True, "version": VERSION}, False),
    ("POST", r"/api/login", h_login, False),
    ("POST", r"/api/logout", h_logout, True),
    ("GET", r"/api/me", lambda c: public_user(c.user), True),
    ("GET", r"/api/geo", lambda c: service.geo(c.conn, c.user), True),
    ("GET", r"/api/dashboard", lambda c: service.dashboard(c.conn, c.user, c.q), True),
    ("GET", r"/api/previsions", lambda c: service.previsions(c.conn, c.user, c.q), True),
    ("GET", r"/api/besoins", lambda c: service.besoins(c.conn, c.user, c.q), True),
    ("POST", r"/api/calcul", lambda c: service.run_calcul(c.conn, c.user), True),
    ("GET", r"/api/recommandations", lambda c: service.recommandations(c.conn, c.user, c.q), True),
    ("POST", r"/api/recommandations/(\d+)/decision", mkdecision, True),
    ("POST", r"/api/import", h_import, True),
    ("GET", r"/api/import/template/(\w+)", h_template, True),
    ("GET", r"/api/normes", lambda c: service.get_normes(c.conn) if c.user["role"] in ("admin", "gestionnaire", "decideur") else service.require_role(c.user, "admin"), True),
    ("PUT", r"/api/normes", lambda c: service.put_normes(c.conn, c.user, c.body), True),
    ("GET", r"/api/parametres", lambda c: service.get_parametres(c.conn) if c.user["role"] in ("admin", "gestionnaire", "decideur") else service.require_role(c.user, "admin"), True),
    ("PUT", r"/api/parametres", lambda c: service.put_parametres(c.conn, c.user, c.body), True),
    ("GET", r"/api/modeles/performance", lambda c: service.performance(c.conn, c.user, c.q), True),
    ("GET", r"/api/export/(\w+)\.(\w+)", h_export, True),
    ("GET", r"/api/admin/users", lambda c: service.list_users(c.conn, c.user), True),
    ("POST", r"/api/admin/users", lambda c: service.create_user(c.conn, c.user, c.body), True),
    ("PATCH", r"/api/admin/users/(\d+)", lambda c: service.patch_user(c.conn, c.user, int(c.args[0]), c.body), True),
    ("GET", r"/api/admin/audit", lambda c: service.audit_list(c.conn, c.user, c.q), True),
    ("POST", r"/api/admin/backup", lambda c: service.backup(c.conn, c.user), True),
    ("GET", r"/api/admin/backups", lambda c: service.list_backups(c.conn, c.user), True),
    ("POST", r"/api/admin/restore", lambda c: service.restore(c.conn, c.user, c.body, c.token), True),
    ("GET", r"/api/admin/rbac-test", lambda c: service.rbac_test(c.conn, c.user), True),
    ("POST", r"/api/alertes/preview", lambda c: service.alertes_preview(c.conn, c.user), True),
    ("POST", r"/api/alertes/envoyer", lambda c: service.alertes_envoyer(c.conn, c.user, c.body), True),
]
ROUTES = [(m, re.compile(p + r"$"), f, a) for m, p, f, a in ROUTES]


class Handler(BaseHTTPRequestHandler):
    server_version = "PlanEdu"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- sortie
    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    # -- entrée
    def do_GET(self):
        self._dispatch()

    do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_GET

    def _dispatch(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        method = "GET" if self.command == "HEAD" else self.command
        if not path.startswith("/api/"):
            if self.command in ("GET", "HEAD"):
                return self._static(path)
            return self._json(405, {"error": "méthode non autorisée"})
        try:
            raw, body = b"", {}
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                self.close_connection = True
                return self._json(413, {"error": "corps de requête trop volumineux"})
            if n:
                raw = self.rfile.read(n)
                if "json" in (self.headers.get("Content-Type") or "") or (raw[:1] in (b"{", b"[") and "multipart" not in (self.headers.get("Content-Type") or "")):
                    try:
                        body = json.loads(raw.decode("utf-8"))
                    except ValueError:
                        raise BadRequest("JSON invalide")
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            for m, rx, fn, needs_auth in ROUTES:
                if m != method:
                    continue
                mt = rx.match(path)
                if not mt:
                    continue
                conn = db.connect()
                user, token = None, None
                if needs_auth:
                    h = self.headers.get("Authorization") or ""
                    token = h[7:].strip() if h.startswith("Bearer ") else None
                    user = auth.user_from_token(conn, token)
                    if not user:
                        raise Unauthorized("authentification requise")
                if not isinstance(body, dict) and method in ("POST", "PUT", "PATCH") and not (rx.pattern.startswith("/api/normes")):
                    raise BadRequest("objet JSON attendu")
                try:
                    res = fn(Ctx(conn, user, q, body, mt.groups(), self.headers, raw, token))
                except Forbidden as e:
                    try:
                        audit.log(conn, user["login"] if user else "?", "acces_refuse", f"{method} {path} : {e.msg} ({e.extra.get('objet', '')})")
                    except Exception:
                        pass
                    raise
                if isinstance(res, Raw):
                    return self._send(res.status, res.body, res.ctype, {"Content-Disposition": f'attachment; filename="{res.filename}"', "Cache-Control": "no-store"} if res.filename else None)
                return self._json(200, res if res is not None else {"ok": True})
            return self._json(404, {"error": "route inconnue"})
        except ApiError as e:
            return self._json(e.status, dict({"error": e.msg}, **e.extra) if e.status != 403 else {"error": e.msg})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            traceback.print_exc()
            return self._json(500, {"error": "erreur interne du serveur"})
        finally:
            db.close_all()

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        fp = (STATIC / rel).resolve()
        if STATIC.resolve() not in fp.parents and fp != STATIC.resolve():
            return self._json(404, {"error": "introuvable"})
        if not fp.is_file():
            fp = STATIC / "index.html"  # SPA hash-router : repli
            if not fp.is_file():
                return self._send(404, "Interface non encore installée.".encode(), "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(fp.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, fp.read_bytes(), ctype, {"Cache-Control": "no-cache"})


def make_server(host="127.0.0.1", port=8000, verbose=False):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.verbose = verbose
    return srv


def ensure_data(verbose=True):
    """Premier démarrage : crée la base et les données de démonstration."""
    c = db.connect()
    if db.is_empty():
        from . import seed
        if verbose:
            print("[planedu] premier démarrage : création de la base et des données de démonstration...", flush=True)
        t = time.time()
        seed.run(c)
        if verbose:
            print(f"[planedu] initialisation terminée en {time.time() - t:.1f} s ; identifiants dans {db.DATA_DIR / 'DEMO_CREDENTIALS.txt'}", flush=True)
