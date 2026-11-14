"""tools/mac-patch/install.sh and rollback.sh against a stand-in app and a stand-in patch (no network, no Mac).

    python tests/test_patch_install.py

The patched installer is replaced by a stub whose result the test chooses, so this checks the script's own guarantees:
with --zh the Chinese models are verified FIRST and a failure changes nothing (files, voice_lang, reply_lang); the config is
written last, atomically, with the model key left alone, and never over a file that cannot be read; a copy that fails is undone;
backup folders are never overwritten; rollback restores the state from before the first install (here: the older installer's
voice_lang=zh stays, reply_lang goes away), not any earlier backup. The real model check is tests/test_model_dirs.py."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if os.name == "nt":
    print("skipped: shell script test")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp())
APP = T / "Jarvis.app"
RES = APP / "Contents" / "Resources"
PATCH = T / "patch"
HOME = T / "home"
SUPPORT = T / "support"
KEY = "sk-test-key-must-survive"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def tree(d):
    return {str(p.relative_to(d)): p.read_bytes() for p in Path(d).rglob("*") if p.is_file()}


def setup(manifest_extra=""):
    for d in (APP, PATCH, HOME, SUPPORT):
        shutil.rmtree(d, ignore_errors=True)
    (APP / "Contents").mkdir(parents=True)
    (APP / "Contents" / "Info.plist").write_text('<plist><dict><key>CFBundleShortVersionString</key><string>2.0.4</string></dict></plist>')
    (RES / "jarvis").mkdir(parents=True)
    (RES / "jarvis" / "service.py").write_text("old service\n")
    (PATCH / "files" / "jarvis").mkdir(parents=True)
    (PATCH / "files" / "app").mkdir()
    (PATCH / "files" / "jarvis" / "service.py").write_text("new service\n")
    (PATCH / "files" / "jarvis" / "added.py").write_text("added\n")
    (PATCH / "files" / "app" / "install.py").write_text(
        "import os, sys\nopen(os.environ['STUB_LOG'], 'a').write(os.environ.get('JARVIS_LANG', '') + ' ' + ' '.join(sys.argv[1:]) + '\\n')\n"
        "sys.exit(int(os.environ.get('STUB_RC', '0')))\n")
    (PATCH / "manifest.txt").write_text(
        f"M jarvis/service.py {sha(b'new service' + chr(10).encode())} {sha(b'old service' + chr(10).encode())}\n"
        f"A jarvis/added.py {sha(b'added' + chr(10).encode())} -\n" + manifest_extra)
    for s in ("install.sh", "rollback.sh"):
        shutil.copy(ROOT / "tools" / "mac-patch" / s, PATCH / s)
    HOME.mkdir(parents=True)
    cfg = {"llm": {"provider": "deepseek", "api_key": KEY, "model": "deepseek-chat"}, "voice_lang": "zh", "keep": [1, 2]}
    (HOME / "config.json").write_text(json.dumps(cfg))
    os.chmod(HOME / "config.json", 0o600)
    return cfg


def run(script, *args, rc="0"):
    env = {**os.environ, "JARVIS_PATCH_ALLOW_NON_MAC": "1", "JARVIS_PATCH_SKIP_HELPER": "1", "JARVIS_PATCH_PYTHON": sys.executable,
           "JARVIS_HOME": str(HOME), "JARVIS_APP_DIR": str(SUPPORT), "STUB_RC": rc, "STUB_LOG": str(T / "stub.log")}
    extra = ["--app", str(APP)] if script == "install.sh" else []
    return subprocess.run(["bash", str(PATCH / script), *extra, *args], env=env, capture_output=True, text=True)


def config():
    return json.loads((HOME / "config.json").read_text())


def backups():
    d = SUPPORT / "patch-backup"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def main():
    # A. --zh with a failing model check: nothing changes. This is the older installer's state: voice_lang=zh, reply_lang unset
    cfg0 = setup()
    app0, raw0 = tree(RES), (HOME / "config.json").read_bytes()
    r = run("install.sh", "--zh", rc="1")
    assert r.returncode != 0 and "nothing was changed" in r.stderr, (r.stdout, r.stderr)
    assert tree(RES) == app0, "the app files are untouched"
    assert (HOME / "config.json").read_bytes() == raw0 and "reply_lang" not in config(), "no language setting is written"
    assert not backups() and (T / "stub.log").read_text().strip() == "zh --no-page --models-only", "the check ran, in zh, before anything else"

    # B. --zh with a passing check: files, then the settings in one write; the key and everything else survive
    r = run("install.sh", "--zh")
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert (RES / "jarvis" / "service.py").read_text() == "new service\n" and (RES / "jarvis" / "added.py").exists()
    c = config()
    assert c["voice_lang"] == "zh" and c["reply_lang"] == "zh" and c["llm"] == cfg0["llm"] and c["keep"] == [1, 2], c
    assert oct(os.stat(HOME / "config.json").st_mode & 0o777) == "0o600"
    first = backups()
    assert len(first) == 1
    saved = json.loads((SUPPORT / "patch-backup" / first[0] / "config-sections.json").read_text())
    assert saved == {"voice_lang": "zh", "reply_lang": None}, saved                 # the state before this install
    assert KEY not in (SUPPORT / "patch-backup" / first[0] / "config-sections.json").read_text(), "no key in the backup record"
    assert (SUPPORT / "patch-backup" / first[0] / "files" / "jarvis" / "service.py").read_text() == "old service\n"
    snap1 = tree(SUPPORT / "patch-backup" / first[0])

    # C. a second run is a new backup folder; the first one is never touched
    r = run("install.sh", "--zh")
    assert r.returncode == 0, r.stderr
    second = backups()
    assert len(second) == 2 and first[0] in second and tree(SUPPORT / "patch-backup" / first[0]) == snap1, "history is not overwritten"
    assert json.loads((SUPPORT / "patch-backup" / [b for b in second if b != first[0]][0] / "config-sections.json").read_text())["reply_lang"] == "zh"

    # D. rollback: the files go back to stock; the settings go back to the state before the FIRST install
    r = run("rollback.sh", "--clean-config")
    assert r.returncode == 0, r.stderr
    assert (RES / "jarvis" / "service.py").read_text() == "old service\n" and not (RES / "jarvis" / "added.py").exists()
    c = config()
    assert c["voice_lang"] == "zh" and "reply_lang" not in c, "the older installer's voice_lang=zh is the state restored, reply_lang is removed"
    assert c["llm"] == cfg0["llm"] and c["keep"] == [1, 2]
    assert backups() == second and tree(SUPPORT / "patch-backup" / first[0]) == snap1, "rollback leaves the backup folders as they are"
    assert "back to the value from before the install" in r.stdout and "removed (it was not set before the install)" in r.stdout

    # E. a config that cannot be read is never overwritten (it holds the key), and nothing else changes either
    setup()
    (HOME / "config.json").write_text("{ this is not json")
    app0 = tree(RES)
    r = run("install.sh", "--zh")
    assert r.returncode != 0 and "cannot be read as JSON" in r.stderr and tree(RES) == app0 and (HOME / "config.json").read_text() == "{ this is not json"

    # F. a copy that fails half way is undone (a file listed in the manifest that the patch does not carry)
    setup(manifest_extra=f"A jarvis/missing.py {sha(b'x')} -\n")
    app0, raw0 = tree(RES), (HOME / "config.json").read_bytes()
    r = run("install.sh", "--zh")
    assert r.returncode != 0 and "undoing the file changes" in r.stdout + r.stderr, (r.stdout, r.stderr)
    assert tree(RES) == app0, "the originals are back, the added file is gone"
    assert (HOME / "config.json").read_bytes() == raw0, "the config was never reached"

    # G. without --zh the models are not checked and the language settings are left alone
    setup()
    log = T / "stub.log"
    log.unlink(missing_ok=True)
    r = run("install.sh")
    assert r.returncode == 0 and not log.exists() and config()["voice_lang"] == "zh" and "reply_lang" not in config()
    assert (RES / "jarvis" / "service.py").read_text() == "new service\n"
    print("ok: install.sh / rollback.sh guarantees")


if __name__ == "__main__":
    main()
