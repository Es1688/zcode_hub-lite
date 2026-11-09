#!/usr/bin/env python3
"""Install the Engineering Hub Lite into the ZCode config directory.

Default is DRY-RUN: nothing is written. Use --apply.
  python install_lite.py                                  # plan
  python install_lite.py --apply                          # install (guard in shadow mode)
  python install_lite.py --apply --strict-fs-root <path>  # + hard read boundary
  python install_lite.py --apply --force                  # overwrite non-lite files
  python install_lite.py --apply --guard-mode enforce     # (later, after shadow week)
  python install_lite.py --uninstall --yes                # remove hook entry; --restore-files
                                                          # rolls files back from the last backup

What it does:
  * AGENTS-L0.md      -> <zcode>/AGENTS.md          (placeholder <ZCODE_DIR> substituted)
  * skills/<name>/    -> <zcode>/skills/<name>/     (text files substituted)
  * templates/        -> <zcode>/templates/         (same)
  * hooks/guard.py, hooks/README.md -> <zcode>/hooks/guard/
  * PreToolUse hook entry appended to <zcode>/cli/config.json (foreign entries kept)
  * marker <zcode>/.zcode-hub-lite.json — manifest of installed files + version

Safety: timestamped backups of everything overwritten (files: tmp->replace atomic;
directories: backup -> remove -> copy, NOT atomic, documented); config.json is
backed up before every change; --uninstall needs --yes; the installer expects a
fresh ZCode profile (existing non-lite files require --force).

<zcode> defaults to ~/.zcode (confirmed by the ZCode configuration guide; on
Windows: %USERPROFILE%\\.zcode). Override with --zcode-dir if the guide for your
build says otherwise. The guard detects its own root from its install location,
so no path is baked into the hook command beyond the script path itself.
"""
import argparse
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent
VERSION = "0.1.0"
PLACEHOLDER = "<ZCODE_DIR>"
MARKER_NAME = ".zcode-hub-lite.json"
MARK = "hooks/guard/guard.py"

# [source relative to repo, destination relative to <zcode>, substitute placeholder]
FILE_TARGETS = [
    ("AGENTS-L0.md", "AGENTS.md", True),
    ("hooks/guard.py", "hooks/guard/guard.py", False),
    ("hooks/README.md", "hooks/guard/README.md", True),
]
DIR_TARGETS = [
    ("skills", "skills", True),
    ("templates", "templates", True),
]


def pick_python():
    """Interpreter used in the hook command; on Windows prefer the py launcher."""
    if os.name == "nt":
        for cand in ("py", "python"):
            found = shutil.which(cand)
            if found:
                return [found] + (["-3"] if cand == "py" else [])
        sys.exit("python not found: install Python 3.10+ and re-run")
    for p in ("/usr/bin/python3", shutil.which("python3")):
        if p and os.access(p, os.X_OK):
            return [p]
    sys.exit("python3 not found")


def load_json(path, what):
    if not path.exists():
        return {}, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        sys.exit("ABORT: %s is not valid JSON (%s). Nothing changed." % (what, e))
    if not isinstance(data, dict):
        sys.exit("ABORT: %s root is not an object. Nothing changed." % what)
    return data, True


def write_json(path, data, backup):
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    if backup and path.exists():
        bak = path.with_name(path.name + ".bak-lite-" + time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, bak)
        print("backup:", bak)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-lite")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(fd, mode)
        os.write(fd, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)   # atomic on both POSIX and Windows for files


def is_ours(entry):
    hooks = entry.get("hooks", []) if isinstance(entry, dict) else []
    return any(isinstance(h, dict) and MARK in str(h.get("command", "")) for h in hooks)


