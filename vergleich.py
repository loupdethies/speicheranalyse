"""Speicheranalyse – Phase 5: Vergleich zweier Scans.

Vergleicht einen klassifizierten Scan mit einem früheren desselben Laufwerks:
Veränderung je Kategorie, freier Platz und die Ordner, die am stärksten
gewachsen oder geschrumpft sind. report.py bindet das automatisch ein.

Aufruf:
    python vergleich.py                   # neuester Scan gegen den vorherigen
    python vergleich.py ALT.json NEU.json # zwei bestimmte klassifizierte Scans
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
MB = 2**20


def load_abgleich(src: Path) -> dict | None:
    f = src.with_name(src.stem.replace("_klassifiziert", "") + "_abgleich.json")
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def category_bytes(scan: dict, abgleich: dict | None) -> dict[str, int]:
    """Bytes je Kategorie; mit Abgleich wandern die erklärten Posten aus "other" in ihre Kategorie."""
    out = {c["id"]: c["bytes"] for c in scan["kategorien"]}
    if abgleich:
        for p in abgleich["posten"]:
            out[p["k"]] = out.get(p["k"], 0) + p["s"]
        out["other"] = out.get("other", 0) - abgleich["erklaert"]
    return {k: max(v, 0) for k, v in out.items()}


def comparable(a: dict, b: dict) -> bool:
    # Scans vor der MFT-Korrektur (ohne "resident") zählen Kleinstdateien anders
    return a["root"].lower() == b["root"].lower() and "resident" in a["stats"] and "resident" in b["stats"]


def find_previous(src: Path, scan: dict) -> Path | None:
    drive = src.name.split("_")[1]
    for f in sorted(src.parent.glob(f"scan_{drive}_*_klassifiziert.json"), reverse=True):
        if f.name >= src.name:
            continue
        try:
            old = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if comparable(old, scan):
            return f
    return None


def path_sizes(tree: dict, root: str) -> dict[str, dict]:
    """Pfad (klein) -> Größe/Kategorie für alle Ordner und einzeln aufgeführten Dateien."""
    out = {}

    def walk(node, path):
        out[path.lower()] = {"path": path, "s": node["s"], "k": node.get("k", "other")}
        for c in node.get("c", []):
            if c.get("t") != "g":
                walk(c, path + "\\" + c["n"])

    walk(tree, root.rstrip("\\"))
    return out


def folder_changes(old_tree: dict, new_tree: dict, root: str, min_delta: int = 50 * MB, n: int = 20) -> list[dict]:
    """Ordner mit der größten Veränderung – jeweils die tiefste Ebene, die die Änderung noch erklärt."""
    a, b = path_sizes(old_tree, root), path_sizes(new_tree, root)
    delta = {}
    for key in a.keys() | b.keys():
        d = b.get(key, {"s": 0})["s"] - a.get(key, {"s": 0})["s"]
        if d:
            delta[key] = d
    children: dict[str, list[str]] = {}
    for key in delta:
        parent = key.rsplit("\\", 1)[0]
        if parent != key:
            children.setdefault(parent, []).append(key)

    out = []
    for key, d in delta.items():
        if abs(d) < min_delta:
            continue
        # ein Kind erklärt den Großteil -> dort berichten statt hier
        if any(delta[c] * d > 0 and abs(delta[c]) >= 0.7 * abs(d) for c in children.get(key, [])):
            continue
        info = b.get(key) or a.get(key)
        out.append({"path": info["path"], "vorher": a.get(key, {"s": 0})["s"], "nachher": b.get(key, {"s": 0})["s"],
                    "k": info["k"], "neu": key not in a, "weg": key not in b})
    out.sort(key=lambda r: abs(r["nachher"] - r["vorher"]), reverse=True)
    return out[:n]


def compare(old_src: Path, old: dict, new: dict, new_abgleich: dict | None) -> dict:
    old_cats = category_bytes(old, load_abgleich(old_src))
    new_cats = category_bytes(new, new_abgleich)
    return {
        "vorher": {"datei": old_src.name, "scanned_at": old["scanned_at"], "volume": old["volume"]},
        "kategorien": [{"id": c["id"], "vorher": old_cats.get(c["id"], 0), "nachher": new_cats.get(c["id"], 0)}
                       for c in new["kategorien"]],
        "ordner": folder_changes(old["tree"], new["tree"], new["root"]),
    }


def main() -> int:
    if len(sys.argv) == 3:
        old_src, new_src = Path(sys.argv[1]), Path(sys.argv[2])
    else:
        found = sorted((HERE / "scans").glob("*_klassifiziert.json"), key=lambda p: p.stat().st_mtime)
        if not found:
            print("Kein klassifizierter Scan gefunden.")
            return 1
        new_src = found[-1]
        old_src = find_previous(new_src, json.loads(new_src.read_text(encoding="utf-8")))
        if old_src is None:
            print("Kein vergleichbarer früherer Scan desselben Laufwerks gefunden.")
            return 1
    old = json.loads(old_src.read_text(encoding="utf-8"))
    new = json.loads(new_src.read_text(encoding="utf-8"))
    res = compare(old_src, old, new, load_abgleich(new_src))

    gb = 2**30
    print(f"{old['scanned_at']}  ->  {new['scanned_at']}")
    print(f"  Frei: {old['volume']['free'] / gb:.2f} -> {new['volume']['free'] / gb:.2f} GB")
    names = {c["id"]: c["name"] for c in new["kategorien"]}
    for c in res["kategorien"]:
        print(f"  {names[c['id']]:<26} {(c['nachher'] - c['vorher']) / gb:+7.2f} GB")
    print("Ordner:")
    for r in res["ordner"]:
        print(f"  {(r['nachher'] - r['vorher']) / gb:+7.2f} GB  {r['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
