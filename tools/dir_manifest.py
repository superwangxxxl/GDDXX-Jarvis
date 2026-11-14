"""Record, in app/models.json, the exact contents (every file's size and sha256) of a model archive listed under "dirs".

    python tools/dir_manifest.py <downloaded archive> [app/models.json]

The archive must already match the size and sha256 written in its "dirs" entry (the official release asset); only then is its
content hashed. app/install.py uses this list to verify a model folder that is already on disk, file by file, instead of
trusting that it exists."""
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path


def sha(fobj):
    h = hashlib.sha256()
    for b in iter(lambda: fobj.read(1 << 20), b""):
        h.update(b)
    return h.hexdigest()


def main():
    arc = Path(sys.argv[1])
    path = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parents[1] / "app" / "models.json")
    man = json.loads(path.read_text(encoding="utf-8"))
    with open(arc, "rb") as f:
        digest = sha(f)
    entry = next((d for d in man["dirs"] if d.get("sha256") == digest and d.get("size") == arc.stat().st_size), None)
    if not entry:
        sys.exit(f"{arc.name}: size/sha256 match no entry in {path.name}; not recording anything")
    files = {}
    with tarfile.open(arc) as t:
        for m in t:
            if m.isdir():
                continue
            if not m.isfile():
                sys.exit(f"unexpected non-regular member {m.name!r}")
            parts = Path(m.name).parts
            if parts[0] != entry["dir"] or ".." in parts or len(parts) < 2:
                sys.exit(f"member {m.name!r} is outside {entry['dir']}/")
            files["/".join(parts[1:])] = [m.size, sha(t.extractfile(m))]
    entry["files"] = dict(sorted(files.items()))
    text = json.dumps(man, ensure_ascii=False, indent=1)
    text = re.sub(r'\[\s+(\d+),\s+"([0-9a-f]{64})"\s+\]', r'[\1, "\2"]', text)          # one line per file
    path.write_text(text + "\n", encoding="utf-8")
    print(f"{entry['dir']}: {len(files)} files recorded")


if __name__ == "__main__":
    main()
