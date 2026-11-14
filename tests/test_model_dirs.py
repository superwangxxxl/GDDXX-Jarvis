"""Model folders are trusted only after a file-by-file check against the official archive's manifest (app/install.py).

    python tests/test_model_dirs.py

A small stand-in archive plays the official release. Covered: a complete folder already on disk, a folder with a missing file,
a tampered file (and a planted extra file / symlink), a half-downloaded folder, and the failures: a tampered archive and an
archive whose content does not match the manifest. A failed check never touches what was on disk. Also exercises
tools/dir_manifest.py, which writes the manifest from the verified archive."""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = Path(tempfile.mkdtemp())
os.environ["JARVIS_APP_DIR"] = str(WORK / "support")
os.environ["JARVIS_HOME"] = str(WORK / "home")
os.environ["JARVIS_LANG"] = "zh"
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("jarvis_install", ROOT / "app" / "install.py")
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)

NAME = "toy-model"
FILES = {"a.bin": os.urandom(100_000), "sub/b.txt": b"hello model\n", "c.json": b'{"k": 1}'}


def make_archive(path, files=FILES):
    with tarfile.open(path, "w:bz2") as t:
        for rel, data in files.items():
            m = tarfile.TarInfo(f"{NAME}/{rel}")
            m.size = len(data)
            t.addfile(m, io.BytesIO(data))
    return path


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write_dir(dest, files=FILES):
    for rel, data in files.items():
        f = Path(dest) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)


def snapshot(d):
    return {str(p.relative_to(d)): p.read_bytes() for p in Path(d).rglob("*") if p.is_file()}


def main():
    arc = make_archive(WORK / "official.tar.bz2")
    root = WORK / "repo"
    (root / "app").mkdir(parents=True)
    manifest = root / "app" / "models.json"
    manifest.write_text(json.dumps({"files": [], "dirs": [{"dir": NAME, "archive": "https://example.invalid/toy.tar.bz2", "group": "zh",
                                                            "size": arc.stat().st_size, "sha256": sha(arc)}]}))

    # -- the manifest tool refuses an archive that is not the one in the manifest, and records the real one
    other = make_archive(WORK / "other.tar.bz2", {"x": b"not the official archive"})
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "dir_manifest.py"), str(other), str(manifest)], capture_output=True, text=True)
    assert r.returncode != 0 and "match no entry" in (r.stdout + r.stderr) and "files" not in json.loads(manifest.read_text())["dirs"][0]
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "dir_manifest.py"), str(arc), str(manifest)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    entry = json.loads(manifest.read_text())["dirs"][0]
    assert set(entry["files"]) == set(FILES) and entry["files"]["c.json"] == [len(FILES["c.json"]), hashlib.sha256(FILES["c.json"]).hexdigest()]

    models = WORK / "support" / "models"
    install.ROOT = root
    install.paths.MODELS = models
    fetched = []
    served = {"path": arc}

    def fake_fetch(url, dest, expect=0):
        fetched.append(url)
        shutil.copyfile(served["path"], dest)
    install._fetch = fake_fetch
    dest = models / NAME

    def reset(files=None):
        shutil.rmtree(models, ignore_errors=True)
        models.mkdir(parents=True)
        if files is not None:
            write_dir(dest, files)
        fetched.clear()
        served["path"] = arc

    def leftovers():
        return [p.name for p in models.iterdir() if p.name != NAME]

    # 0. nothing on disk: downloaded, verified, installed
    reset()
    install.models()
    assert len(fetched) == 1 and snapshot(dest) == FILES and not leftovers()

    # 1. a complete folder already on disk: verified file by file, not downloaded again
    reset(FILES)
    before = snapshot(dest)
    install.models()
    assert not fetched and snapshot(dest) == before and not leftovers()
    assert install._dir_problems(dest, entry) == []
    (dest / ".DS_Store").write_bytes(b"finder")                      # Finder's own file is not a reason to download 400 MB
    assert install._dir_problems(dest, entry) == []

    # 2. a missing file: not trusted, replaced from the verified archive
    reset(FILES)
    (dest / "sub" / "b.txt").unlink()
    assert install._dir_problems(dest, entry) == ["缺少 sub/b.txt"]
    install.models()
    assert len(fetched) == 1 and snapshot(dest) == FILES and not leftovers()

    # 3. a tampered file of the same size, plus a planted extra file and a symlink: replaced
    reset(FILES)
    tampered = bytearray(FILES["a.bin"]); tampered[5] ^= 0xFF
    (dest / "a.bin").write_bytes(bytes(tampered))
    (dest / "evil.py").write_text("print('x')")
    os.symlink("/etc/hosts", dest / "link")
    bad = install._dir_problems(dest, entry)
    assert any("校验值不对 a.bin" in b for b in bad) and any("多出的文件 evil.py" in b for b in bad) and any("符号链接 link" in b for b in bad), bad
    install.models()
    assert len(fetched) == 1 and snapshot(dest) == FILES and not (dest / "link").exists() and not leftovers()

    # 4. a half-downloaded folder (an interrupted extraction: one file cut short, the rest absent)
    reset({"a.bin": FILES["a.bin"][:40_000]})
    install.models()
    assert len(fetched) == 1 and snapshot(dest) == FILES and not leftovers()

    # 5. failures never touch what was there: a tampered archive, and an archive whose content is not the manifest's
    reset({"a.bin": FILES["a.bin"][:40_000]})
    before = snapshot(dest)
    evil = make_archive(WORK / "evil.tar.bz2", {**FILES, "c.json": b'{"k": 2}'})
    served["path"] = evil
    try:
        install.models()
        raise AssertionError("a tampered archive was accepted")
    except RuntimeError as e:
        assert "校验不通过" in str(e)
    assert snapshot(dest) == before and not leftovers(), "the existing folder is untouched after a refused archive"
    good_hash = json.loads(manifest.read_text())
    swapped = json.loads(manifest.read_text())
    swapped["dirs"][0]["files"]["c.json"][1] = "0" * 64                 # right archive, manifest that disagrees with its content
    manifest.write_text(json.dumps(swapped))
    served["path"] = arc
    try:
        install.models()
        raise AssertionError("content that does not match the manifest was accepted")
    except RuntimeError as e:
        assert "与清单不符" in str(e)
    assert snapshot(dest) == before and not leftovers()
    manifest.write_text(json.dumps(good_hash))
    nomanifest = json.loads(manifest.read_text())
    del nomanifest["dirs"][0]["files"]
    manifest.write_text(json.dumps(nomanifest))
    try:
        install.models()
        raise AssertionError("a folder without a manifest cannot be verified")
    except RuntimeError as e:
        assert "无法验证" in str(e) or "拒绝" in str(e)
    assert snapshot(dest) == before
    manifest.write_text(json.dumps(good_hash))

    # 6. the shipped manifest covers both Chinese folders file by file
    shipped = json.loads((ROOT / "app" / "models.json").read_text(encoding="utf-8"))["dirs"]
    assert {d["dir"] for d in shipped} == {"kokoro-multi-lang-v1_1", "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"}
    for d in shipped:
        assert len(d["sha256"]) == 64 and d["size"] > 0 and len(d["files"]) > 20 and all(len(v[1]) == 64 for v in d["files"].values()), d["dir"]
    print("ok: model folders are verified file by file")


if __name__ == "__main__":
    main()
