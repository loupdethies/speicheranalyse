"""Speicheranalyse – Phase 5: Aufräumliste.

Sucht im klassifizierten Baum nach Aufräumkandidaten (Regeln in aufraeumen.json):
bekannte Caches/Temp-Ordner mit Anleitung, sonstige App-Caches, lange
unveränderte node_modules/.venv und alte große Dateien im Download-Ordner.
Löscht nichts – report.py zeigt die Liste im Bericht.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from classify import expand  # noqa: E402

MB = 2**20
DAY = 86400


def _compile(pattern: str) -> re.Pattern:
    p = expand(pattern)
    return re.compile("".join("[^\\\\]*" if ch == "*" else "[^\\\\]" if ch == "?" else re.escape(ch) for ch in p))


def app_name(path: str) -> str:
    """Grober App-Name aus einem Pfad unter AppData (Local/Roaming)."""
    parts = path.split("\\")
    low = [p.lower() for p in parts]
    for i, p in enumerate(low[:-1]):
        if p == "appdata" and low[i + 1] in ("local", "roaming", "locallow"):
            rest = parts[i + 2:]
            if not rest:
                break
            if rest[0].lower() == "packages" and len(rest) > 1:
                return rest[1].split("_")[0]
            if rest[0].lower() in ("microsoft", "google", "mozilla") and len(rest) > 1:
                return f"{rest[0]} {rest[1]}"
            return rest[0]
    return parts[1] if len(parts) > 1 else path


def load_rules() -> dict:
    cfg = json.loads((HERE / "aufraeumen.json").read_text(encoding="utf-8"))
    for r in cfg["regeln"]:
        r["rx"] = _compile(r["muster"])
    return cfg


def candidates(scan: dict, cfg: dict | None = None, now: float | None = None) -> dict:
    cfg = cfg or load_rules()
    gen = cfg["allgemein"]
    now = now or time.time()
    dev_names = {n.lower() for n in gen["entwicklung_namen"]}
    dev_age = gen["entwicklung_tage"] * DAY
    items = []

    def walk(node, path, parent_k):
        if node.get("t") == "g":
            return
        pl = path.lower()
        k = node.get("k")
        rule = next((r for r in cfg["regeln"] if r["rx"].fullmatch(pl)), None)
        if rule:
            if node["s"] > 0:
                items.append({"titel": rule["titel"], "path": path, "s": node["s"], "m": node.get("m", 0),
                              "stufe": rule["stufe"], "wie": rule["wie"]})
            return
        if node.get("t") != "f":
            if k == "cache" and parent_k != "cache":
                if node["s"] > 0:
                    app = app_name(path)
                    g = app_caches.setdefault(app, {"titel": f"{gen['cache_titel']}: {app}", "paths": [], "s": 0, "m": 0,
                                                    "stufe": "sicher", "wie": gen["cache_wie"]})
                    g["paths"].append(path)
                    g["s"] += node["s"]
                    g["m"] = max(g["m"], node.get("m", 0))
                return
            if node["n"].lower() in dev_names and k == "dev" and node["s"] >= gen["entwicklung_min_mb"] * MB:
                # npm stempelt entpackte Dateien auf 1985 -> zusätzlich Änderungsdatum des Ordners selbst
                try:
                    m = max(node.get("m", 0), os.stat(path).st_mtime)
                except OSError:
                    m = node.get("m", 0)
                if m and now - m > dev_age:
                    items.append({"titel": f"Lange unverändert: {node['n']}", "path": path, "s": node["s"], "m": m,
                                  "stufe": "prüfen", "wie": gen["entwicklung_wie"].format(tage=gen["entwicklung_tage"])})
                    return
        for c in node.get("c", []):
            walk(c, path.rstrip("\\") + "\\" + c["n"], k)

    app_caches: dict[str, dict] = {}
    walk(scan["tree"], scan["root"].rstrip("\\"), None)
    for g in app_caches.values():
        if g["s"] >= gen["cache_min_mb"] * MB:
            paths = g.pop("paths")
            g["path"] = os.path.commonpath(paths) if len(paths) > 1 else paths[0]
            g["n"] = len(paths)
            items.append(g)

    dl = expand(gen["downloads"]) + "\\"
    dl_age = gen["downloads_tage"] * DAY
    for f in scan["top_files"]:
        if (f["path"].lower().startswith(dl) and f["s"] >= gen["downloads_min_mb"] * MB
                and f["m"] and now - f["m"] > dl_age):
            items.append({"titel": "Alter Download", "path": f["path"], "s": f["s"], "m": f["m"], "stufe": "prüfen",
                          "wie": gen["downloads_wie"].format(tage=gen["downloads_tage"])})

    items.sort(key=lambda r: r["s"], reverse=True)
    summe = {}
    for r in items:
        summe[r["stufe"]] = summe.get(r["stufe"], 0) + r["s"]
    return {"items": items[:30], "summe": summe, "anzahl": len(items)}


if __name__ == "__main__":
    found = sorted((HERE / "scans").glob("*_klassifiziert.json"), key=lambda p: p.stat().st_mtime)
    res = candidates(json.loads(found[-1].read_text(encoding="utf-8")))
    sys.stdout.reconfigure(encoding="utf-8")
    for r in res["items"]:
        print(f"{r['s'] / 2**30:6.2f} GB  {r['stufe']:<7} {r['titel']:<40} {r['path']}")
    print({k: f"{v / 2**30:.2f} GB" for k, v in res["summe"].items()})
