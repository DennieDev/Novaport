# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only inspection of host paths through the host root mount.

Symlinks are followed the way the host would follow them, but resolution can never leave the
host root: absolute link targets are re-rooted and `..` stops at the root.
"""

import os
import stat
from pathlib import Path

from pydantic import BaseModel

MAX_LINKS = 40


class InvalidPath(ValueError):
    pass


def resolve(root: Path, path: str) -> Path:
    """Map an absolute host path to its location under `root`, following symlinks.

    The result may not exist; callers check that themselves.
    """
    if not path.startswith("/"):
        raise InvalidPath(f"{path!r} is not an absolute path.")
    if "\0" in path:
        raise InvalidPath("Path contains a NUL byte.")

    pending = [p for p in path.split("/") if p not in ("", ".")]
    current = root
    links = 0
    while pending:
        part = pending.pop(0)
        if part == "..":
            if current != root:
                current = current.parent
            continue
        candidate = current / part
        if candidate.is_symlink():
            links += 1
            if links > MAX_LINKS:
                raise InvalidPath(f"Too many symbolic links in {path!r}.")
            target = os.readlink(candidate)
            if target.startswith("/"):
                current = root
            pending = [p for p in target.split("/") if p not in ("", ".")] + pending
            continue
        current = candidate
    return current


def to_host(root: Path, resolved: Path) -> str:
    relative = resolved.relative_to(root)
    return "/" + str(relative) if str(relative) != "." else "/"


class PathInfo(BaseModel):
    path: str
    exists: bool
    kind: str | None = None
    resolved: str | None = None
    uid: int | None = None
    gid: int | None = None
    mode: str | None = None
    error: str | None = None


def _kind(mode: int) -> str:
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISSOCK(mode):
        return "socket"
    return "other"


def check(root: Path, path: str) -> PathInfo:
    try:
        resolved = resolve(root, path)
    except InvalidPath as exc:
        return PathInfo(path=path, exists=False, error=str(exc))
    try:
        st = resolved.lstat()
    except FileNotFoundError:
        return PathInfo(path=path, exists=False, resolved=to_host(root, resolved))
    except OSError as exc:
        return PathInfo(path=path, exists=False, error=exc.strerror or str(exc))
    return PathInfo(
        path=path,
        exists=True,
        kind=_kind(st.st_mode),
        resolved=to_host(root, resolved),
        uid=st.st_uid,
        gid=st.st_gid,
        mode=oct(stat.S_IMODE(st.st_mode)),
    )


class DirEntry(BaseModel):
    name: str
    kind: str
    size: int
    modified: float


def list_dir(root: Path, path: str, limit: int = 1000) -> tuple[list[DirEntry], bool]:
    """Entries of a host directory, and whether the list was cut off at `limit`."""
    resolved = resolve(root, path)
    entries: list[DirEntry] = []
    with os.scandir(resolved) as it:
        for entry in it:
            if len(entries) >= limit:
                return entries, True
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            kind = "symlink" if stat.S_ISLNK(st.st_mode) else _kind(st.st_mode)
            entries.append(
                DirEntry(name=entry.name, kind=kind, size=st.st_size, modified=st.st_mtime)
            )
    entries.sort(key=lambda e: (e.kind != "directory", e.name))
    return entries, False
