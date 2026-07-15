"""Safe file operations under DOWNLOAD_DIR for download manager UI."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .config import DOWNLOAD_DIR

# Must not be deleted/managed from the UI (app/install assets)
PROTECTED_NAMES = frozenset(
    {
        "anime-stack",
        "anime-hub",
        "anime-stack-credentials.txt",
        "filebrowser-credentials.txt",
    }
)


def root() -> Path:
    return Path(DOWNLOAD_DIR).resolve()


def is_protected_rel(rel: str) -> bool:
    rel = (rel or "").strip().lstrip("/")
    if not rel:
        return True  # never delete the root itself via API
    top = rel.split("/", 1)[0]
    return top in PROTECTED_NAMES or rel in PROTECTED_NAMES


def resolve_under_root(rel: str) -> Path:
    """Resolve relative path strictly under download root. Raises ValueError if escapes."""
    base = root()
    rel = (rel or "").strip().lstrip("/")
    if not rel:
        return base
    if is_protected_rel(rel):
        # still allow resolve for listing protection flag, but delete will block
        pass
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as e:
        raise ValueError("path escapes download directory") from e
    return candidate


def list_dir(rel: str = "") -> Dict[str, Any]:
    base = root()
    target = resolve_under_root(rel)
    if not target.exists():
        raise FileNotFoundError(str(rel or "/"))
    if not target.is_dir():
        raise NotADirectoryError(str(rel or "/"))

    entries: List[Dict[str, Any]] = []
    try:
        children = list(target.iterdir())
    except PermissionError as e:
        raise PermissionError(f"cannot read directory: {e}") from e

    def sort_key(p: Path):
        return (0 if p.is_dir() else 1, p.name.lower())

    for child in sorted(children, key=sort_key):
        try:
            st = child.stat()
        except OSError:
            continue
        child_rel = str(child.relative_to(base))
        is_dir = child.is_dir()
        entries.append(
            {
                "name": child.name,
                "path": child_rel,
                "is_dir": is_dir,
                "size": None if is_dir else int(st.st_size),
                "mtime": int(st.st_mtime),
                "protected": is_protected_rel(child_rel),
            }
        )

    # parent path for breadcrumb
    parent = ""
    if rel.strip("/"):
        parent = str(Path(rel.strip("/")).parent)
        if parent == ".":
            parent = ""

    return {
        "root": str(base),
        "path": rel.strip("/"),
        "parent": parent,
        "entries": entries,
        "count": len(entries),
    }


def delete_path(rel: str) -> Dict[str, Any]:
    """Delete a file or directory under download root. Blocks protected paths."""
    rel = (rel or "").strip().lstrip("/")
    if not rel:
        raise ValueError("cannot delete download root")
    if is_protected_rel(rel):
        raise PermissionError(f"protected path: {rel}")

    target = resolve_under_root(rel)
    if not target.exists():
        raise FileNotFoundError(rel)

    base = root()
    # double-check still under root after resolve
    target.relative_to(base)

    if target.is_dir():
        shutil.rmtree(target)
        kind = "dir"
    else:
        target.unlink()
        kind = "file"
        # remove sibling .aria2 control file if present
        aria2_ctl = Path(str(target) + ".aria2")
        if aria2_ctl.is_file():
            try:
                aria2_ctl.unlink()
            except OSError:
                pass
        # prune empty parents up to root
        _prune_empty_parents(target.parent, base)

    return {"ok": True, "path": rel, "kind": kind}


def _prune_empty_parents(start: Path, base: Path) -> None:
    cur = start
    base = base.resolve()
    while True:
        try:
            cur.relative_to(base)
        except ValueError:
            break
        if cur == base:
            break
        try:
            if cur.is_dir() and not any(cur.iterdir()):
                cur.rmdir()
                cur = cur.parent
            else:
                break
        except OSError:
            break


def paths_from_aria2_task(task: Dict[str, Any]) -> List[Path]:
    """Extract on-disk paths belonging to an aria2 task status dict."""
    out: List[Path] = []
    for f in task.get("files") or []:
        p = (f.get("path") or "").strip()
        if not p or p.startswith("[METADATA]"):
            continue
        out.append(Path(p))
    return out


def delete_task_files(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    Delete files produced by an aria2 task.
    Prefer removing the whole torrent directory when multi-file under DOWNLOAD_DIR.
    """
    base = root()
    raw_paths = paths_from_aria2_task(task)
    deleted: List[str] = []
    errors: List[str] = []
    candidates: Set[Path] = set()

    for p in raw_paths:
        try:
            rp = p.resolve()
            rp.relative_to(base)
        except Exception:
            errors.append(f"skip outside root: {p}")
            continue
        if is_protected_rel(str(rp.relative_to(base))):
            errors.append(f"skip protected: {rp}")
            continue
        candidates.add(rp)

    if not candidates:
        # fallback: bittorrent info name under dir
        bt = task.get("bittorrent") or {}
        info = bt.get("info") or {}
        name = info.get("name") or ""
        dir_ = task.get("dir") or str(base)
        if name:
            folder = Path(dir_) / name
            try:
                fp = folder.resolve()
                fp.relative_to(base)
                if fp.exists() and not is_protected_rel(str(fp.relative_to(base))):
                    candidates.add(fp)
            except Exception:
                pass

    # If all files share a common subdirectory (torrent folder), delete that folder once
    torrent_dirs: Set[Path] = set()
    files: List[Path] = []
    for c in candidates:
        if c.is_dir():
            torrent_dirs.add(c)
        elif c.is_file():
            files.append(c)
            # if parent is not root and looks like torrent folder, group later
        elif not c.exists():
            # path from aria2 may be planned path; parent dir may exist with partials
            if c.parent != base and c.parent.exists():
                pass

    # Group files by immediate parent under base
    by_parent: Dict[Path, List[Path]] = {}
    for f in files:
        by_parent.setdefault(f.parent, []).append(f)

    for parent, flist in by_parent.items():
        try:
            parent.relative_to(base)
        except ValueError:
            continue
        if parent == base:
            for f in flist:
                try:
                    f.unlink(missing_ok=True)
                    deleted.append(str(f.relative_to(base)))
                    aria2_ctl = Path(str(f) + ".aria2")
                    if aria2_ctl.is_file():
                        aria2_ctl.unlink(missing_ok=True)
                except OSError as e:
                    errors.append(f"{f}: {e}")
            continue
        # If parent only contains this torrent's files (or empty leftovers), rmtree parent
        try:
            rel = str(parent.relative_to(base))
            if is_protected_rel(rel):
                continue
            shutil.rmtree(parent, ignore_errors=False)
            deleted.append(rel + "/")
        except OSError as e:
            # fallback: delete files one by one
            errors.append(f"rmtree {parent}: {e}")
            for f in flist:
                try:
                    f.unlink(missing_ok=True)
                    deleted.append(str(f.relative_to(base)))
                except OSError as e2:
                    errors.append(f"{f}: {e2}")

    for d in torrent_dirs:
        try:
            rel = str(d.relative_to(base))
            if d.exists():
                shutil.rmtree(d)
                deleted.append(rel + "/")
        except OSError as e:
            errors.append(f"{d}: {e}")

    # clean common aria2 leftovers: .torrent / .aria2 named by hash under root
    info_hash = (task.get("infoHash") or "").lower()
    if info_hash:
        for pat in (f"{info_hash}.torrent", f"{info_hash}.aria2"):
            p = base / pat
            if p.is_file():
                try:
                    p.unlink()
                    deleted.append(pat)
                except OSError as e:
                    errors.append(f"{pat}: {e}")

    return {"deleted": deleted, "errors": errors, "count": len(deleted)}
