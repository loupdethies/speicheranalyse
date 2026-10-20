"""Speicheranalyse – Phase 2: Klassifizierung.

Liest einen Scan (scanner.py) und die Regeln aus kategorien.json, ordnet jedem
Knoten eine Kategorie zu und schreibt <scan>_klassifiziert.json mit
Kategoriesummen, Dateitypen der eigenen Dateien und dem markierten Baum.

Aufruf:
    python classify.py                    # neuester Scan in scans/
    python classify.py scans/scan_C_….json
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent


def expand(p: str) -> str:
    # %TEMP% u. ä. auflösen, kurze 8.3-Namen vermeiden, ohne abschließenden Backslash
    p = os.path.expandvars(p)
    if "*" not in p and "?" not in p and os.path.exists(p):
        p = os.path.realpath(p)
    return p.rstrip("\\").lower()


class Rules:
    def __init__(self, cfg: dict):
        self.cats = cfg["kategorien"]
        self.default = cfg["standard"]
        self.exact: dict[str, str] = {}
        self.patterns: list[tuple[str, str, re.Pattern]] = []
        for pat, cat in cfg["pfadregeln"]:
            p = expand(pat)
            if "*" in p or "?" in p:
                # * und ? gelten nur innerhalb eines Pfadsegments, sonst würde
                # C:\Users\* auch jeden tieferen Ordner treffen
                rx = "".join("[^\\\\]*" if ch == "*" else "[^\\\\]" if ch == "?" else re.escape(ch) for ch in p)
                self.patterns.append((p, cat, re.compile(rx)))
            else:
                self.exact.setdefault(p, cat)  # erste Regel gewinnt
        self.order = {expand(p): i for i, (p, _) in enumerate(cfg["pfadregeln"])}
        self.name_rules = []
        for r in cfg["namensregeln"]:
            within = [expand(u) for u in r.get("unterhalb", [])]
            not_in = set(r.get("nicht_in", []))
            self.name_rules.append(({n.lower() for n in r["namen"]}, r["kategorie"], within, not_in))
        self.types: dict[str, str] = {}
        self.type_order = [k for k in cfg["dateitypen"] if not k.startswith("_")]
        for t in self.type_order:
            for e in cfg["dateitypen"][t]:
                self.types.setdefault("." + e.lower(), t)

    def match(self, path_l: str, name_l: str, parent_cat: str) -> str | None:
        hits = []
        if path_l in self.exact:
            hits.append((self.order.get(path_l, 0), self.exact[path_l]))
        for pat, cat, rx in self.patterns:
            if rx.fullmatch(path_l):
                hits.append((self.order.get(pat, 0), cat))
        if hits:
            return min(hits)[1]
        for names, cat, within, not_in in self.name_rules:
            if parent_cat in not_in:
                continue
            if name_l in names and (not within or any(path_l.startswith(w + "\\") for w in within)):
                return cat
        return None


def classify(node: dict, path: str, parent_cat: str, rules: Rules, totals: dict, user_ext: dict, examples: dict) -> None:
    path_l = path.rstrip("\\").lower()
    if node.get("t") == "g":
        cat = parent_cat  # gebündelte kleine Einträge erben
    else:
        cat = rules.match(path_l, node["n"].lower(), parent_cat) or parent_cat
    node["k"] = cat

    children = node.get("c", [])
    own = node["s"] - sum(c["s"] for c in children)
    totals[cat] = totals.get(cat, 0) + own
    if own > 0:
        examples.setdefault(cat, []).append((own, path))

    if cat == "user" and own > 0:
        if "xo" in node or not children:
            ext = node.get("xo", node.get("x", {}))  # vom Scanner direkt erfasst
        else:
            # ältere Scans: Teilbaum minus Kinder (Näherung, da Endungslisten gekürzt sind)
            ext = dict(node.get("x", {}))
            for c in children:
                for k, v in c.get("x", {}).items():
                    ext[k] = ext.get(k, 0) - v
        counted = 0
        for k, v in ext.items():
            if v <= 0:
                continue
            t = rules.types.get(k, "Sonstige") if k != "*" else "Sonstige"
            user_ext[t] = user_ext.get(t, 0) + v
            counted += v
        if own > counted:
            user_ext["Sonstige"] = user_ext.get("Sonstige", 0) + own - counted

    for c in children:
        child_path = path.rstrip("\\") + "\\" + c["n"] if c.get("t") != "g" else path
        classify(c, child_path, cat, rules, totals, user_ext, examples)


def main() -> int:
    if len(sys.argv) > 1:
        scan_file = Path(sys.argv[1])
    else:
        scans = sorted((p for p in (HERE / "scans").glob("scan_*.json") if not p.stem.endswith(("_klassifiziert", "_abgleich"))),
                       key=lambda p: p.stat().st_mtime)
        if not scans:
            print("Kein Scan gefunden. Zuerst scanner.py ausführen.")
            return 1
        scan_file = scans[-1]
    scan = json.loads(scan_file.read_text(encoding="utf-8"))
    cfg = json.loads((HERE / "kategorien.json").read_text(encoding="utf-8"))
    rules = Rules(cfg)

    totals: dict[str, int] = {}
    user_ext: dict[str, int] = {}
    examples: dict[str, list] = {}
    tree = scan["tree"]
    system = scan["volume"].get("system")
    if system is None:  # ältere Scans
        system = scan["root"][:2].upper() == os.environ.get("SystemDrive", "C:").upper()
    # auf USB-Sticks und Datenplatten ist fast alles eigene Datei
    start = rules.default if system else cfg.get("standard_andere_laufwerke", rules.default)
    classify(tree, scan["root"], start, rules, totals, user_ext, examples)

    # Nicht lesbarer Rest: zählt als "kein Zugriff"
    unacc = max(0, scan["unaccounted_bytes"])
    totals[rules.default] = totals.get(rules.default, 0) + unacc

    vol = scan["volume"]
    cats_out = []
    for c in rules.cats:
        cats_out.append({**c, "bytes": totals.get(c["id"], 0),
                         "top": [p for _, p in sorted(examples.get(c["id"], []), reverse=True)[:5]]})
    types_out = [{"typ": t, "bytes": user_ext.get(t, 0)} for t in rules.type_order + ["Sonstige"]]

    result = {**{k: v for k, v in scan.items() if k != "tree"},
              "kategorien": cats_out, "kein_zugriff_bytes": unacc, "dateitypen": types_out, "tree": tree}
    out = scan_file.with_name(scan_file.stem + "_klassifiziert.json")
    out.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    gb = 2**30
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"Laufwerk {scan['root']}  belegt {vol['used'] / gb:.1f} GB von {vol['total'] / gb:.1f} GB\n")
    for c in cats_out:
        print(f"  {c['name']:<26} {c['bytes'] / gb:7.2f} GB   {c['bytes'] / vol['used'] * 100:5.1f} %")
    print(f"  {'Frei':<26} {vol['free'] / gb:7.2f} GB")
    print(f"\n  Summe Kategorien: {sum(totals.values()) / gb:.2f} GB (belegt: {vol['used'] / gb:.2f} GB)")
    print("\nEigene Dateien nach Typ:")
    for t in types_out:
        print(f"  {t['typ']:<22} {t['bytes'] / gb:7.2f} GB")
    print(f"\n-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
