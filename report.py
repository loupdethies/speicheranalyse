"""Speicheranalyse – Phase 3: HTML-Bericht.

Liest einen klassifizierten Scan (classify.py) und schreibt eine eigenständige
HTML-Datei (D3 eingebettet, funktioniert offline) mit Laufwerksbalken,
Treemap (nach Kategorie oder nach Ordner), Dateitypen, Aufräumliste und Tabelle.

Aufruf:
    python report.py                      # neuester klassifizierter Scan
    python report.py scans/…_klassifiziert.json
    python report.py --open               # danach im Browser öffnen
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from aufraeumen import candidates  # noqa: E402
from vergleich import compare, find_previous  # noqa: E402
OTHER = "<kleinere Einträge>"
SYSTEM_FILES = {"pagefile.sys", "hiberfil.sys", "swapfile.sys"}


def own(node: dict) -> int:
    return node["s"] - sum(c["s"] for c in node.get("c", []))


def category_roots(tree: dict, root_path: str) -> dict[str, list[dict]]:
    """Zusammenhängende Teilbäume gleicher Kategorie mit ihren eigenen Bytes."""
    roots: dict[str, list[dict]] = {}

    def walk(node, path, names, current):
        k = node["k"]
        if current is None or current["k"] != k:
            current = {"k": k, "path": path, "p": names, "s": 0, "m": node.get("m", 0)}
            roots.setdefault(k, []).append(current)
        current["s"] += own(node)
        for c in node.get("c", []):
            cp = path if c.get("t") == "g" else path.rstrip("\\") + "\\" + c["n"]
            walk(c, cp if c.get("t") != "g" else path + "\\" + OTHER, names + [c["n"]], current)

    walk(tree, root_path, [], None)
    for k in roots:
        roots[k] = sorted((r for r in roots[k] if r["s"] > 0), key=lambda r: r["s"], reverse=True)
    return roots


def cloud_only(tree: dict, root_path: str, min_gap: int = 2**30) -> list[dict]:
    """Ordner, deren nominelle Größe viel größer ist als der Platz auf der Platte (OneDrive-Platzhalter)."""
    out = []

    def walk(node, path, depth):
        if node.get("t") == "f":
            return
        gap = node["l"] - node["s"]
        if gap >= min_gap and "onedrive" in node["n"].lower():
            out.append({"path": path, "nominell": node["l"], "lokal": node["s"]})
            return
        if depth < 4:
            for c in node.get("c", []):
                if c.get("t") != "g":
                    walk(c, path.rstrip("\\") + "\\" + c["n"], depth + 1)

    walk(tree, root_path, 0)
    return out


def file_category(tree: dict, path_names: list[str]) -> str:
    node = tree
    for n in path_names:
        nxt = next((c for c in node.get("c", []) if c["n"].lower() == n.lower()), None)
        if nxt is None:
            break
        node = nxt
    return node["k"]


def annotate_mix(node: dict) -> dict:
    """Bytes pro Kategorie im Teilbaum ("kb"), damit die Treemap Mischordner richtig einfärbt."""
    mix = {node["k"]: own(node)}
    for c in node.get("c", []):
        for k, v in annotate_mix(c).items():
            mix[k] = mix.get(k, 0) + v
    mix = {k: v for k, v in mix.items() if v > 0}
    if len(mix) > 1:
        node["kb"] = mix
    return mix


def build_data(scan: dict, abgleich: dict | None = None) -> dict:
    tree = scan["tree"]
    root = scan["root"]
    annotate_mix(tree)
    roots = category_roots(tree, root)
    cats = []
    for c in scan["kategorien"]:
        lst = roots.get(c["id"], [])
        top = [{"path": r["path"], "p": r["p"], "s": r["s"], "m": r["m"]} for r in lst[:40]]
        rest = sum(r["s"] for r in lst[40:])
        cat_bytes = c["bytes"]
        if abgleich:
            # erklärte Posten der Lücke in ihre Kategorie; nur der Rest bleibt "nicht lesbar"
            for p in abgleich["posten"]:
                if p["k"] == c["id"]:
                    top.append({"path": p["name"], "p": None, "s": p["s"], "m": 0})
                    cat_bytes += p["s"]
            if c["id"] == "other":
                cat_bytes -= abgleich["erklaert"]
        unread = max(abgleich["rest"], 0) if abgleich else scan["kein_zugriff_bytes"]
        if c["id"] == "other" and unread > 0:
            top.append({"path": "Ohne Admin-Rechte nicht lesbar", "p": None, "s": unread, "m": 0})
        if rest:
            top.append({"path": f"{len(lst) - 40} weitere Bereiche", "p": None, "s": rest, "m": 0})
        top.sort(key=lambda r: r["s"], reverse=True)
        cats.append({"id": c["id"], "name": c["name"], "hell": c["hell"], "dunkel": c["dunkel"],
                     "bytes": max(cat_bytes, 0), "areas": top})

    root_l = root.rstrip("\\")
    big_files = []
    for f in scan["top_files"]:
        name = f["path"].rsplit("\\", 1)[-1].lower()
        if name in SYSTEM_FILES or f["s"] < 100 * 2**20:
            continue
        rel = f["path"][len(root_l):].strip("\\").split("\\")
        big_files.append({**f, "k": file_category(tree, rel)})
    return {
        "root": root, "scanned_at": scan["scanned_at"], "duration_s": scan["duration_s"], "admin": scan["admin"],
        "volume": scan["volume"],
        "unaccounted": max(abgleich["rest"], 0) if abgleich else scan["kein_zugriff_bytes"],
        "stats": scan["stats"], "error_count": scan["error_count"],
        "cats": cats, "types": scan["dateitypen"], "cloud": cloud_only(tree, root),
        "big_files": big_files[:30], "cleanup": candidates(scan), "tree": strip_ext(tree), "abgleich": abgleich,
    }


def strip_ext(node: dict) -> dict:
    """Endungslisten braucht der Bericht nicht – spart rund die Hälfte der Dateigröße."""
    node.pop("x", None)
    node.pop("xo", None)
    for c in node.get("c", []):
        strip_ext(c)
    return node


def main() -> int:
    ap = argparse.ArgumentParser(description="Speicheranalyse – HTML-Bericht (Phase 3)")
    ap.add_argument("scan", nargs="?", help="klassifizierte Scan-Datei")
    ap.add_argument("--out", help="Ausgabedatei (Standard: berichte/bericht_<LW>_<Zeit>.html)")
    ap.add_argument("--open", action="store_true", help="Bericht danach im Browser öffnen")
    ap.add_argument("--vergleich", help="mit diesem klassifizierten Scan vergleichen (Standard: vorheriger Scan)")
    ap.add_argument("--kein-vergleich", action="store_true", help="keinen Vergleich einbinden")
    args = ap.parse_args()

    if args.scan:
        src = Path(args.scan)
    else:
        found = sorted((HERE / "scans").glob("*_klassifiziert.json"), key=lambda p: p.stat().st_mtime)
        if not found:
            print("Kein klassifizierter Scan gefunden. Zuerst scanner.py und classify.py ausführen.")
            return 1
        src = found[-1]
    scan = json.loads(src.read_text(encoding="utf-8"))
    abg_file = src.with_name(src.stem.replace("_klassifiziert", "") + "_abgleich.json")
    abgleich = json.loads(abg_file.read_text(encoding="utf-8")) if abg_file.exists() else None
    data = build_data(scan, abgleich)
    if not args.kein_vergleich:
        old_src = Path(args.vergleich) if args.vergleich else find_previous(src, scan)
        if old_src:
            data["vergleich"] = compare(old_src, json.loads(old_src.read_text(encoding="utf-8")), scan, abgleich)
            print(f"Vergleich mit {old_src.name}")

    template = (HERE / "bericht_vorlage.html").read_text(encoding="utf-8")
    d3 = (HERE / "vendor" / "d3.min.js").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = template.replace("/*__D3__*/", d3).replace('"__DATA__"', payload)

    stem = src.stem.replace("scan_", "bericht_").replace("_klassifiziert", "")
    out = Path(args.out) if args.out else HERE / "berichte" / f"{stem}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Bericht: {out}  ({out.stat().st_size / 2**20:.1f} MB)")
    if args.open:
        os.startfile(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
