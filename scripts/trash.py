"""Delete files and folders recoverably instead of permanently.

  py -3 scripts/trash.py PATH [PATH ...]          # dry run: shows what would happen
  py -3 scripts/trash.py PATH [PATH ...] --yes    # do it

On Windows each item goes to the Recycle Bin when that is known to be safe: recycling is on for the item's volume,
the item is at most --max-mb (default 200 MB) and half the bin capacity, and the bin can be queried. Otherwise the
item is renamed into a dated backup folder on the same volume (default `<volume>\\cli-crew-trash`). A rename on one
volume is atomic, so an item is either fully moved or left untouched; moving to another volume is refused. On other
systems every item goes to the backup folder.

Refused: paths that do not exist, network (UNC) paths, the repository root or any of its parent folders, anything
inside a `.git` folder, drive or volume roots, and the home folder. If any path is refused, nothing is moved; the
run stops at the first failure. Nothing is deleted permanently on purpose; if Windows would destroy an item instead
of recycling it, it is asked to warn first (FOF_WANTNUKEWARNING; answering yes in that dialog does destroy it).

Known limits: the bin check compares item counts, so another program recycling at the same moment can hide a
problem; paths are re-checked right before each move, but a link changed in that instant is not caught; the
non-Windows backup path is unverified.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WINDOWS = os.name == "nt"


def norm(p: str | Path) -> str:
    """Canonical comparison key: real path (links, junctions, 8.3 names resolved), case-folded on Windows."""
    return os.path.normcase(os.path.realpath(p)).rstrip("\\/")


def is_within(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent + os.sep)


def volume_root(p: Path) -> str:
    """Mount point of the volume that holds `p` (handles volumes mounted in folders)."""
    if not WINDOWS:
        path = os.path.realpath(p)
        while not os.path.ismount(path):
            path = os.path.dirname(path)
        return path
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetVolumePathNameW(os.path.realpath(p), buf, 1024):
        raise OSError("cannot determine the volume")
    return buf.value


def size_of(p: Path) -> int:
    if p.is_symlink() or p.is_file():
        return p.lstat().st_size
    total = 0
    for root, _dirs, files in os.walk(p, followlinks=False):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                pass
    return total


def refusal(p: Path) -> str | None:
    """Why `p` must not be trashed, or None."""
    if not os.path.lexists(p):
        return "does not exist"
    raw = os.path.normcase(str(p)).rstrip("\\/")
    real = norm(p)
    for key in (raw, real):
        if key.startswith("\\\\") and not key.startswith("\\\\?\\"):
            return "is a network path"
    repo, home = norm(REPO), norm(Path.home())
    for key in (raw, real):
        if is_within(repo, key):
            return "is the repository root or one of its parent folders"
        if key == home:
            return "is the home folder"
        if ".git" in Path(key).parts:
            return "is inside a .git folder"
    try:
        if norm(volume_root(p)) == real or os.path.ismount(real):
            return "is a drive or volume root"
    except OSError:
        return "is on a volume that cannot be identified"
    return None


def bin_settings(vol: str) -> tuple[bool, float | None]:
    """(recycling known to be on, capacity in MB or None) for a volume mount point, from Explorer's settings."""
    import ctypes
    import winreg

    buf = ctypes.create_unicode_buffer(64)
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(vol, buf, 64):
        return False, None
    guid = buf.value.rstrip("\\").split("Volume", 1)[-1]
    key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume" + "\\" + guid
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            def val(name):
                try:
                    return winreg.QueryValueEx(k, name)[0]
                except OSError:
                    return None
            nuke, cap = val("NukeOnDelete"), val("MaxCapacity")
    except OSError:
        return False, None  # settings unknown: do not trust recycling
    return nuke == 0, (float(cap) if cap is not None else None)  # a missing value counts as unknown


def bin_count(vol: str) -> int | None:
    import ctypes

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint32), ("i64Size", ctypes.c_int64), ("i64NumItems", ctypes.c_int64)]

    info = SHQUERYRBINFO(ctypes.sizeof(SHQUERYRBINFO), 0, 0)
    if ctypes.windll.shell32.SHQueryRecycleBinW(vol, ctypes.byref(info)) != 0:
        return None
    return info.i64NumItems