def transform_config(cfg, argv, guard_dst, uninstall):
    hooks = cfg.get("hooks")
    if hooks is None:
        hooks = {}
        if not uninstall:
            cfg["hooks"] = hooks
    if not isinstance(hooks, dict):
        sys.exit("ABORT: existing 'hooks' is not an object. Nothing changed.")
    events = hooks.get("events")
    if events is None:
        events = {}
        if not uninstall:
            hooks["events"] = events
    if not isinstance(events, dict):
        sys.exit("ABORT: existing 'hooks.events' is not an object. Nothing changed.")
    lst = events.get("PreToolUse", [])
    if not isinstance(lst, list):
        sys.exit("ABORT: hooks.events.PreToolUse is not a list. Nothing changed.")
    foreign = [e for e in lst if not is_ours(e)]
    if uninstall:
        if foreign:
            events["PreToolUse"] = foreign
        else:
            events.pop("PreToolUse", None)
        if events:
            hooks["events"] = events
        else:
            hooks.pop("events", None)
        return cfg, ["remove guard from PreToolUse (%d other entries kept)" % len(foreign)]
    # Shell form only: a separate `args` field makes ZCode reject the whole config.
    cmd = " ".join('"%s"' % a for a in argv + [str(guard_dst)])
    entry = {"hooks": [{"type": "command", "command": cmd, "timeoutMs": 5000}]}
    events["PreToolUse"] = foreign + [entry]
    hooks["enabled"] = True
    hooks["events"] = events
    return cfg, ["PreToolUse: +1 guard entry, %d existing kept" % len(foreign)]


