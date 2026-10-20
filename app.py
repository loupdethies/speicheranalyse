"""Speicheranalyse – lokale App.

Startet einen kleinen Webserver auf 127.0.0.1 und öffnet den neuesten Bericht
im Browser. Über eine Leiste im Bericht lassen sich Laufwerke (auch USB-Sticks)
neu analysieren – auf Wunsch mit Admin-Rechten (Windows fragt per UAC) – und
frühere Berichte öffnen.

Sicherheit: Der Server lauscht nur auf 127.0.0.1, jede Anfrage braucht einen
zufälligen Schlüssel aus der Start-URL, und er führt nur die Analyse-Schritte
dieses Projekts aus. Laufwerke werden gegen die echte Laufwerksliste geprüft.

Aufruf:
    pythonw app.py            # ohne Konsolenfenster (so startet die Verknüpfung)
    python app.py --port 8765 # fester Port, Konsole zeigt Meldungen
"""
from __future__ import annotations

import argparse
import ctypes
import hmac
import json
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import threading
import time
import webbrowser
from ctypes import wintypes
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

HERE = Path(__file__).resolve().parent
SCANS = HERE / "scans"
REPORTS = HERE / "berichte"
JOBS = SCANS / "laufend"
REPORT_NAME = re.compile(r"^bericht_[A-Z]_\d{8}_\d{4}\.html$")
DRIVE_TYPES = {2: "Wechseldatenträger", 3: "Festplatte"}  # 4 = Netzlaufwerk, 5 = CD/DVD: nicht angeboten
IDLE_EXIT_S = 15 * 60  # ohne Anfragen beenden
BYE_EXIT_S = 8         # nach Schließen des Tabs, falls keine neue Seite kommt

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
_k32.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)


# --- Laufwerke ---------------------------------------------------------------
def drives() -> list[dict]:
    out = []
    system = os.environ.get("SystemDrive", "C:").upper()
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        kind = _k32.GetDriveTypeW(root)
        if kind not in DRIVE_TYPES:
            continue
        label, fs = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(32)
        if not _k32.GetVolumeInformationW(root, label, 261, None, None, None, fs, 32):
            continue  # z. B. Kartenleser ohne Karte
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        out.append({"lw": f"{letter}:", "label": label.value, "fs": fs.value, "typ": DRIVE_TYPES[kind],
                    "system": f"{letter}:" == system, "total": usage.total, "used": usage.used})
    return out


def reports() -> list[dict]:
    out = []
    for f in REPORTS.glob("bericht_*.html"):
        if not REPORT_NAME.match(f.name):
            continue
        _, lw, d, t = f.stem.split("_")
        out.append({"name": f.name, "lw": lw + ":", "zeit": f"{d[:4]}-{d[4:6]}-{d[6:]}T{t[:2]}:{t[2:]}",
                    "mtime": f.stat().st_mtime})
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


# --- Analyse-Lauf ------------------------------------------------------------
def console_python() -> str:
    """python.exe neben pythonw.exe – die Teilschritte brauchen stdout/stderr."""
    exe = Path(sys.executable)
    alt = exe.with_name("python.exe")
    return str(alt if alt.exists() else exe)