def recycle_windows(p: Path) -> None:
    """Send one item to the Recycle Bin. FOF_WANTNUKEWARNING makes Windows warn instead of silently destroying it."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO = 3, 0x4, 0x10, 0x40
    FOF_NOERRORUI, FOF_WANTNUKEWARNING = 0x400, 0x4000
    flags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING
    op = SHFILEOPSTRUCTW(None, FO_DELETE, str(p) + "\0", None, flags, False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise OSError(f"Recycle Bin operation failed or was cancelled (code {rc:#x})")
    if os.path.lexists(p):
        raise OSError("item still exists after the Recycle Bin operation")


def backup_target(p: Path, backup_root: Path, stamp: str) -> Path:
    """Keep the item's own path (a link is named after itself, not its target) under the dated folder."""
    rel = Path(os.path.abspath(p))
    rel = rel.relative_to(rel.anchor)
    return backup_root / stamp / rel


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--yes", action="store_true", help="actually move the items (default: dry run)")
    ap.add_argument("--max-mb", type=float, default=200.0, help="larger items go to the backup folder (default 200)")
    ap.add_argument("--backup-dir", type=Path, default=None,
                    help="backup folder on the same volume as the items (default <volume>/cli-crew-trash)")
    a = ap.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    plans, failed = [], False
    for raw in a.paths:
        p = Path(os.path.abspath(raw))
        why = refusal(p)
        if why:
            print(f"REFUSED {p}: {why}")
            failed = True
            continue
        vol = volume_root(p)
        backup_root = Path(os.path.abspath(a.backup_dir)) if a.backup_dir else Path(vol) / "cli-crew-trash"
        backup_parent = backup_root if backup_root.exists() else backup_root.parent
        if not backup_parent.exists() or norm(volume_root(backup_parent)) != norm(vol):
            print(f"REFUSED {p}: backup folder {backup_root} is not on the same volume")
            failed = True
            continue
        if is_within(norm(backup_root), norm(p)):
            print(f"REFUSED {p}: contains the backup folder")
            failed = True
            continue
        size = size_of(p)
        how, note = "backup", "not Windows"
        if WINDOWS:
            enabled, cap_mb = bin_settings(vol)
            limit = a.max_mb if cap_mb is None else min(a.max_mb, cap_mb * 0.5)
            if not enabled:
                note = "Recycle Bin off or unknown for this volume"
            elif size > limit * 1024 * 1024:
                note = f"larger than {limit:.0f} MB"
            elif bin_count(vol) is None:
                note = "Recycle Bin cannot be queried"
            else:
                how, note = "recycle", ""
        target = "Recycle Bin" if how == "recycle" else f"{backup_target(p, backup_root, stamp)} ({note})"
        plans.append((p, how, vol, backup_root))
        print(f"{'WOULD MOVE' if not a.yes else 'WILL MOVE'} {p} ({size / 1048576:.1f} MB) -> {target}")
    if failed:
        print("Nothing was moved: fix or remove the refused paths and run again.")
        return 1
    if not a.yes:
        print("Dry run only. Re-run with --yes to move these items." if plans else "Nothing to do.")
        return 0
    for i, (p, how, vol, backup_root) in enumerate(plans):
        why = refusal(p)  # check again right before acting (a link may have changed since planning)
        if why:
            print(f"  STOPPED at {p}: {why}; {len(plans) - i} item(s) not moved")
            return 1
        try:
            before = bin_count(vol) if how == "recycle" else None
            if how == "recycle" and before is None:
                how = "backup"  # cannot confirm recycling: fall back to the backup folder
            if how == "recycle":
                recycle_windows(p)
                after = bin_count(vol)
                if after is None or after <= before:
                    print(f"  WARNING {p}: removed, but the Recycle Bin did not confirm a new item; check the bin."
                          f" {len(plans) - i - 1} remaining item(s) not moved")
                    return 1
                print(f"  recycled: {p}")
            else:
                dest = backup_target(p, backup_root, stamp)
                if os.path.lexists(dest):
                    raise OSError(f"backup target already exists: {dest}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.rename(p, dest)  # same volume: atomic (Windows also refuses an existing target)
                print(f"  moved to: {dest}")
        except OSError as e:
            if how == "recycle":
                state = "still in place" if os.path.lexists(p) else "no longer at its path; check the Recycle Bin"
            else:
                state = "still in place" if os.path.lexists(p) else "no longer at its path; check the backup folder"
            print(f"  FAILED {p}: {e} ({state}). {len(plans) - i - 1} remaining item(s) not moved")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
