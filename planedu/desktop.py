"""Application de bureau : serveur local + fenêtre native (pywebview) ; repli navigateur. Mode démo, 100 % local (127.0.0.1)."""
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _data_dir():
    base = os.environ.get("APPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "PLANEDU-IA"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PLANEDU_DATA", str(d))
    os.environ["PLANEDU_DEMO"] = "1"
    os.environ["PLANEDU_DEMO_NO_OTP"] = "1"
    pwd = os.environ.setdefault("PLANEDU_DEMO_PASSWORD", "Demo-PLANEDU-2026")  # démo locale, données synthétiques
    os.environ["PLANEDU_DEMO_HINT"] = pwd
    from . import db, server
    db.configure(str(d))
    server.ensure_data(verbose=False)
    port = _free_port()
    srv = server.make_server("127.0.0.1", port, False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    try:
        import webview  # pywebview
        webview.create_window("PLANEDU-IA — démonstration", url, width=1280, height=820, min_size=(420, 600))
        webview.start()
    except Exception:
        webbrowser.open(url)
        print(f"PLANEDU-IA ouvert dans le navigateur : {url}  (Ctrl+C pour quitter)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
