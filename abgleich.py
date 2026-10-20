"""Speicheranalyse – Phase 4: Abgleich mit den Windows-Werten.

Vergleicht "belegt" laut Windows mit der Summe des Scans und zerlegt die
Lücke in erklärbare Posten:

  * NTFS-Verwaltungsdaten ($MFT, $Bitmap; mit Admin auch das USN-Journal)
  * App-Pakete in C:\\Program Files\\WindowsApps (Ordner selbst gesperrt,
    die Paketordner des angemeldeten Benutzers sind aber lesbar)
  * Schattenkopien / Wiederherstellungspunkte (nur mit Admin messbar)
  * Rest: gesperrte Ordner, die ohne Admin-Rechte nicht messbar sind

Ergebnis: scans/<scan>_abgleich.json, wird von report.py eingebunden.

Aufruf:
    python abgleich.py                    # neuester klassifizierter Scan
    python abgleich.py scans/…_klassifiziert.json
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from scanner import Scanner, is_admin  # noqa: E402

FSCTL_QUERY_USN_JOURNAL = 0x000900F4
GB = 2**30

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                 ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]


def de(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def round_up(n: int, cluster: int) -> int:
    return (n + cluster - 1) // cluster * cluster


def powershell(cmd: str) -> str | None:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


# --- einzelne Posten ---------------------------------------------------------
def ntfs_metadata(drive: str, volume: dict) -> list[dict]:
    out = []
    try:
        st = os.stat(drive + "$MFT")
        out.append({"id": "mft", "name": "NTFS-Dateitabelle ($MFT)", "k": "sys", "s": round_up(st.st_size, volume["cluster"]),
                    "quelle": "gemessen", "info": "Ein Eintrag je Datei und Ordner; wächst mit, schrumpft aber nie."})
    except OSError:
        pass
    clusters = volume["total"] // volume["cluster"]
    out.append({"id": "bitmap", "name": "NTFS-Belegungstabelle ($Bitmap)", "k": "sys",
                "s": round_up((clusters + 7) // 8, volume["cluster"]), "quelle": "berechnet",
                "info": "Ein Bit je Cluster."})
    return out


def usn_journal(drive: str) -> dict | None:
    """Änderungsjournal von NTFS. Braucht Admin-Rechte (Volume-Handle)."""
    h = _k32.CreateFileW("\\\\.\\" + drive.rstrip("\\"), 0x80000000, 3, None, 3, 0, None)
    if h is None or h == wintypes.HANDLE(-1).value:
        return None
    try:
        buf = ctypes.create_string_buffer(56)
        n = wintypes.DWORD()
        if not _k32.DeviceIoControl(h, FSCTL_QUERY_USN_JOURNAL, None, 0, buf, 56, ctypes.byref(n), None):
            return None
        _id, first, nxt, _low, _max, max_size, delta = __import__("struct").unpack_from("<7q", buf.raw)
        size = min(nxt - first, max_size + delta)
    finally:
        _k32.CloseHandle(h)
    return {"id": "usn", "name": "NTFS-Änderungsjournal ($UsnJrnl)", "k": "sys", "s": max(size, 0),
            "quelle": "gemessen", "info": "Protokoll der letzten Dateiänderungen (für Suche, Backup, Defender)."}


def shadow_storage(drive: str) -> dict | None:
    """Speicher für Schattenkopien / Wiederherstellungspunkte. Braucht Admin-Rechte."""
    letter = drive.rstrip("\\")
    out = powershell(
        f"$v = (Get-CimInstance Win32_Volume -Filter \"DriveLetter='{letter}'\").DeviceID; "
        "(Get-CimInstance Win32_ShadowStorage -ErrorAction Stop | Where-Object { $_.Volume.DeviceID -eq $v } "
        "| Measure-Object UsedSpace -Sum).Sum")
    if out is None or out == "":
        return None
    try:
        used = int(out)
    except ValueError:
        return None
    return {"id": "shadow", "name": "Wiederherstellungspunkte (Schattenkopien)", "k": "sys", "s": used,
            "quelle": "gemessen", "info": "Liegt in „System Volume Information“; Größe regelt der Computerschutz."}


def windows_apps(drive: str, scan: dict) -> dict | None:
    """Paketordner unter WindowsApps einzeln messen, falls der Scan den Ordner nicht lesen konnte."""
    apps = drive + "Program Files\\WindowsApps"
    if not any(e["path"].lower() == apps.lower() for e in scan["errors"]):
        return None
    all_users = " -AllUsers" if is_admin() else ""
    out = powershell(f"Get-AppxPackage{all_users} -PackageTypeFilter Main,Framework,Resource,Bundle,Optional "
                     "| Select-Object -ExpandProperty InstallLocation")
    if not out:
        return None
    locs = sorted({p.strip() for p in out.splitlines() if p.strip().lower().startswith(apps.lower() + "\\")})
    sc = Scanner("\\\\?\\" + drive, 2**62, 2**62, None, 1)
    total, readable = 0, 0
    for loc in locs:
        node = sc.scan_dir("\\\\?\\" + loc, os.path.basename(loc))
        if "err" not in node:
            readable += 1
        total += node["s"]
    sys.stderr.write("\n")
    who = "aller Benutzer" if all_users else "des angemeldeten Benutzers"
    return {"id": "appx", "name": "App-Pakete (Program Files\\WindowsApps)", "k": "prog", "s": total,
            "quelle": "gemessen", "info": f"{readable} von {len(locs)} Paketen {who} gelesen (Store-Apps und Frameworks)."}


def blocked_folders(scan: dict, drive: str) -> list[dict]:
    """Gesperrte Ordner aus dem Scan, nach den ersten zwei Ebenen gruppiert."""
    skip = {(drive + "Program Files\\WindowsApps").lower()}
    groups = Counter()
    for e in scan["errors"]:
        if e["path"].lower() in skip:
            continue
        parts = e["path"].rstrip("\\").split("\\")
        depth = 2 if len(parts) > 1 and parts[1].startswith("$") else 3  # $Recycle.Bin\<SID> zusammenfassen
        groups["\\".join(parts[:depth])] += 1
    return [{"path": p, "n": n} for p, n in groups.most_common(15)]


# --- Abgleich ---------------------------------------------------------------
def reconcile(scan: dict) -> dict:
    drive = os.path.splitdrive(scan["root"])[0] + "\\"
    vol = scan["volume"]
    admin = is_admin()
    gap = vol["used"] - scan["scanned_bytes"]

    ntfs = vol.get("fs", "NTFS") == "NTFS"  # FAT32/exFAT: keine MFT, kein Journal
    posten = ntfs_metadata(drive, vol) if ntfs else []
    for p in (usn_journal(drive) if admin and ntfs else None,
              shadow_storage(drive) if admin else None,
              windows_apps(drive, scan)):
        if p:
            posten.append(p)
    posten = [p for p in posten if p["s"] > 0]
    explained = sum(p["s"] for p in posten)

    now = shutil.disk_usage(drive)
    hl = scan["stats"].get("hardlink_bytes_skipped", 0)
    cl = vol["cluster"]
    hinweise = [
        {"id": "gib", "text": f"Windows rechnet in GiB, schreibt aber „GB“: Das Laufwerk hat {de(vol['total'] / 1e9)} GB "
                              f"(Herstellerangabe), Windows zeigt {de(vol['total'] / GB)} GB. Dieser Bericht rechnet wie Windows."},
        {"id": "explorer", "text": "Der Explorer zeigt bei „Größe“ die Dateilänge. Dieser Bericht nutzt die „Größe auf Datenträger“ "
                                   f"(auf {cl // 1024 if cl >= 1024 else cl} {'KB' if cl >= 1024 else 'Byte'}-Blöcke aufgerundet, "
                                   "komprimierte Dateien und Cloud-Platzhalter mit ihrem echten Platz)."},
    ]
    if hl:
        hinweise.insert(1, {"id": "hardlinks", "text": f"Hardlinks: {de(hl / GB)} GB im Windows-Ordner sind mehrfach verlinkt und "
                                                       "hier nur einmal gezählt. Die Eigenschaften des Explorers zählen sie pro Link, "
                                                       "daher wirkt C:\\Windows dort größer."})
    if vol.get("system", drive.upper().startswith(os.environ.get("SystemDrive", "C:").upper())):
        hinweise.append({"id": "settings", "text": "Einstellungen → System → Speicher ordnet anders zu: „System & reserviert“ ≈ "
                                                   "Betriebssystem + System-reserviert, „Temporäre Dateien“ ≈ Teil von Caches & Temp, "
                                                   "„Apps“ enthält Programme samt einem Teil der Programmdaten."})
    return {
        "version": 1,
        "scan": scan["scanned_at"],
        "erstellt": datetime.now().isoformat(timespec="seconds"),
        "admin": admin,
        "windows": {"total": vol["total"], "used": vol["used"], "free": vol["free"]},
        "gescannt": scan["scanned_bytes"],
        "luecke": gap,
        "posten": posten,
        "erklaert": explained,
        "rest": gap - explained,
        "gesperrt": blocked_folders(scan, drive),
        "jetzt": {"used": now.used, "free": now.free, "drift": now.used - vol["used"]},
        "hinweise": hinweise,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Speicheranalyse – Abgleich mit Windows (Phase 4)")
    ap.add_argument("scan", nargs="?", help="klassifizierte Scan-Datei")
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
    res = reconcile(scan)
    out = src.with_name(src.stem.replace("_klassifiziert", "") + "_abgleich.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    w = res["windows"]
    print(f"Abgleich {'mit' if res['admin'] else 'ohne'} Admin-Rechte")
    print(f"  Windows belegt:  {w['used'] / GB:7.2f} GB")
    print(f"  Scan gezählt:    {res['gescannt'] / GB:7.2f} GB")
    print(f"  Lücke:           {res['luecke'] / GB:7.2f} GB, davon erklärt:")
    for p in res["posten"]:
        print(f"    {p['name']:<44} {p['s'] / GB:6.2f} GB  ({p['quelle']})")
    print(f"  Rest:            {res['rest'] / GB:7.2f} GB  (gesperrte Ordner{', ohne Admin nicht messbar' if not res['admin'] else ''})")
    print(f"  Seit dem Scan:   {res['jetzt']['drift'] / GB:+7.2f} GB")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
