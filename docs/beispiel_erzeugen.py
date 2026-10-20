"""Erzeugt einen Beispielbericht mit erfundenen Daten (docs/beispiel.html).

Für README-Screenshots und zum Ausprobieren, ohne einen eigenen Rechner zu
scannen. Läuft durch dieselben Schritte wie eine echte Analyse (classify.py,
report.py), nur mit einem ausgedachten Laufwerk eines Benutzers "Max".

Aufruf:
    python docs/beispiel_erzeugen.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
GB, MB = 2**30, 2**20
NOW = time.time()
DAY = 86400


def folder(name, size_gb=0.0, ext=None, files=0, age_days=5, children=()):
    """Ordner-Knoten im Format von scanner.py; eigene Dateien über ext {Endung: Anteil}."""
    own = int(size_gb * GB)
    ext = {k: int(own * v) for k, v in (ext or {"": 1}).items()} if own else {}
    node = {"n": name, "s": own, "l": own, "f": files or max(1, own // (2 * MB)), "fs": own,
            "m": NOW - age_days * DAY, "x": dict(ext), "c": list(children)}
    if own:
        node["xo"] = dict(ext)
    for c in children:
        node["s"] += c["s"]
        node["l"] += c["l"]
        node["f"] += c["f"]
        node["m"] = max(node["m"], c["m"])
        for k, v in c["x"].items():
            node["x"][k] = node["x"].get(k, 0) + v
    node["c"].sort(key=lambda c: c["s"], reverse=True)
    if not node["c"]:
        del node["c"]
    return node


def bigfile(name, size_gb, age_days=30):
    s = int(size_gb * GB)
    return {"n": name, "t": "f", "s": s, "l": s, "f": 1, "m": NOW - age_days * DAY, "x": {os.path.splitext(name)[1].lower(): s}}


def demo_tree():
    sw = {".dll": .6, ".exe": .2, ".dat": .1, ".pak": .1}
    local = folder("Local", children=[
        folder("Google", children=[folder("Chrome", children=[folder("User Data", children=[folder("Default", 0.3, {".db": 1}, children=[
            folder("Cache", 1.1, {"": 1}, age_days=0), folder("Code Cache", 0.4, {"": 1}, age_days=0),
            folder("Service Worker", 2.1, {"": 1}, age_days=0)])])])]),
        folder("Temp", 1.6, {".tmp": .6, ".log": .2, ".msi": .2}, age_days=0),
        folder("Spotify", 2.4, {".file": 1}, age_days=1),
        folder("npm-cache", 0.7, {"": 1}, age_days=40),
        folder("Microsoft", 2.9, {".dat": .5, ".db": .5}, age_days=0, children=[
            folder("Teams", 1.3, {"": 1}, age_days=2), folder("OneDrive", 0.4, {".exe": 1})]),
        folder("Programs", 3.4, sw, age_days=12),
        folder("CrashDumps", 0.6, {".dmp": 1}, age_days=80),
    ])
    user = folder("Max", 0.05, {".ini": 1}, children=[
        folder("AppData", children=[local, folder("Roaming", 2.2, {".json": .3, ".db": .7}, age_days=1)]),
        folder("Bilder", 17.5, {".jpg": .6, ".heic": .3, ".png": .1}, age_days=3, children=[
            folder("Urlaub 2025", 6.3, {".jpg": .7, ".mp4": .3}, age_days=150)]),
        folder("Videos", 8.6, {".mp4": .8, ".mov": .2}, age_days=20),
        folder("Musik", 4.1, {".mp3": .7, ".flac": .3}, age_days=200),
        folder("Dokumente", 3.3, {".pdf": .5, ".docx": .2, ".xlsx": .2, ".pptx": .1}, age_days=1, children=[
            folder("Elektronik", 1.4, {".kicad_pcb": .3, ".step": .4, ".gbr": .1, ".pdf": .2}, age_days=9)]),
        folder("Downloads", 3.1, {".exe": .4, ".msi": .2, ".zip": .3, ".pdf": .1}, age_days=2, children=[
            bigfile("Ubuntu-24.04-desktop-amd64.iso", 5.8, 210), bigfile("Treiberpaket.zip", 0.9, 120)]),
        folder("Desktop", 0.3, {".lnk": .1, ".pdf": .9}, age_days=0),
        folder("projects", 0.4, {".py": .5, ".js": .5}, age_days=4, children=[
            folder("webshop", 0.1, {".ts": 1}, age_days=200, children=[folder("node_modules", 1.3, {".js": 1}, age_days=200)]),
            folder("datenlogger", 0.2, {".py": 1}, age_days=3, children=[folder(".venv", 0.7, {".pyd": .5, ".py": .5}, age_days=3)])]),
        folder(".cache", 0.6, {"": 1}, age_days=30),
    ])
    windows = folder("Windows", 2.0, {".dll": 1}, age_days=1, children=[
        folder("System32", 8.9, {".dll": .7, ".exe": .1, ".mui": .2}), folder("WinSxS", 7.6, {".dll": .8, ".manifest": .2}),
        folder("Installer", 4.8, {".msi": .5, ".msp": .5}, age_days=60), folder("SoftwareDistribution", children=[
            folder("Download", 1.3, {".cab": 1}, age_days=7)]),
        folder("Temp", 0.5, {".tmp": 1}, age_days=0), folder("Panther", 0.3, {".log": 1}, age_days=90),
        folder("SysWOW64", 1.9, {".dll": 1})])
    pf = folder("Program Files", 0.2, sw, children=[
        folder("Microsoft Office", 4.6, sw, age_days=20), folder("KiCad", 5.2, sw, age_days=45),
        folder("Steam", 1.1, sw, age_days=2, children=[folder("steamapps", 18.4, {".pak": .8, ".bin": .2}, age_days=4)]),
        folder("Google", 0.7, sw), folder("WindowsApps", 3.8, sw)])
    root = folder("C:\\", 0, children=[
        windows, pf, folder("Program Files (x86)", 4.4, sw, age_days=30), folder("ProgramData", 5.3, {".dat": .5, ".db": .5}),
        folder("Users", children=[user, folder("Public", 0.1, {".lnk": 1})]),
        folder("$Recycle.Bin", 0.9, {".jpg": .5, ".zip": .5}, age_days=10),
        bigfile("pagefile.sys", 12.0, 0), bigfile("hiberfil.sys", 6.4, 0), bigfile("swapfile.sys", 0.25, 0)])
    root["c"].append({"n": "System Volume Information", "s": 0, "l": 0, "f": 0, "fs": 0, "m": 0, "x": {}, "err": "PermissionError"})
    return root


def walk_files(node, path, out):
    for c in node.get("c", []):
        p = path.rstrip("\\") + "\\" + c["n"]
        if c.get("t") == "f":
            out.append({"path": p, "s": c["s"], "m": c["m"]})
        else:
            walk_files(c, p, out)


def main() -> int:
    tree = demo_tree()
    top = []
    walk_files(tree, "C:\\", top)
    unread = int(3.1 * GB)
    total = 256_060_514_304
    used = tree["s"] + unread
    scan = {
        "version": 1, "root": "C:\\", "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "duration_s": 94.0, "admin": False,
        "volume": {"total": total, "used": used, "free": total - used, "cluster": 4096, "fs": "NTFS",
                   "label": "Beispiel", "system": True},
        "scanned_bytes": tree["s"], "unaccounted_bytes": unread,
        "options": {"min_dir": 10 * MB, "min_file": 100 * MB, "hardlinks": "windows"},
        "stats": {"files": 612_408, "dirs": 148_211, "hardlinks_skipped": 41_002, "hardlink_bytes_skipped": int(6.8 * GB),
                  "reparse_skipped": 52, "resident": 120_377},
        "errors": [{"path": "C:\\System Volume Information", "error": "PermissionError: Zugriff verweigert"}],
        "error_count": 1, "top_files": sorted(top, key=lambda f: f["s"], reverse=True), "tree": tree,
    }
    # Pfadregeln nutzen %USERPROFILE% usw. – für das Beispiel auf den erfundenen Benutzer umbiegen
    env = dict(os.environ, USERPROFILE="C:\\Users\\Max", LOCALAPPDATA="C:\\Users\\Max\\AppData\\Local",
               APPDATA="C:\\Users\\Max\\AppData\\Roaming", TEMP="C:\\Users\\Max\\AppData\\Local\\Temp",
               TMP="C:\\Users\\Max\\AppData\\Local\\Temp", SystemDrive="C:")
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "scan_C_20260101_1200.json"
        raw.write_text(json.dumps(scan), encoding="utf-8")
        klass = raw.with_name(raw.stem + "_klassifiziert.json")
        out = DOCS / "beispiel.html"
        for cmd in ([sys.executable, "-I", str(ROOT / "classify.py"), str(raw)],
                    [sys.executable, "-I", str(ROOT / "report.py"), str(klass), "--out", str(out)]):
            subprocess.run(cmd, env=env, check=True, stdout=subprocess.DEVNULL)
    print(f"Beispielbericht: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
