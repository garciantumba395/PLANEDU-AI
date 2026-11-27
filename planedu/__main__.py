"""Point d'entrée : python3 -m planedu [--port 8000] [--host 127.0.0.1] [--data DIR] | otp <login>"""
import argparse
import sys
import time

from . import auth, db, server


def main(argv=None):
    ap = argparse.ArgumentParser(prog="planedu")
    ap.add_argument("commande", nargs="?", default="serve", choices=["serve", "otp"])
    ap.add_argument("login", nargs="?")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--data", help="dossier de données (défaut : ./data ou $PLANEDU_DATA)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    if a.data:
        db.configure(a.data)
    if a.commande == "otp":
        if not a.login:
            ap.error("usage : python3 -m planedu otp <login>")
        c = db.connect()
        db.init_schema(c)
        r = c.execute("SELECT * FROM utilisateur WHERE login=?", (a.login,)).fetchone()
        if not r or not r["totp_secret"]:
            print("compte inconnu ou sans 2FA", file=sys.stderr)
            return 1
        print(f"{auth.totp_code(auth.user_secret(r))}  (valable encore {30 - int(time.time()) % 30} s)")
        return 0
    server.ensure_data()
    srv = server.make_server(a.host, a.port, a.verbose)
    print(f"[planedu] http://{a.host}:{a.port}  (Ctrl+C pour arrêter)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[planedu] arrêt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