def subst_text(src, dst, zcode_dir):
    """Copy a text file replacing the <ZCODE_DIR> placeholder (atomic tmp->replace)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    body = src.read_text(encoding="utf-8").replace(PLACEHOLDER, str(zcode_dir))
    tmp = dst.with_name(dst.name + ".tmp-lite")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, dst)


def copy_dir(src, dst, zcode_dir, backup_dir):
    """Directory copy: backup -> remove -> copy (NOT atomic: os.replace cannot
    swap non-empty dirs on Windows; recovery path = restore from the backup)."""
    if dst.exists():
        bak = backup_dir / dst.name
        if bak.exists():
            shutil.rmtree(bak)
        shutil.copytree(dst, bak)
        print("backup dir:", bak)
        shutil.rmtree(dst)
    for f in sorted(src.rglob("*")):
        if f.is_dir():
            continue
        rel = f.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if f.suffix == ".md":
            subst_text(f, target, zcode_dir)
        else:
            shutil.copy2(f, target)


def marker_path(zcode_dir):
    return Path(zcode_dir) / MARKER_NAME


def read_marker(zcode_dir):
    try:
        return json.loads(marker_path(zcode_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def check_targets(zcode_dir, force):
    """Fresh-profile guard: existing destinations without our marker need --force."""
    problems = []
    if read_marker(zcode_dir) is None:
        for _, dst, _ in FILE_TARGETS:
            if (Path(zcode_dir) / dst).exists():
                problems.append(dst)
        for src, dst, _ in DIR_TARGETS:
            d = Path(zcode_dir) / dst
            if d.exists() and any(d.iterdir()):
                problems.append(src + "/")
    if problems and not force:
        sys.exit("ABORT: %s already exist and no %s marker found (fresh profile expected).\n"
                 "Inspect them, then re-run with --force to overwrite (backups will be made)."
                 % (", ".join(sorted(problems)), MARKER_NAME))
    return problems


def installed_files(zcode_dir):
    files = [d for _, d, _ in FILE_TARGETS]
    for src, dst, _ in DIR_TARGETS:
        for f in sorted((SRC / src).rglob("*")):
            if f.is_file():
                files.append((Path(dst) / f.relative_to(SRC / src)).as_posix())
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--zcode-dir", metavar="PATH", default=str(Path.home() / ".zcode"),
                    help="ZCode config dir (default: ~/.zcode; per ZCode docs)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing non-lite files (they are backed up)")
    ap.add_argument("--strict-fs-root", metavar="PATH", default=None,
                    help="set the guard hard read boundary (recommended: your work root)")
    ap.add_argument("--guard-mode", choices=["shadow", "enforce"], default=None,
                    help="write this mode into guard.json (default: not written = shadow)")
    ap.add_argument("--uninstall", action="store_true", help="remove the hook entry "
                    "(+ files from the last backup with --restore-files)")
    ap.add_argument("--restore-files", action="store_true", help="with --uninstall: "
                    "roll installed files back from the newest backup")
    ap.add_argument("--yes", action="store_true", help="confirm --uninstall")
    a = ap.parse_args()

    zcode_dir = Path(a.zcode_dir).expanduser()
    cfg_file = zcode_dir / "cli" / "config.json"
    guard_dir = zcode_dir / "hooks" / "guard"
    guard_json = guard_dir / "guard.json"
    backup_dir = zcode_dir / "backup" / ("lite-" + time.strftime("%Y%m%d-%H%M%S"))
    py_argv = pick_python()

    print("mode:        %s" % ("APPLY" if a.apply else "DRY-RUN (nothing written)"))
    print("zcode dir:   %s" % zcode_dir)
    print("python:      %s" % " ".join(py_argv))
    print("source:      %s" % SRC)

    if a.uninstall:
        if not a.yes:
            print("\n--uninstall needs --yes (explicit confirmation). Nothing done.")
            return
        zcfg, _ = load_json(cfg_file, str(cfg_file))
        new_z, notes = transform_config(zcfg, py_argv, guard_dir / "guard.py", True)
        for n in notes:
            print(" -", n)
        if a.restore_files:
            print("file rollback from newest backup: see %s" % (zcode_dir / "backup"))
            print("(dry-run: run with --apply to perform)")
        if not a.apply:
            print("\nRe-run with --apply to write.")
            return
        write_json(cfg_file, new_z, backup=True)
        if a.restore_files:
            backups = sorted((zcode_dir / "backup").glob("lite-*")) if (zcode_dir / "backup").exists() else []
            if backups:
                latest = backups[-1]
                for item in latest.iterdir():
                    dest = zcode_dir / item.name
                    if dest.name == "cli":
                        continue
                    if dest.exists():
                        (shutil.rmtree(dest) if dest.is_dir() else dest.unlink())
                    (shutil.copytree(item, dest) if item.is_dir() else shutil.copy2(item, dest))
                print("files restored from:", latest)
            else:
                print("no backups found; files left in place")
        mp = marker_path(zcode_dir)
        if mp.exists():
            mp.unlink()
        print("\nDone. Restart ZCode. (Guard files/logs stay in %s)" % guard_dir)
        return

    problems = check_targets(zcode_dir, a.force)
    if problems:
        print("overriding existing (with --force): %s" % ", ".join(sorted(problems)))

    zcfg, existed = load_json(cfg_file, str(cfg_file))
    new_z, notes = transform_config(zcfg, py_argv, guard_dir / "guard.py", False)
    print("zcode cfg:   %s (%s)" % (cfg_file, "exists" if existed else "will be created"))
    for n in notes:
        print(" -", n)
    print("install:     AGENTS-L0.md -> AGENTS.md, skills/, templates/, hooks/guard/")
    if a.strict_fs_root:
        print("strict root: %s" % os.path.realpath(os.path.expanduser(a.strict_fs_root)))
    if a.guard_mode:
        print("guard mode:  %s (written to guard.json)" % a.guard_mode)
    else:
        print("guard mode:  shadow (guard.json not written; guard default)")
    if not a.apply:
        print("\nRe-run with --apply to write.")
        return

    backup_dir.mkdir(parents=True, exist_ok=True)
    for src, dst, _ in FILE_TARGETS + DIR_TARGETS:
        target = zcode_dir / dst
        if target.exists() and target.is_file():
            bak = backup_dir / dst
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, bak)
            print("backup:", bak)
    for src, dst, subst in FILE_TARGETS:
        s, d = SRC / src, zcode_dir / dst
        if subst:
            subst_text(s, d, zcode_dir)
        else:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
        print("install:", d)
    for src, dst, _ in DIR_TARGETS:
        copy_dir(SRC / src, zcode_dir / dst, zcode_dir, backup_dir)
        print("install:", str(zcode_dir / dst) + "/")
    gcfg = {}
    if a.strict_fs_root:
        gcfg["strict_fs_root"] = os.path.realpath(os.path.expanduser(a.strict_fs_root))
    if a.guard_mode:
        gcfg["mode"] = a.guard_mode
    if gcfg:
        write_json(guard_json, gcfg, backup=False)
        print("guard.json:", gcfg)
    write_json(cfg_file, new_z, backup=True)
    write_json(marker_path(zcode_dir),
               {"version": VERSION, "installed": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "zcode_dir": str(zcode_dir), "files": installed_files(zcode_dir)},
               backup=False)
    print("\nDone. Restart ZCode to load the hook and skills.")
    print("Self-check:   python %s --doctor" % (guard_dir / "guard.py"))
    print("Shadow review: python %s --report 7" % (guard_dir / "guard.py"))


if __name__ == "__main__":
    main()
