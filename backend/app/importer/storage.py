"""§3.2 — "Store the file."

*Keep the original upload on disk keyed by SHA-256, so a full rebuild is
always possible.* Without this the raw payload is unrecoverable once a Flex
window rolls off, and "all derived data is recomputed from raw rows" stops
being true at the outermost layer: the rows themselves came from a document
nobody kept.

Content-addressed, so re-importing the same file writes nothing new and a
corrupted store is detectable by rehashing.
"""

import hashlib
import pathlib

from app.config import get_settings


def store_root() -> pathlib.Path:
    return pathlib.Path(get_settings().data_dir).expanduser() / "imports"


def path_for(sha256: str) -> pathlib.Path:
    # Two-character shard: a flat directory of thousands of files is slow to
    # list and unpleasant to look at.
    return store_root() / sha256[:2] / sha256


def store_payload(sha256: str, content: bytes) -> str | None:
    """Write the payload if it is not already stored. Returns the path, or
    None if the store is unwritable.

    A failure here must not fail the import: losing the archive copy is bad,
    but refusing to ingest data the user already has is worse. The caller
    surfaces it as a warning.
    """
    target = path_for(sha256)
    try:
        if target.exists():
            return str(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary name and rename, so an interrupted write
        # never leaves a truncated file under a hash that claims otherwise.
        tmp = target.with_suffix(".partial")
        tmp.write_bytes(content)
        tmp.rename(target)
        return str(target)
    except OSError:
        return None


def load_payload(sha256: str) -> bytes | None:
    target = path_for(sha256)
    if not target.exists():
        return None
    content = target.read_bytes()
    # Content-addressed storage is only worth anything if the address is
    # checked; a silently corrupted payload would rebuild wrong data.
    if hashlib.sha256(content).hexdigest() != sha256:
        raise ValueError(f"Stored payload {sha256} does not match its own hash — the store is corrupt")
    return content
