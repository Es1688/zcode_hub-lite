#!/usr/bin/env python3
"""Unit tests for Windows-path handling in guard.py (Engineering Hub Lite).

SCOPE (deliberately narrow, see README "Верификация" and ADR-0004):
  * only the pure path logic — under() prefix semantics with '\\' separators,
    drive roots, case folding, mixed separators;
  * NO realpath/resolve() integration: on Windows realpath resolves symlinks,
    drive-letter casing and UNC prefixes (\\\\?\\, mount points), which cannot
    be reproduced faithfully on POSIX. Full-stack verification runs on the real
    Windows machine during deployment (shadow week + guard --doctor).

Run from anywhere:  python3 hooks/test_winpaths.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402

PASS = FAIL = 0


def ok(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL %s" % name)


W = "\\"  # Windows separator for the tests
WIN = "C:\\Users\\vasya\\work"
PROJ = WIN + W + "proj"
SEP = "\\"

# ---- under(): Windows-style paths, exact and nested
ok("under: exact", guard.under(PROJ, PROJ, sep=SEP))
ok("under: nested file", guard.under(PROJ + SEP + "a.py", PROJ, sep=SEP))
ok("under: nested x3", guard.under(WIN + SEP + "proj" + SEP + "src" + SEP + "m.py", PROJ, sep=SEP))
# ---- prefix traps: sibling with common prefix must NOT match (the bug class of round 1)
ok("under: sibling trap", not guard.under("C:\\Users\\vasya\\work-other\\f", PROJ, sep=SEP))
ok("under: root is prefix of deeper root", guard.under(PROJ, WIN, sep=SEP))
ok("under: deeper root is NOT inside proj", not guard.under(WIN, PROJ, sep=SEP))
# ---- trailing separator on root
ok("under: root trailing sep", guard.under(PROJ + SEP + "f", PROJ + SEP, sep=SEP))
# ---- mixed separators (commands may use either on Windows)
ok("under: mixed sep in path", guard.under("C:/Users/vasya/work/proj/a.py", PROJ, sep=SEP))
ok("under: mixed sep in root", guard.under(PROJ + SEP + "a.py", "C:/Users/vasya/work", sep=SEP))
# ---- case-insensitivity on NTFS (fold=True emulates IS_WINDOWS default)
ok("under: case-insensitive fold", guard.under("c:\\USERS\\vasya\\Work\\Proj\\a.py", PROJ, sep=SEP, fold=True))
ok("under: case-sensitive when fold=False", not guard.under("c:\\users\\vasya\\work\\proj\\a.py", PROJ, sep=SEP, fold=False))
# ---- drive roots
ok("under: drive root contains all", guard.under("C:" + SEP + "x" + SEP + "y", "C:" + SEP, sep=SEP))
# ---- POSIX semantics must stay intact (default sep on Linux)
ok("under: posix exact", guard.under("/home/u/work/p", "/home/u/work/p"))
ok("under: posix nested", guard.under("/home/u/work/p/a.py", "/home/u/work/p"))
ok("under: posix sibling trap", not guard.under("/home/u/work-other/f", "/home/u/work"))
ok("under: empty root is False", not guard.under("/home/u/work", ""))
ok("under: empty path is False", not guard.under("", "/home/u/work"))

# ---- _is_fs_root()
ok("fs root: posix /", guard._is_fs_root("/"))
ok("fs root: posix /home is not", not guard._is_fs_root("/home"))
ok("fs root: drive C:\\", guard._is_fs_root("C:\\"))
ok("fs root: drive C:/", guard._is_fs_root("C:/"))
ok("fs root: C:\\Users is not", not guard._is_fs_root("C:\\Users"))

# ---- expand(): %USERPROFILE% and ~\ forms are HOME-expanded on Windows (checked
#      through the pure string expansion; on POSIX expand() keeps legacy behavior)
if guard.IS_WINDOWS:
    ok("expand: %USERPROFILE%", guard.expand("%USERPROFILE%\\work").startswith(guard.HOME))
    ok("expand: ~\\", guard.expand("~\\work").startswith(guard.HOME))
else:
    # on POSIX the Windows forms are opaque names; only ~ stays expanded
    ok("expand: posix ~", guard.expand("~/work").startswith(guard.HOME))
    ok("expand: posix %USERPROFILE% untouched", guard.expand("%USERPROFILE%\\work") == "%USERPROFILE%\\work")

# ---- is_secret_name(): Windows separators in the path tail
ok("secret name: .env with win path", guard.is_secret_name("C:\\proj\\.env"))
ok("secret name: id_rsa", guard.is_secret_name("C:\\Users\\v\\.ssh\\id_rsa"))
ok("secret name: .env.example excepted", not guard.is_secret_name("C:\\proj\\.env.example"))

# ---- scan_home_outside() regex catches Windows-style raw mentions (pure regex part:
#      exercised via a tiny Ctx-like shim because scan_home_outside needs a Ctx)
class _Shim:
    def __init__(self, fs_root, read_zones):
        self.fs_root = fs_root
        self.read_zones = read_zones


if not guard.IS_WINDOWS:
    # POSIX: HOME is real, fs_root inside it — the ~/ relative branches are testable
    shim = _Shim(os.path.join(guard.HOME, "work"), [])
    out2 = []
    guard.scan_home_outside("cat ~/work/proj/a.py", shim, out2)
    ok("scan regex: inside fs_root not flagged", not out2)
    out3 = []
    guard.scan_home_outside("cat ~/Downloads/x.pdf", shim, out3)
    ok("scan regex: outside fs_root flagged", any(r == "G-READ-OUTSIDE" for r, _ in out3))
    # win-style raw mentions (%USERPROFILE%, C:\Users\...) need a Windows HOME to
    # match the regex — verified on the real Windows machine (deployment checklist)
else:
    shim = _Shim(os.path.join(guard.HOME, "work"), [])
    out = []
    guard.scan_home_outside("type %USERPROFILE%\\.ssh\\id_rsa", shim, out)
    ok("scan regex: %USERPROFILE% mention caught", any(r == "G-READ-OUTSIDE" for r, _ in out))

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