def write_status(job: Path, **kw) -> None:
    f = job / "status.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.update(kw)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def run_pipeline(lw: str, job: Path) -> int:
    """Scannen → klassifizieren → abgleichen → Bericht. Läuft als eigener Prozess (ggf. erhöht)."""
    py = console_python()
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    scan = SCANS / f"scan_{lw[0]}_{stamp}.json"
    klass = scan.with_name(scan.stem + "_klassifiziert.json")
    report = REPORTS / f"bericht_{lw[0]}_{stamp}.html"
    steps = [
        ("scan", [py, "-I", str(HERE / "scanner.py"), "--root", lw, "--out", str(scan),
                  "--progress", str(job / "fortschritt.json")]),
        ("klassifizieren", [py, "-I", str(HERE / "classify.py"), str(scan)]),
        ("abgleich", [py, "-I", str(HERE / "abgleich.py"), str(klass)]),
        ("bericht", [py, "-I", str(HERE / "report.py"), str(klass), "--out", str(report)]),
    ]
    write_status(job, phase="start", admin=bool(ctypes.windll.shell32.IsUserAnAdmin()), pid=os.getpid())
    with open(job / "protokoll.txt", "w", encoding="utf-8") as log:
        for phase, cmd in steps:
            write_status(job, phase=phase)
            log.write(f"\n== {phase}: {' '.join(cmd)}\n")
            log.flush()
            r = subprocess.run(cmd, stdout=log, stderr=log, cwd=HERE, creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode != 0:
                write_status(job, phase="fehler", fehler=f"Schritt „{phase}“ ist fehlgeschlagen (Code {r.returncode}).")
                return 1
    write_status(job, phase="fertig", bericht=report.name)
    return 0


class SHELLEXECUTEINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


class Job:
    """Ein laufender Analyse-Lauf; der Server fragt nur Statusdateien ab."""

    def __init__(self, lw: str, admin: bool):
        self.lw, self.admin = lw, admin
        self.dir = JOBS / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.started = time.time()
        self.proc: subprocess.Popen | None = None
        self.handle = None
        self.error: str | None = None
        args = ["-I", str(HERE / "app.py"), "--pipeline", lw, "--job", str(self.dir)]
        if admin:
            self._start_elevated(args)
        else:
            self.proc = subprocess.Popen([sys.executable, *args], cwd=HERE, creationflags=subprocess.CREATE_NO_WINDOW,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _start_elevated(self, args: list[str]) -> None:
        exe = Path(sys.executable).with_name("pythonw.exe")
        params = subprocess.list2cmdline(args)
        info = SHELLEXECUTEINFO(cbSize=ctypes.sizeof(SHELLEXECUTEINFO), fMask=0x40,  # SEE_MASK_NOCLOSEPROCESS
                                lpVerb="runas", lpFile=str(exe if exe.exists() else sys.executable),
                                lpParameters=params, lpDirectory=str(HERE), nShow=0)
        if _shell32.ShellExecuteExW(ctypes.byref(info)):
            self.handle = info.hProcess
        else:
            err = ctypes.get_last_error()
            self.error = "Admin-Rechte wurden abgelehnt." if err == 1223 else f"Start mit Admin-Rechten fehlgeschlagen (Fehler {err})."

    def running(self) -> bool:
        if self.proc:
            return self.proc.poll() is None
        if self.handle:
            return _k32.WaitForSingleObject(self.handle, 0) == 0x102  # WAIT_TIMEOUT
        return False

    def status(self) -> dict:
        def read(name):
            try:
                return json.loads((self.dir / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
        st = read("status.json")
        st.update(lw=self.lw, admin_gewuenscht=self.admin, elapsed=round(time.time() - self.started))
        if self.error:
            st.update(phase="fehler", fehler=self.error)
        elif st.get("phase") not in ("fertig", "fehler") and not self.running():
            st.update(phase="fehler", fehler="Der Analyse-Prozess wurde unerwartet beendet.")
        if st.get("phase") == "scan":
            st["fortschritt"] = read("fortschritt.json")
        if st.get("phase") == "fehler":
            try:
                st["protokoll"] = (self.dir / "protokoll.txt").read_text(encoding="utf-8", errors="replace")[-1500:]
            except OSError:
                pass
        return st


# --- Webserver ---------------------------------------------------------------
class App:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        self.job: Job | None = None
        self.last_request = time.time()
        self.bye_at: float | None = None
        self.lock = threading.Lock()


START_PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Speicheranalyse</title>
<style>:root{--bg:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;--axis:#c3c2b7;--ring:rgba(11,11,11,.10);
--k-user:#1baf7a;--k-cache:#eda100;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink-2:#c3c2b7;--axis:#383835;
--ring:rgba(255,255,255,.10);color-scheme:dark}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,sans-serif;padding:28px 16px}
.wrap{max-width:1180px;margin:0 auto;display:grid;gap:24px}h1{font-size:28px;margin:0}p{color:var(--ink-2);margin:0}</style>
</head><body><div class="wrap"><h1>Speicheranalyse</h1>
<p>Noch kein Bericht vorhanden. Laufwerk wählen und „Neue Analyse“ starten – ein Scan von C: dauert je nach Rechner 1–5 Minuten.</p>
</div></body></html>"""


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Speicheranalyse"

        def log_message(self, fmt, *args):  # keine Konsole unter pythonw
            if sys.stderr:
                super().log_message(fmt, *args)

        # -- Hilfen --
        def _authorized(self, query: dict) -> bool:
            host = self.headers.get("Host", "")
            if host not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
                return False  # schützt vor DNS-Rebinding
            given = self.headers.get("X-Token") or (query.get("t") or [""])[0]
            return hmac.compare_digest(given, app.token)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")  # Schlüssel nicht weitergeben
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _page(self, html: str) -> None:
            tag = f'<script src="/app.js?t={quote(app.token)}"></script>'
            head, sep, tail = html.rpartition("</body>")
            html = head + tag + sep + tail if sep else html + tag
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

        def _touch(self) -> None:
            app.last_request = time.time()
            app.bye_at = None

        # -- GET --
        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if not self._authorized(q):
                return self._send(403, b"Kein Zugriff", "text/plain; charset=utf-8")
            self._touch()
            if url.path == "/":
                rs = reports()
                if rs:
                    self.send_response(302)
                    self.send_header("Location", f"/bericht/{rs[0]['name']}?t={quote(app.token)}")
                    self.end_headers()
                    return
                return self._page(START_PAGE)
            if url.path.startswith("/bericht/"):
                name = url.path.rsplit("/", 1)[-1]
                f = REPORTS / name
                if not REPORT_NAME.match(name) or not f.is_file():
                    return self._send(404, b"Bericht nicht gefunden", "text/plain; charset=utf-8")
                return self._page(f.read_text(encoding="utf-8"))
            if url.path == "/app.js":
                return self._send(200, (HERE / "app_leiste.js").read_bytes(), "text/javascript; charset=utf-8")
            if url.path == "/api/laufwerke":
                return self._json(drives())
            if url.path == "/api/berichte":
                return self._json(reports())
            if url.path == "/api/status":
                with app.lock:
                    return self._json(app.job.status() if app.job else {"phase": "bereit"})
            self._send(404, b"Nicht gefunden", "text/plain; charset=utf-8")

        # -- POST --
        def do_POST(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if not self._authorized(q):
                return self._send(403, b"Kein Zugriff", "text/plain; charset=utf-8")
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            if url.path == "/api/tschuess":  # Tab geschlossen (sendBeacon)
                app.bye_at = time.time()
                return self._json({"ok": True})
            self._touch()
            if url.path == "/api/scan":
                lw = str(body.get("lw", "")).upper()
                if lw not in {d["lw"] for d in drives()}:
                    return self._json({"fehler": "Unbekanntes Laufwerk."}, 400)
                with app.lock:
                    if app.job and app.job.status().get("phase") not in ("fertig", "fehler"):
                        return self._json({"fehler": "Es läuft bereits eine Analyse."}, 409)
                    app.job = Job(lw, bool(body.get("admin")))
                    return self._json(app.job.status())
            if url.path == "/api/beenden":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self._send(404, b"Nicht gefunden", "text/plain; charset=utf-8")

    return Handler


def watchdog(server: ThreadingHTTPServer, app: App) -> None:
    """Beendet den Server, wenn der Tab zu ist oder lange niemand fragt – nie während einer Analyse."""
    while True:
        time.sleep(2)
        busy = app.job is not None and app.job.running()
        now = time.time()
        if busy:
            continue
        if (app.bye_at and now - app.bye_at > BYE_EXIT_S) or now - app.last_request > IDLE_EXIT_S:
            server.shutdown()
            return


def cleanup_jobs(keep: int = 5) -> None:
    """Statusordner alter Läufe aufräumen (nur die von app.py selbst angelegten)."""
    if not JOBS.is_dir():
        return
    olds = sorted((d for d in JOBS.iterdir() if d.is_dir() and re.fullmatch(r"\d{8}_\d{6}", d.name)), reverse=True)
    for d in olds[keep:]:
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Speicheranalyse – lokale App")
    ap.add_argument("--port", type=int, default=0, help="fester Port (Standard: zufällig freier Port)")
    ap.add_argument("--kein-browser", action="store_true", help="Browser nicht automatisch öffnen")
    ap.add_argument("--pipeline", metavar="LW", help=argparse.SUPPRESS)  # intern: Analyse-Lauf
    ap.add_argument("--job", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.pipeline:
        if not re.fullmatch(r"[A-Za-z]:", args.pipeline) or not args.job:
            return 2
        job = Path(args.job).resolve()
        if job.parent != JOBS.resolve():
            return 2  # nur eigene Statusordner beschreiben
        return run_pipeline(args.pipeline.upper(), job)

    SCANS.mkdir(exist_ok=True)
    REPORTS.mkdir(exist_ok=True)
    cleanup_jobs()
    app = App()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(app))
    url = f"http://127.0.0.1:{server.server_port}/?t={app.token}"
    threading.Thread(target=watchdog, args=(server, app), daemon=True).start()
    if sys.stdout:
        print(f"Speicheranalyse läuft: {url}\nBeenden mit Strg+C oder über „Beenden“ im Bericht.", flush=True)
    if not args.kein_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
