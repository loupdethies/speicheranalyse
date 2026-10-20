"""Speicheranalyse – Phase 1: Scanner.

Durchläuft ein Laufwerk (Standard C:\\) und schreibt einen aggregierten
Ordnerbaum als JSON. Nur Standardbibliothek, Windows.

Größen sind "Größe auf Datenträger": auf Clustergröße aufgerundet, bei
komprimierten/sparse Dateien und Cloud-Platzhaltern (OneDrive) über
GetCompressedFileSizeW ermittelt. Hardlinks werden in den konfigurierten
Pfaden nur einmal gezählt. Junctions und Symlinks werden nicht verfolgt.

Aufruf:
    python scanner.py                 # scannt C:\\
    python scanner.py --root E:\\      # z. B. USB-Stick
    python scanner.py --hardlinks all # Hardlinks überall abgleichen (langsamer)
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import stat
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

# --- Windows-Konstanten ----------------------------------------------------
FILE_ATTRIBUTE_SPARSE_FILE = 0x200
FILE_ATTRIBUTE_COMPRESSED = 0x800
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
NEEDS_REAL_SIZE = (FILE_ATTRIBUTE_SPARSE_FILE | FILE_ATTRIBUTE_COMPRESSED | FILE_ATTRIBUTE_OFFLINE
                   | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # Junction
IO_REPARSE_TAG_SYMLINK = 0xA000000C
SKIP_REPARSE_TAGS = {IO_REPARSE_TAG_MOUNT_POINT, IO_REPARSE_TAG_SYMLINK}

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
_k32.GetCompressedFileSizeW.restype = wintypes.DWORD
_k32.GetDiskFreeSpaceW.argtypes = [wintypes.LPCWSTR] + [ctypes.POINTER(wintypes.DWORD)] * 4
_k32.GetDiskFreeSpaceW.restype = wintypes.BOOL
_k32.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]
_k32.GetVolumeInformationW.restype = wintypes.BOOL
RESIDENT_MAX = 600

OTHER = "<kleinere Einträge>"


def cluster_size(root: str) -> int:
    spc, bps, free, total = (wintypes.DWORD() for _ in range(4))
    if _k32.GetDiskFreeSpaceW(root, ctypes.byref(spc), ctypes.byref(bps), ctypes.byref(free), ctypes.byref(total)):
        return spc.value * bps.value
    return 4096


def volume_info(root: str) -> tuple[str, str]:
    """(Dateisystem, Bezeichnung) des Laufwerks, z. B. ("NTFS", "Windows")."""
    fs, label = ctypes.create_unicode_buffer(32), ctypes.create_unicode_buffer(261)
    vol = root[4:] if root.startswith("\\\\?\\") else root
    if _k32.GetVolumeInformationW(os.path.splitdrive(vol)[0] + "\\", label, 261, None, None, None, fs, 32):
        return fs.value, label.value
    return "", ""


def filesystem(root: str) -> str:
    return volume_info(root)[0]


def compressed_size(path: str) -> int | None:
    high = wintypes.DWORD(0)
    low = _k32.GetCompressedFileSizeW(path, ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error() != 0:
        return None
    return (high.value << 32) + low


def write_json_atomic(path: Path, data: dict) -> None:
    """Erst in eine Nachbardatei schreiben, dann ersetzen – Leser sehen nie eine halbe Datei."""
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # Leser hält die Datei gerade offen – beim nächsten Mal


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


class Scanner:
    def __init__(self, root: str, min_dir: int, min_file: int, hardlink_paths: list[str] | None, top_files: int):
        self.root = root
        self.cluster = cluster_size(root)
        self.fs = filesystem(root)
        # NTFS speichert Dateien bis ca. 600 Byte direkt im MFT-Eintrag (Stichprobe: >90 % bis 599 Byte)
        self.resident_max = RESIDENT_MAX if self.fs == "NTFS" else 0
        self.min_dir = min_dir
        self.min_file = min_file
        # None = überall prüfen; Liste = nur unter diesen Präfixen (kleingeschrieben, Long-Path-Form)
        self.hardlink_paths = hardlink_paths
        self.seen_links: set[tuple[int, int]] = set()
        self.top_n = top_files
        self.top_files: list[tuple[int, str, float]] = []
        self.errors: list[dict] = []
        self.stats = {"files": 0, "dirs": 0, "hardlinks_skipped": 0, "hardlink_bytes_skipped": 0, "reparse_skipped": 0, "resident": 0}
        self._last_progress = 0.0
        self._t0 = time.time()
        self.progress_file: Path | None = None  # Fortschritt für app.py

    # -- Hilfen -----------------------------------------------------------
    def _alloc(self, size: int, attrs: int, path: str) -> int:
        if attrs & NEEDS_REAL_SIZE:
            real = compressed_size(path)
            if real is not None:
                size = real
        elif size <= self.resident_max:
            return 0  # liegt im MFT-Eintrag, belegt keinen eigenen Cluster
        c = self.cluster
        return (size + c - 1) // c * c

    def _check_hardlinks(self, path: str) -> bool:
        if self.hardlink_paths is None:
            return True
        p = path.lower()
        return any(p.startswith(h) for h in self.hardlink_paths)

    def _remember_top(self, alloc: int, path: str, mtime: float) -> None:
        if len(self.top_files) < self.top_n:
            self.top_files.append((alloc, path, mtime))
            if len(self.top_files) == self.top_n:
                self.top_files.sort()
        elif alloc > self.top_files[0][0]:
            self.top_files[0] = (alloc, path, mtime)
            self.top_files.sort()

    def _progress(self, path: str) -> None:
        now = time.time()
        if now - self._last_progress >= 2:
            self._last_progress = now
            done = self.stats["files"]
            shown = path if len(path) < 90 else "…" + path[-89:]
            if sys.stderr:  # unter pythonw gibt es keine Konsole
                sys.stderr.write(f"\r{now - self._t0:6.0f}s  {done:>9,} Dateien  {shown:<91}")
                sys.stderr.flush()
            if self.progress_file:
                write_json_atomic(self.progress_file, {"files": done, "dirs": self.stats["dirs"], "path": path,
                                                       "elapsed": round(now - self._t0)})

    @staticmethod
    def _display(path: str) -> str:
        return path[4:] if path.startswith("\\\\?\\") else path

    # -- Kern -------------------------------------------------------------
    def scan_dir(self, path: str, name: str) -> dict:
        node = {"n": name, "s": 0, "l": 0, "f": 0, "fs": 0, "m": 0.0, "x": {}, "c": []}
        self.stats["dirs"] += 1
        self._progress(self._display(path))
        try:
            it = os.scandir(path)
        except OSError as e:
            node["err"] = e.__class__.__name__
            self.errors.append({"path": self._display(path), "error": f"{e.__class__.__name__}: {e.strerror}"})
            return node

        check_links = self._check_hardlinks(path)
        ext = node["x"]
        own_ext: dict[str, int] = {}  # nur Dateien direkt in diesem Ordner (ohne einzeln aufgeführte große)
        with it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError as e:
                    self.errors.append({"path": self._display(entry.path), "error": f"{e.__class__.__name__}: {e.strerror}"})
                    continue
                tag = getattr(st, "st_reparse_tag", 0)
                if entry.is_dir(follow_symlinks=False) or stat.S_ISDIR(st.st_mode):
                    if tag in SKIP_REPARSE_TAGS:
                        self.stats["reparse_skipped"] += 1
                        continue
                    child = self.scan_dir(entry.path, entry.name)
                    node["c"].append(child)
                    continue
                if tag in SKIP_REPARSE_TAGS:  # Datei-Symlink
                    self.stats["reparse_skipped"] += 1
                    continue

                attrs = st.st_file_attributes
                size = st.st_size
                if check_links:
                    try:
                        full = os.stat(entry.path, follow_symlinks=False)
                        if full.st_nlink > 1:
                            key = (full.st_dev, full.st_ino)
                            if key in self.seen_links:
                                self.stats["hardlinks_skipped"] += 1
                                self.stats["hardlink_bytes_skipped"] += self._alloc(size, attrs, entry.path)
                                continue
                            self.seen_links.add(key)
                    except OSError:
                        pass
                alloc = self._alloc(size, attrs, entry.path)
                self.stats["files"] += 1
                if size and not alloc:
                    self.stats["resident"] += 1
                node["f"] += 1
                node["s"] += alloc
                node["l"] += size
                node["fs"] += alloc
                if st.st_mtime > node["m"]:
                    node["m"] = st.st_mtime
                e = os.path.splitext(entry.name)[1].lower()[:12]
                ext[e] = ext.get(e, 0) + alloc
                if alloc >= self.min_file:
                    node["c"].append({"n": entry.name, "t": "f", "s": alloc, "l": size, "f": 1, "m": st.st_mtime,
                                      "x": {e: alloc}})
                    node["fs"] -= alloc
                elif alloc:
                    own_ext[e] = own_ext.get(e, 0) + alloc
                self._remember_top(alloc, self._display(entry.path), st.st_mtime)

        # Kinder aufsummieren, Erweiterungen zusammenführen, kleine Kinder bündeln
        keep, small = [], {"n": OTHER, "t": "g", "s": 0, "l": 0, "f": 0, "m": 0.0, "x": {}, "k": 0}
        for ch in node["c"]:
            if ch.get("t") != "f":
                node["s"] += ch["s"]
                node["l"] += ch["l"]
                node["f"] += ch["f"]
                if ch["m"] > node["m"]:
                    node["m"] = ch["m"]
                for k, v in ch["x"].items():
                    ext[k] = ext.get(k, 0) + v
            if ch["s"] >= self.min_dir or ch.get("t") == "f":
                keep.append(ch)
            else:
                small["s"] += ch["s"]
                small["l"] += ch["l"]
                small["f"] += ch["f"]
                small["k"] += 1
                small["m"] = max(small["m"], ch["m"])
                for k, v in ch["x"].items():
                    small["x"][k] = small["x"].get(k, 0) + v
        if small["k"]:
            small["x"] = self._top_ext(small["x"], 80)
            keep.append(small)
        keep.sort(key=lambda c: c["s"], reverse=True)
        node["c"] = keep
        node["x"] = self._top_ext(ext)
        if own_ext:
            node["xo"] = self._top_ext(own_ext, 80)
        if not node["c"]:
            del node["c"]
        return node

    @staticmethod
    def _top_ext(ext: dict, n: int = 40) -> dict:
        if len(ext) <= n:
            return ext
        items = sorted(ext.items(), key=lambda kv: kv[1], reverse=True)
        out = dict(items[:n])
        out["*"] = sum(v for _, v in items[n:])
        return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Speicheranalyse – Scanner (Phase 1)")
    ap.add_argument("--root", default="C:\\", help="Laufwerk oder Ordner, Standard C:\\")
    ap.add_argument("--out", default=None, help="Ausgabedatei (Standard: scans/scan_<LW>_<Zeit>.json)")
    ap.add_argument("--min-dir-mb", type=float, default=10, help="Ordner kleiner als das werden gebündelt")
    ap.add_argument("--min-file-mb", type=float, default=100, help="Dateien ab dieser Größe einzeln aufführen")
    ap.add_argument("--hardlinks", choices=["windows", "all", "off"], default="windows",
                    help="Hardlink-Abgleich: nur C:\\Windows + uv-Cache (Standard), überall oder aus")
    ap.add_argument("--top-files", type=int, default=200, help="Anzahl größter Dateien in der Liste")
    ap.add_argument("--progress", help="Fortschritt laufend in diese JSON-Datei schreiben (für app.py)")
    args = ap.parse_args()

    # "C:\" aus einer .cmd kommt als C:" an (Backslash maskiert das Anführungszeichen)
    root = args.root.strip().strip('"')
    if len(root) == 2 and root[1] == ":":
        root += "\\"  # sonst löst abspath("C:") auf das aktuelle Verzeichnis auf
    root = os.path.abspath(root)
    if not root.endswith("\\"):
        root += "\\"
    long_root = "\\\\?\\" + root
    drive = os.path.splitdrive(root)[0].rstrip(":") or "X"

    if args.hardlinks == "all":
        hl = None
    elif args.hardlinks == "off":
        hl = []
    else:
        hl = [("\\\\?\\" + p).lower() for p in (os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), ""),
                                                  os.path.join(os.environ.get("LOCALAPPDATA", ""), "uv", ""))
              if p.lower().startswith(root.lower())]

    sc = Scanner(long_root, int(args.min_dir_mb * 2**20), int(args.min_file_mb * 2**20), hl, args.top_files)
    sc.progress_file = Path(args.progress) if args.progress else None
    label = volume_info(root)[1]
    system_drive = os.environ.get("SystemDrive", "C:").upper() == os.path.splitdrive(root)[0].upper()
    usage = shutil.disk_usage(root)
    t0 = time.time()
    tree = sc.scan_dir(long_root, root)
    dur = time.time() - t0
    if sys.stderr:
        sys.stderr.write("\n")

    result = {
        "version": 1,
        "root": root,
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "duration_s": round(dur, 1),
        "admin": is_admin(),
        "volume": {"total": usage.total, "used": usage.used, "free": usage.free, "cluster": sc.cluster, "fs": sc.fs,
                   "label": label, "system": system_drive},
        "scanned_bytes": tree["s"],
        "unaccounted_bytes": usage.used - tree["s"],
        "options": {"min_dir": sc.min_dir, "min_file": sc.min_file, "hardlinks": args.hardlinks},
        "stats": sc.stats,
        "errors": sc.errors[:500],
        "error_count": len(sc.errors),
        "top_files": [{"path": p, "s": s, "m": m} for s, p, m in sorted(sc.top_files, reverse=True)],
        "tree": tree,
    }
    out = Path(args.out) if args.out else Path(__file__).parent / "scans" / f"scan_{drive}_{datetime.now():%Y%m%d_%H%M}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    gb = 2**30
    print(f"Fertig in {dur:.0f} s{'  (ohne Admin-Rechte)' if not result['admin'] else ''}")
    print(f"  Dateien: {sc.stats['files']:,}   Ordner: {sc.stats['dirs']:,}   Fehler/kein Zugriff: {len(sc.errors):,}")
    print(f"  Hardlinks übersprungen: {sc.stats['hardlinks_skipped']:,} ({sc.stats['hardlink_bytes_skipped'] / gb:.2f} GB)")
    print(f"  Junctions/Symlinks nicht verfolgt: {sc.stats['reparse_skipped']:,}")
    print(f"  Laufwerk belegt: {usage.used / gb:7.2f} GB")
    print(f"  Gescannt:        {tree['s'] / gb:7.2f} GB")
    print(f"  Nicht erfasst:   {result['unaccounted_bytes'] / gb:7.2f} GB")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
