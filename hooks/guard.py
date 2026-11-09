#!/usr/bin/env python3
"""ZCode Engineering Hub Lite guard: PreToolUse policy hook (enforces Level 0 rules).

Windows port of the hub guard (see ADR-0004 in the source hub for the port notes).
Differences from the hub original:
  * ZCODE_DIR is detected from the script location (<zcode>/hooks/guard/guard.py),
    from the ZCODE_GUARD_DIR env override, or falls back to ~/.zcode;
  * under() compares paths through a separator-parameterized normpath so Windows
    "\"-paths work (unit-tested by hooks/test_winpaths.py without realpath);
  * scratch roots default to %TEMP%/%TMP% on Windows, /tmp//var/tmp on POSIX;
  * SYSTEM_DIRS/SAFE_DEV gain Windows roots (C:\\Windows, Program Files, NUL...);
  * tokenize() normalizes backslashes to "/" before shlex on Windows (Git Bash
    commands still parse; escaped backslashes are rare in guarded commands);
  * PowerShell/cmd shells are noted (NOTE-raw-shell), not fully parsed.

Modes (<zcode>/hooks/guard/guard.json, key "mode"):
  off      do nothing
  shadow   decide + LOG only, never blocks (default)
  enforce  block via PreToolUse "deny" / "ask"

Strict FS boundary ("strict_fs_root", e.g. the user's work root): when set, reading
any path outside that root, "read_allow_roots" and system dirs is blocked
(G-READ-OUTSIDE, Level C). cd into forbidden paths, interpreter script files and
raw mentions of $HOME-subpaths (python -c, eval, ...) are covered too.

Trusted read zones ("read_zone_roots", default: <zcode>/cli/plugins/cache and
<zcode>/skills): reads there also skip the <zcode> secret rule, so document
skills can run bundled scripts; secret-NAMED files stay hidden, writes unaffected.

Design rules:
  * decisions depend on tool / command / path, never on ZCode's `mode` field;
  * any internal error -> allow (fail-open) + log;
  * a safety net against agent mistakes, NOT a security boundary: it cannot see
    inside scripts (python -c, bash script.sh), base64 tricks, etc.

Manual subcommands (not hook calls):
  guard.py --report [DAYS]   summarize the log (shadow review)
  guard.py --doctor          self-check
Stdlib only.
"""
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
import time

IS_WINDOWS = os.name == "nt"


def _detect_zcode_dir():
    """Where the ZCode config root lives. Install layout <zcode>/hooks/guard/guard.py
    wins (portable across OSes); ZCODE_GUARD_DIR env overrides (install_lite.py hook
    command / isolated tests); legacy default is ~/.zcode (used by test_guard.py,
    which runs the script from a plain hooks/ directory with a throw-away HOME)."""
    env = os.environ.get("ZCODE_GUARD_DIR")
    if env:
        return os.path.abspath(env)
    here = os.path.dirname(os.path.realpath(__file__))
    if os.path.basename(here) == "guard" and os.path.basename(os.path.dirname(here)) == "hooks":
        return os.path.dirname(os.path.dirname(here))
    return os.path.join(HOME, ".zcode")


HOME = os.path.realpath(os.path.expanduser("~"))
ZCODE_DIR = _detect_zcode_dir()
GUARD_DIR = os.path.join(ZCODE_DIR, "hooks", "guard")
LOG_FILE = os.path.join(GUARD_DIR, "guard.jsonl")
CONFIG_FILE = os.path.join(GUARD_DIR, "guard.json")
TOKEN_FILE = os.path.join(GUARD_DIR, "allow-level-c")
SSH_DIR = os.path.join(HOME, ".ssh")
PROJECTS_DIR = os.path.join(HOME, "PROJECTS")

if IS_WINDOWS:
    _SYSROOT = os.environ.get("SystemRoot", r"C:\Windows")
    _SCRATCH_ROOTS = [p for p in (os.environ.get("TEMP"), os.environ.get("TMP"),
                                  os.path.join(_SYSROOT, "Temp")) if p]
else:
    _SCRATCH_ROOTS = ["/tmp", "/var/tmp"]

DEFAULTS = {
    "mode": "shadow",
    "ask_supported": False,       # set true only after the `ask` test passed on this ZCode build
    "ask_fallback": "deny",       # what an "ask" becomes when ask is unsupported: deny | allow
    "allow_once_ttl_min": 10,     # lifetime of the Level C token file
    "write_allow_roots": list(_SCRATCH_ROOTS),
    "strict_fs_root": "",         # non-empty: hard read boundary at this root (G-READ-OUTSIDE)
    "read_allow_roots": list(_SCRATCH_ROOTS),
    "read_zone_roots": [os.path.join(ZCODE_DIR, "cli", "plugins", "cache"),
                        os.path.join(ZCODE_DIR, "skills")],  # trusted reads: plugin/skill assets
    "protected_data_names": ["DATASTOR", "REPORTS", "legacy"],
    "disabled_rules": [],
}

# rule id: (level, action, reason)
RULES = {
    "G-PUSH": ("C", "ask", "git push: Level C, нужно явное разрешение пользователя"),
    "G-PUSH-FORCE": ("D", "deny", "force/delete push запрещён"),
    "G-MERGE-MAIN": ("C", "ask", "merge в main (Level C), нужно явное разрешение пользователя"),
    "G-RELEASE": ("C", "ask", "публикация/релиз (Level C), нужно явное разрешение пользователя"),
    "G-GIT-DESTRUCTIVE": ("D", "ask", "необратимая git-операция"),
    "G-ADD-SECRET": ("D", "deny", "в git добавляется файл с секретами"),
    "G-RM-CRITICAL": ("D", "deny", "удаление корня проекта, домашнего каталога, .git или системного пути"),
    "G-RM-RECURSIVE": ("B", "ask", "рекурсивное удаление (rm -rf) только после подтверждения"),
    "G-WRITE-PROTECTED": ("D", "deny", "запись в защищённый путь (~/.ssh, zcode/hooks|cli, ~/PROJECTS, системные каталоги)"),
    "G-WRITE-ZCODE": ("D", "ask", "изменение каталога ZCode только после подтверждения"),
    "G-WRITE-REFDATA": ("D", "deny", "эталонные/оригинальные данные не изменять"),
    "G-WRITE-OUTSIDE": ("B", "ask", "запись вне текущего workspace"),
    "G-READ-SSH": ("D", "deny", "чтение приватных ключей ~/.ssh запрещено"),
    "G-READ-OUTSIDE": ("C", "ask", "чтение вне рабочего корня (strict_fs_root) запрещено (жёсткая граница ФС)"),
    "G-READ-SECRET": ("B", "ask", "чтение файла с секретами или каталога ZCode: содержимое попадёт в контекст модели"),
    "G-PIPE-TO-SHELL": ("D", "deny", "скачивание или декодирование с немедленным исполнением (curl|sh)"),
    "G-SUDO": ("D", "ask", "повышение привилегий (sudo)"),
    "G-SQL-DESTRUCTIVE": ("D", "ask", "деструктивный SQL (DROP/TRUNCATE)"),
    "G-SYSTEM": ("D", "deny", "системная операция (mkfs/shutdown/запись в устройство)"),
}
LEVEL_C = {"G-PUSH", "G-MERGE-MAIN", "G-RELEASE", "G-READ-OUTSIDE"}
SEVERITY = {"note": 1, "allow": 0, "ask": 2, "deny": 3}

SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|Bearer\s+[A-Za-z0-9._\-]{10,}"
    r"|(?:api[_-]?key|token|secret|passwd|password)\s*[=:]\s*\S+)", re.IGNORECASE)

if IS_WINDOWS:
    _PF = os.environ.get("ProgramFiles", r"C:\Program Files")
    _PF86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    _PD = os.environ.get("ProgramData", r"C:\ProgramData")
    SYSTEM_DIRS = (_SYSROOT, _PF, _PF86, _PD, r"C:\Windows\System32",
                   os.path.join(HOME[:3], "Windows") if len(HOME) > 3 else r"C:\Windows")
    SAFE_DEV = {"NUL", "CON", "CONIN$", "CONOUT$"}
else:
    SYSTEM_DIRS = ("/etc", "/usr", "/boot", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/var",
                   "/opt", "/root", "/sys", "/proc", "/srv", "/dev", "/run", "/snap")
    SAFE_DEV = {"/dev/null", "/dev/tty", "/dev/stdout", "/dev/stderr", "/dev/stdin", "/dev/zero"}
# readable under strict_fs_root policy: system dirs minus other users' homes
# (/root on POSIX; C:\Users holds other profiles on Windows -> excluded as well)
_USERS_DIRS = (os.path.join(HOME[:3], "Users"),) if IS_WINDOWS and len(HOME) > 3 else ("/root",)
READABLE_SYSTEM_DIRS = tuple(d for d in SYSTEM_DIRS if d not in _USERS_DIRS)
MAIN_BRANCHES = {"main", "master"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
# Windows-native shells: command grammar is not parsed; noted for the report only
WINDOWS_SHELLS = {"powershell", "pwsh", "cmd", "powershell_ise"}

SECRET_NAME_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.kdbx",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*", "cookies.txt", ".netrc", ".pgpass",
    "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml", "secrets.toml",
    "service-account*.json",
]
SECRET_NAME_EXCEPT_SUFFIX = (".pub", ".example", ".sample", ".template", ".dist", ".defaults")

READERS = {"cat", "less", "more", "head", "tail", "tac", "nl", "bat", "batcat", "grep", "egrep",
           "fgrep", "rg", "ag", "ack", "sed", "awk", "gawk", "cut", "sort", "uniq", "strings",
           "xxd", "od", "hexdump", "base64", "openssl", "gpg", "diff", "cmp", "jq", "yq",
           "type"}
PATTERN_FIRST = {"grep", "egrep", "fgrep", "rg", "ag", "ack", "sed", "awk", "gawk"}

WRITE_ALL_ARGS = {"touch", "mkdir", "rmdir", "chmod", "chown", "chgrp", "truncate", "tee",
                  "shred", "unlink", "mv"}
DEST_LAST = {"cp", "install", "ln", "rsync", "xcopy", "robocopy"}

WRAPPERS = {
    "nohup": ((), 0), "command": ((), 0), "builtin": ((), 0), "exec": ((), 0), "time": ((), 0),
    "setsid": ((), 0), "stdbuf": ((), 0), "unbuffer": ((), 0),
    "nice": (("-n", "--adjustment"), 0), "ionice": (("-c", "-n", "-p", "-t"), 0),
    "timeout": (("-s", "-k", "--signal", "--kill-after"), 1),
    "xargs": (("-n", "-I", "-P", "-L", "-s", "-d", "-E", "-a", "-l"), 0),
}


# ----------------------------------------------------------------- helpers
def clip(s, n=200):
    s = SECRET_RE.sub("<redacted>", str(s))
    return s if len(s) <= n else s[:n] + "...(+%d)" % (len(s) - n)


def is_interp(b):
    return b in SHELLS or b in ("perl", "ruby", "node", "php") or bool(re.match(r"^python[\d.]*$", b or ""))


def short_has(args, ch):
    return any(a.startswith("-") and not a.startswith("--") and ch in a[1:] for a in args)


def nonopts(args):
    out, dd = [], False
    for a in args:
        if dd:
            out.append(a)
        elif a == "--":
            dd = True
        elif a.startswith("-") and a != "-":
            continue
        else:
            out.append(a)
    return out


def expand(p):
    if p is None:
        return None
    p = p.replace("${HOME}", HOME).replace("$HOME", HOME)
    if IS_WINDOWS:
        p = p.replace("%USERPROFILE%", HOME)
    if p == "~" or p.startswith("~/") or (IS_WINDOWS and p.startswith("~\\")):
        p = HOME + p[1:]
    if "$" in p or "`" in p:
        return None
    return p


def resolve(p, cwd):
    p = expand(p)
    if not p:
        return None
    if not os.path.isabs(p):
        if cwd is None:
            return None
        p = os.path.join(cwd, p)
    return os.path.realpath(p)


def under(path, root, sep=os.sep, fold=None):
    """path == root or path is inside root.

    Separator-parameterized so Windows "\"-paths can be unit-tested on any OS
    (hooks/test_winpaths.py): pass sep='\\\\'. Inputs are expected already
    absolute (resolve() -> realpath); normpath is idempotent on those. fold
    defaults to case-insensitive on Windows (NTFS), sensitive on POSIX."""
    if not path or not root:
        return False
    if fold is None:
        fold = IS_WINDOWS
    p = os.path.normpath(str(path)).replace("\\", sep).replace("/", sep)
    r = os.path.normpath(str(root)).replace("\\", sep).replace("/", sep)
    if fold:
        p, r = p.casefold(), r.casefold()
    return p == r or p.startswith(r.rstrip(sep) + sep)


def _is_fs_root(p):
    """Drive root on Windows (C:\\), "/" or "\\" elsewhere. The drive form is
    checked unconditionally: a POSIX realpath never looks like 'C:\\'."""
    if p in ("/", "\\"):
        return True
    return len(p) == 3 and p[1] == ":" and p[2] in "\\/"


def is_secret_name(path):
    b = os.path.basename(str(path).rstrip("/\\").replace("\\", "/")).lower()
    if not b or b.endswith(SECRET_NAME_EXCEPT_SUFFIX):
        return False
    return any(fnmatch.fnmatch(b, pat) for pat in SECRET_NAME_PATTERNS)


class Ctx:
    def __init__(self, ws, cwd, cfg):
        self.ws = os.path.realpath(ws)
        self.cwd = os.path.realpath(cwd)
        self.cfg = cfg
        self.allow_roots = [os.path.realpath(r) for r in cfg["write_allow_roots"]]
        self.fs_root = os.path.realpath(cfg["strict_fs_root"]) if cfg["strict_fs_root"] else ""
        self.read_roots = [os.path.realpath(r) for r in cfg["read_allow_roots"]]
        self.read_zones = [os.path.realpath(r) for r in cfg["read_zone_roots"]]
        self.refnames = set(cfg["protected_data_names"])
        self.progs = []

    def read_ok(self, path):
        return (under(path, self.fs_root)
                or any(under(path, r) for r in self.read_roots)
                or any(under(path, z) for z in self.read_zones)
                or any(under(path, d) for d in READABLE_SYSTEM_DIRS))


def add(out, rule, detail=""):
    out.append((rule, detail))


def classify_write(path, ctx, out, detail=""):
    if path is None or path in SAFE_DEV or os.path.basename(str(path)) in SAFE_DEV:
        return
    if (under(path, SSH_DIR) or under(path, os.path.join(ZCODE_DIR, "hooks"))
            or under(path, os.path.join(ZCODE_DIR, "cli")) or under(path, PROJECTS_DIR)):
        add(out, "G-WRITE-PROTECTED", detail or path)
        return
    if under(path, ZCODE_DIR):
        add(out, "G-WRITE-ZCODE", detail or path)
        return
    if under(path, ctx.ws):
        rel = os.path.relpath(path, ctx.ws).split(os.sep)
        if any(part in ctx.refnames for part in rel):
            add(out, "G-WRITE-REFDATA", detail or path)
        return
    if any(under(path, r) for r in ctx.allow_roots):
        return
    if any(under(path, d) for d in SYSTEM_DIRS) or _is_fs_root(path):
        add(out, "G-WRITE-PROTECTED", detail or path)
        return
    add(out, "G-WRITE-OUTSIDE", detail or path)


def classify_read(path, ctx, out, detail=""):
    if path is None:
        return
    if under(path, SSH_DIR) and not path.endswith(".pub"):
        add(out, "G-READ-SSH", detail or path)
    elif any(under(path, z) for z in ctx.read_zones):
        if is_secret_name(path):   # trusted zone, but secret-named files stay hidden
            add(out, "G-READ-SECRET", detail or path)
    elif under(path, ZCODE_DIR):
        if not path.endswith(".md"):
            add(out, "G-READ-SECRET", detail or path)
    elif is_secret_name(path):
        add(out, "G-READ-SECRET", detail or path)
    if ctx.fs_root and not ctx.read_ok(path):
        add(out, "G-READ-OUTSIDE", detail or path)


# ----------------------------------------------------------------- git helpers
def run_git(cwd, args, timeout=2):
    try:
        r = subprocess.run(["git", "-C", cwd] + args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except Exception:
        return 1, ""


def git_branch(cwd):
    if not cwd:
        return None
    rc, o = run_git(cwd, ["symbolic-ref", "--short", "-q", "HEAD"])
    return o.strip() if rc == 0 else None


def git_ignored(cwd, p):
    rc, _ = run_git(cwd, ["check-ignore", "-q", "--", p])
    return rc == 0


def git_untracked(cwd, paths):
    args = ["ls-files", "--others", "--exclude-standard", "-z"]
    if paths:
        args += ["--"] + paths
    rc, o = run_git(cwd, args, timeout=3)
    return [x for x in o.split("\0") if x] if rc == 0 else []


def git_parse(args):
    i, cdir = 0, None
    while i < len(args):
        a = args[i]
        if a == "-C":
            cdir = args[i + 1] if i + 1 < len(args) else None
            i += 2
            continue
        if a in ("-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        return a, args[i + 1:], cdir
    return None, [], cdir


def handle_git_add(rest, gcwd, out):
    force = "--force" in rest or short_has(rest, "f")
    paths, dd = [], False
    for a in rest:
        if dd:
            paths.append(a)
        elif a == "--":
            dd = True
        elif not a.startswith("-"):
            paths.append(a)
    bulk = "--all" in rest or short_has(rest, "A")
    hits, specs = [], []
    for p in paths:
        if is_secret_name(p):
            if force or not (gcwd and git_ignored(gcwd, p)):
                hits.append(p)
        elif p in (".", ":/", "*", "./") or (gcwd and os.path.isdir(os.path.join(gcwd, p))):
            specs.append(p)
    if gcwd and (bulk or specs):
        for f in git_untracked(gcwd, specs if not bulk else []):
            if is_secret_name(f):
                hits.append(f)
    if hits:
        add(out, "G-ADD-SECRET", ", ".join(sorted(set(hits))[:5]))


def handle_git(args, ctx, st, out):
    sub, rest, cdir = git_parse(args)
    gcwd = resolve(cdir, st["vcwd"]) if cdir else st["vcwd"]
    if sub is None:
        return
    if sub == "push":
        force = (any(a.startswith("--force") or a in ("--delete", "--mirror", "--prune", "-f", "-d") for a in rest)
                 or any(a[:1] in ("+", ":") for a in rest if not a.startswith("-")) or short_has(rest, "f"))
        add(out, "G-PUSH-FORCE" if force else "G-PUSH", "git push " + " ".join(rest)[:80])
    elif sub == "merge":
        if not any(a in ("--abort", "--continue", "--quit") for a in rest):
            br = st["branch"] if st["branch"] is not None else git_branch(gcwd)
            if br in MAIN_BRANCHES:
                add(out, "G-MERGE-MAIN", "git merge " + " ".join(rest)[:80] + " (на %s)" % br)
    elif sub in ("checkout", "switch"):
        pos = [a for a in rest if not a.startswith("-")]
        if pos:
            st["branch"] = pos[0]
    elif sub == "reset":
        if "--hard" in rest:
            add(out, "G-GIT-DESTRUCTIVE", "git reset --hard")
    elif sub == "clean":
        if (short_has(rest, "f") or "--force" in rest) and not (short_has(rest, "n") or "--dry-run" in rest):
            add(out, "G-GIT-DESTRUCTIVE", "git clean " + " ".join(rest)[:60])
    elif sub == "branch":
        if "-D" in rest or (("-d" in rest or "--delete" in rest) and ("-f" in rest or "--force" in rest)) \
                or any(a in ("-df", "-fd") for a in rest):
            add(out, "G-GIT-DESTRUCTIVE", "git branch " + " ".join(rest)[:60])
    elif sub == "stash":
        if rest[:1] in (["drop"], ["clear"]):
            add(out, "G-GIT-DESTRUCTIVE", "git stash " + rest[0])
    elif sub in ("filter-branch", "filter-repo"):
        add(out, "G-GIT-DESTRUCTIVE", "git " + sub)
    elif sub == "reflog" and rest[:1] == ["expire"]:
        add(out, "G-GIT-DESTRUCTIVE", "git reflog expire")
    elif sub == "add":
        handle_git_add(rest, gcwd, out)


# ----------------------------------------------------------------- shell parsing
def strip_heredocs(cmd):
    out, skip = [], None
    for line in cmd.split("\n"):
        if skip is not None:
            if line.strip() == skip:
                skip = None
            continue
        out.append(line)
        m = re.search(r"(?<!<)<<(?!<)-?\s*(['\"]?)(\w+)\1", line)
        if m and not re.search(r"(?:^|[|;&]\s*)(?:sudo\s+)?(?:ba|z|da)?sh\b", line):
            skip = m.group(2)   # data heredoc: drop body; shell heredoc: body stays and is analyzed
    return "\n".join(out)


def newlines_to_semis(s):
    out, q, esc = [], None, False
    for ch in s:
        if esc:
            out.append(ch)
            esc = False
        elif ch == "\\" and q != "'":
            out.append(ch)
            esc = True
        elif q:
            if ch == q:
                q = None
            out.append(ch)
        elif ch in "'\"":
            q = ch
            out.append(ch)
        elif ch == "\n":
            out.append(";")
        else:
            out.append(ch)
    return "".join(out)


def extract_subshells(s):
    res, i, n, sq = [], 0, len(s), False
    while i < n:
        ch = s[i]
        if ch == "'":
            sq = not sq
        elif not sq and s.startswith("$(", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                j += 1
            inner = s[i + 2:j - 1] if depth == 0 else s[i + 2:]
            if inner.strip():
                res.append(inner)
        elif not sq and ch == "`":
            j = s.find("`", i + 1)
            if j > i + 1:
                res.append(s[i + 1:j])
                i = j
        i += 1
    return res


def tokenize(s):
    if IS_WINDOWS:
        # Windows paths use "\" which posix shlex would eat as escapes; guarded
        # commands rarely rely on backslash escapes, so normalize to "/" first.
        s = s.replace("\\", "/")
    lex = shlex.shlex(s.replace("`", " "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def is_sep(t):
    return bool(t) and set(t) <= set(";&|()")


def is_redirect(t):
    return bool(t) and set(t) <= set("<>&|") and (">" in t or "<" in t)


def parse_commands(toks):
    cmds, cur, pipe_in, i = [], {"argv": [], "redirs": [], "piped": False}, False, 0
    while i < len(toks):
        t = toks[i]
        if is_sep(t):
            if cur["argv"] or cur["redirs"]:
                cmds.append(cur)
            pipe_in = t in ("|", "|&")
            cur = {"argv": [], "redirs": [], "piped": pipe_in}
        elif is_redirect(t):
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt is not None:
                if t in (">", ">>", ">|", "&>", "&>>"):
                    cur["redirs"].append(("w", nxt))
                elif t == "<":
                    cur["redirs"].append(("r", nxt))
                i += 1
        else:
            cur["argv"].append(t)
        i += 1
    if cur["argv"] or cur["redirs"]:
        cmds.append(cur)
    return cmds


def unwrap(argv):
    a, flags = list(argv), set()
    for _ in range(8):
        if not a:
            break
        w = a[0]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
            a.pop(0)
            continue
        b = os.path.basename(w.replace("\\", "/"))
        if b in ("sudo", "doas"):
            flags.add("sudo")
            a.pop(0)
            while a and a[0].startswith("-"):
                o = a.pop(0)
                if o in ("-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U") and a:
                    a.pop(0)
            continue
        if b == "env":
            a.pop(0)
            while a and (a[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", a[0])):
                o = a.pop(0)
                if o in ("-u", "-C", "-S") and a:
                    a.pop(0)
            continue
        if b in WRAPPERS:
            optargs, npos = WRAPPERS[b]
            a.pop(0)
            while a and a[0].startswith("-"):
                o = a.pop(0)
                if o in optargs and a:
                    a.pop(0)
            for _ in range(npos):
                if a:
                    a.pop(0)
            continue
        break
    return (os.path.basename((a[0] if a else "").replace("\\", "/")) or None), (a[1:] if a else []), flags


def shell_c_script(args):
    for i, a in enumerate(args):
        if a == "--":
            break
        if a.startswith("-") and not a.startswith("--") and "c" in a[1:]:
            return args[i + 1] if i + 1 < len(args) else None
    return None


def script_files(args, prog):
    """Non-option args that are files for sed/perl/grep-like tools (skip the script/pattern)."""
    files, has_script_opt, skip, i = [], False, False, 0
    while i < len(args):
        a = args[i]
        if a in ("-e", "-f", "--expression", "--file", "--regexp") or \
                (a.startswith("-") and not a.startswith("--") and a.endswith("e") and prog in ("perl",)):
            has_script_opt = True
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        files.append(a)
        i += 1
    return files if has_script_opt else files[1:]


def handle_cmd(cmd, ctx, st, depth, out):
    prog, args, flags = unwrap(cmd["argv"])
    for kind, tgt in cmd["redirs"]:
        p = resolve(tgt, st["vcwd"])
        if kind == "w":
            classify_write(p, ctx, out, "> " + tgt)
        else:
            classify_read(p, ctx, out, "< " + tgt)
    if not cmd["piped"]:
        st["dl"] = False
    if prog is None:
        return
    ctx.progs.append(prog)
    if prog.startswith("$") or prog.startswith("`"):
        add(out, "NOTE-dynamic-command", clip(prog, 40))
    if prog in WINDOWS_SHELLS:
        # PowerShell/cmd grammar is not parsed: surface it in the report instead
        add(out, "NOTE-raw-shell", prog + " " + " ".join(args)[:60])
        return
    if "sudo" in flags:
        add(out, "G-SUDO", "sudo " + prog)
    if prog in ("curl", "wget", "base64", "xxd", "gunzip", "zcat", "openssl"):
        st["dl"] = True   # remote or decoded content piped onward
    elif cmd["piped"] and st["dl"] and is_interp(prog) and shell_c_script(args) is None:
        add(out, "G-PIPE-TO-SHELL", "| " + prog)

    if prog in ("cd", "pushd"):
        tgt = args[0] if args else "~"
        p = None if tgt == "-" else resolve(tgt, st["vcwd"])
        if p is not None and ctx.fs_root and not ctx.read_ok(p):
            add(out, "G-READ-OUTSIDE", "cd " + tgt)
        st["vcwd"] = p
        return
    if prog in SHELLS:
        script = shell_c_script(args)
        if script is not None:
            analyze_string(script, ctx, dict(st), depth + 1, out)
        elif args and not args[0].startswith("-"):
            classify_read(resolve(args[0], st["vcwd"]), ctx, out, prog + " " + args[0])
        return
    if is_interp(prog) and args:
        first = next((a for a in args if not a.startswith("-")), None)
        if first is not None:
            classify_read(resolve(first, st["vcwd"]), ctx, out, prog + " " + first)
    if prog == "eval":
        analyze_string(" ".join(args), ctx, dict(st), depth + 1, out)
        return
    if prog in ("source", ".") and args:
        classify_read(resolve(args[0], st["vcwd"]), ctx, out, "source " + args[0])
        return
    if prog == "git":
        handle_git(args, ctx, st, out)
        return
    if prog == "gh":
        if args[:2] == ["pr", "merge"]:
            add(out, "G-MERGE-MAIN", "gh pr merge")
        elif args[:1] == ["release"]:
            add(out, "G-RELEASE", "gh release " + " ".join(args[1:2]))
        elif args[:2] == ["repo", "delete"]:
            add(out, "G-GIT-DESTRUCTIVE", "gh repo delete")
        return
    if (prog in ("npm", "pnpm", "yarn") and "publish" in args[:2]) or \
            (prog == "twine" and "upload" in args) or (prog in ("cargo", "poetry") and "publish" in args[:1]) or \
            (prog == "docker" and args[:1] == ["push"]) or (prog == "gem" and args[:1] == ["push"]) or \
            (is_interp(prog) and "twine" in args and "upload" in args):
        add(out, "G-RELEASE", prog + " " + " ".join(args[:2]))
        return
    if prog.startswith("mkfs") or prog in ("shutdown", "reboot", "halt", "poweroff"):
        add(out, "G-SYSTEM", prog)
        return
    if prog == "dd":
        for a in args:
            if a.startswith("of="):
                p = resolve(a[3:], st["vcwd"])
                is_device = (under(p, "/dev") if not IS_WINDOWS
                             else bool(p) and str(p).startswith("\\\\.\\"))
                if p and is_device and p not in SAFE_DEV:
                    add(out, "G-SYSTEM", "dd of=" + a[3:])
                else:
                    classify_write(p, ctx, out, a)
            elif a.startswith("if="):
                classify_read(resolve(a[3:], st["vcwd"]), ctx, out, a)
        return
    if prog == "find":
        if "-delete" in args or any(args[i] in ("-exec", "-execdir") and args[i + 1:i + 2] == ["rm"]
                                    for i in range(len(args))):
            add(out, "G-RM-RECURSIVE", "find ... delete")
        return
    if prog == "rm":
        handle_rm(args, ctx, st, out)
        return

    vc = st["vcwd"]
    if prog in WRITE_ALL_ARGS:
        for a in nonopts(args):
            classify_write(resolve(a, vc), ctx, out, "%s %s" % (prog, a))
    elif prog in DEST_LAST:
        tdir = None
        for i, a in enumerate(args):
            if a == "-t" and i + 1 < len(args):
                tdir = args[i + 1]
            elif a.startswith("--target-directory="):
                tdir = a.split("=", 1)[1]
        pos = nonopts(args)
        srcs = pos if tdir else pos[:-1]
        dest = tdir or (pos[-1] if pos else None)
        if dest and ":" not in dest.split("/")[0]:
            classify_write(resolve(dest, vc), ctx, out, "%s -> %s" % (prog, dest))
        for s in srcs:
            classify_read(resolve(s, vc), ctx, out, "%s %s" % (prog, s))
    elif prog in ("sed", "perl") and (any(a.startswith("-i") or a == "--in-place" for a in args)
                                      or (prog == "perl" and short_has(args, "i"))):
        for a in script_files(args, prog):
            classify_write(resolve(a, vc), ctx, out, "%s -i %s" % (prog, a))
    elif prog in READERS:
        files = script_files(args, prog) if prog in PATTERN_FIRST else nonopts(args)
        for a in files:
            classify_read(resolve(a, vc), ctx, out, "%s %s" % (prog, a))
    elif prog in ("curl", "wget"):
        for i, a in enumerate(args):
            nxt = args[i + 1] if i + 1 < len(args) else None
            if prog == "curl" and a in ("-o", "--output") and nxt:
                classify_write(resolve(nxt, vc), ctx, out, "curl -o " + nxt)
            elif prog == "wget" and a in ("-O", "--output-document", "-P", "--directory-prefix") and nxt:
                classify_write(resolve(nxt, vc), ctx, out, "wget %s %s" % (a, nxt))
            elif a.startswith("--output-document=") or a.startswith("--directory-prefix="):
                classify_write(resolve(a.split("=", 1)[1], vc), ctx, out, a)
            if a in ("-T", "--upload-file") and nxt:
                classify_read(resolve(nxt, vc), ctx, out, "upload " + nxt)
            m = re.search(r"(?:^|=)@([^;,\"']+)", a)
            if m:
                classify_read(resolve(m.group(1), vc), ctx, out, "upload @" + m.group(1))


def handle_rm(args, ctx, st, out):
    recursive = short_has(args, "r") or short_has(args, "R") or "--recursive" in args
    targets = nonopts(args)
    critical, outside_tmp, unresolved = False, False, False
    for a in targets:
        p = resolve(a, st["vcwd"])
        if p is None:
            unresolved = True
            continue
        if recursive:
            if _is_fs_root(p) or p == HOME or p == ctx.ws or under(ctx.ws, p) \
                    or p == os.path.join(ctx.ws, ".git") or p == os.path.realpath(ZCODE_DIR) \
                    or p == SSH_DIR:
                critical = True
            elif under(p, ctx.ws) or not any(under(p, r) for r in ctx.allow_roots):
                outside_tmp = True   # inside workspace, or outside every scratch root
        classify_write(p, ctx, out, "rm " + a)
    if recursive:
        if critical:
            add(out, "G-RM-CRITICAL", "rm -r " + " ".join(targets)[:80])
        elif outside_tmp or unresolved or not targets:
            add(out, "G-RM-RECURSIVE", "rm -r " + " ".join(targets)[:80])


def analyze_string(s, ctx, st, depth, out):
    if depth > 3:
        return
    s = strip_heredocs(s.replace("\\\n", " "))
    for inner in extract_subshells(s):
        analyze_string(inner, ctx, dict(st), depth + 1, out)
    try:
        toks = tokenize(newlines_to_semis(s))
    except ValueError:
        add(out, "NOTE-parse-failed", clip(s, 80))
        fallback_scan(s, out)
        return
    for c in parse_commands(toks):
        handle_cmd(c, ctx, st, depth, out)


def fallback_scan(raw, out):
    if re.search(r"\bgit\s+(?:-\S+\s+)*push\b", raw):
        add(out, "G-PUSH", "(fallback) git push")
    if re.search(r"\brm\s+-[a-zA-Z]*[rR]", raw):
        add(out, "G-RM-RECURSIVE", "(fallback) rm -r")
    if re.search(r"\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b", raw) and re.search(r"\b(?:curl|wget)\b", raw):
        add(out, "G-PIPE-TO-SHELL", "(fallback)")
    if re.search(r"\bsudo\b", raw):
        add(out, "G-SUDO", "(fallback)")


RAW_CHECKS = [
    (re.compile(r"\b(?:DROP\s+(?:TABLE|DATABASE|SCHEMA|INDEX)|TRUNCATE\s+TABLE)\b", re.I), "G-SQL-DESTRUCTIVE"),
    (re.compile(r"\b(?:ba|z|da)?sh\b[^|;&\n]*-\w*c\s+[\"']?\$\(\s*(?:curl|wget)\b"), "G-PIPE-TO-SHELL"),
    (re.compile(r"\b(?:eval|source)\s+[\"']?\$\(\s*(?:curl|wget)\b"), "G-PIPE-TO-SHELL"),
]


def scan_home_outside(s, ctx, out):
    """Raw heuristic: mentions of $HOME-subpaths outside fs_root (python -c, eval, obfuscation)."""
    if not ctx.fs_root or not ctx.fs_root.startswith(HOME + os.sep):
        return
    rel_root = os.path.relpath(ctx.fs_root, HOME)
    seg = r"[^\s/\\\"'`;&|<>)]+"
    home_alt = re.escape(HOME) + (r"|%USERPROFILE%" if IS_WINDOWS else "")
    rx = re.compile(r"(?:~|\$HOME|%s)[\\/](%s(?:[\\/]%s){0,3})" % (home_alt, seg, seg))
    seen = set()
    for m in rx.finditer(s):
        rel = m.group(1)
        if not rel or rel in seen:
            continue
        seen.add(rel)
        if re.split(r"[/\\]", rel)[0] == rel_root:
            continue
        if any(under(os.path.join(HOME, rel), z) for z in ctx.read_zones):
            continue
        add(out, "G-READ-OUTSIDE", "raw ~/" + rel)


def analyze_bash(cmd, ctx):
    out = []
    st = {"vcwd": ctx.cwd, "branch": None, "dl": False}
    analyze_string(cmd, ctx, st, 0, out)
    for rx, rule in RAW_CHECKS:
        if rx.search(cmd):
            add(out, rule, clip(cmd, 60))
    if ctx.fs_root:
        scan_home_outside(strip_heredocs(cmd.replace("\\\n", " ")), ctx, out)
    return out


# ----------------------------------------------------------------- decision
WRITE_TOOLS = {"write", "edit", "multiedit", "notebookedit"}
READ_TOOLS = {"read", "glob", "grep", "ls", "notebookread"}
PATH_KEYS = ("file_path", "path", "notebook_path", "directory")


def token_fresh(cfg):
    try:
        age = time.time() - os.path.getmtime(TOKEN_FILE)
        return age <= cfg["allow_once_ttl_min"] * 60
    except OSError:
        return False


def decide(payload, cfg):
    tool = str(payload.get("tool_name") or payload.get("toolName") or "")
    ti = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(ti, dict):
        ti = {}
    cwd = payload.get("cwd") or os.getcwd()
    ws = os.environ.get("ZCODE_PROJECT_DIR") or cwd
    ctx = Ctx(ws, cwd, cfg)
    tl = tool.lower()
    out, subject = [], ""
    if tl in ("bash", "shell"):
        subject = str(ti.get("command", ""))
        out = analyze_bash(subject, ctx)
    elif tl in WRITE_TOOLS or (tl not in READ_TOOLS and any(k in tl for k in ("write", "edit", "patch"))):
        for k in PATH_KEYS:
            if ti.get(k):
                subject = str(ti[k])
                classify_write(resolve(subject, ctx.cwd), ctx, out, subject)
                break
    elif tl in READ_TOOLS:
        for k in PATH_KEYS:
            if ti.get(k):
                subject = str(ti[k])
                classify_read(resolve(subject, ctx.cwd), ctx, out, subject)
                break
    else:
        add(out, "NOTE-unmonitored-tool", tool)

    seen, findings = set(), []
    for rule, detail in out:
        if (rule, detail) not in seen and rule not in cfg["disabled_rules"]:
            seen.add((rule, detail))
            findings.append((rule, detail))
    token_used = False
    if findings and token_fresh(cfg):
        kept = [f for f in findings if f[0] not in LEVEL_C]
        token_used = len(kept) != len(findings)
        findings = kept
    actions = [(SEVERITY[RULES[r][1]] if r in RULES else SEVERITY["note"], r, d) for r, d in findings]
    top = max([a[0] for a in actions], default=0)
    decision = {3: "deny", 2: "ask"}.get(top, "allow")
    blocking = [(r, d) for s, r, d in actions if s >= 2]
    return {"decision": decision, "findings": findings, "blocking": blocking, "tool": tool,
            "subject": subject, "token_used": token_used, "progs": ctx.progs}


def build_reason(res, cfg):
    parts = []
    for r, d in res["blocking"][:3]:
        lvl, _, why = RULES[r]
        parts.append("[%s, Level %s] %s (%s)" % (r, lvl, why, clip(d, 80)))
    msg = "Hub guard: " + "; ".join(parts) + ". Не пытайся обойти запрет другой командой или инструментом: " \
        "сообщи пользователю и попроси явное подтверждение."
    if all(r in LEVEL_C for r, _ in res["blocking"]):
        msg += " Пользователь может разрешить Level C на %d мин., создав файл %s" % (
            cfg["allow_once_ttl_min"], TOKEN_FILE.replace(HOME, "~"))
    return msg


def effective(res, cfg):
    d = res["decision"]
    if d == "ask" and not cfg["ask_supported"]:
        return "deny" if cfg["ask_fallback"] == "deny" else "allow"
    return d


def emit(decision, reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason}}, ensure_ascii=False))


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in DEFAULTS:
                if k in data and type(data[k]) is type(DEFAULTS[k]):
                    cfg[k] = data[k]
    except (OSError, ValueError):
        pass
    if cfg["mode"] not in ("off", "shadow", "enforce"):
        cfg["mode"] = "shadow"
    return cfg


def write_log(rec):
    try:
        os.makedirs(GUARD_DIR, mode=0o700, exist_ok=True)
        try:
            if os.path.getsize(LOG_FILE) > 5 * 1024 * 1024:
                os.replace(LOG_FILE, LOG_FILE + ".1")
        except OSError:
            pass
        fd = os.open(LOG_FILE, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass


def hook_main():
    cfg = load_config()
    if cfg["mode"] == "off":
        return
    raw = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
    t0 = time.time()
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "mode": cfg["mode"]}
    try:
        payload = json.loads(raw)
        res = decide(payload, cfg)
    except Exception as e:  # fail-open
        rec["error"] = clip(repr(e), 160)
        write_log(rec)
        return
    eff = effective(res, cfg)
    rec.update({
        "sid": str(payload.get("session_id", ""))[-8:], "zmode": payload.get("permission_mode"),
        "tool": res["tool"], "would": res["decision"], "effective": eff if cfg["mode"] == "enforce" else "allow",
        "rules": [r for r, _ in res["findings"]], "token_used": res["token_used"],
    })
    if res["findings"]:
        rec["detail"] = [clip(d, 100) for _, d in res["findings"][:3]]
        rec["subject"] = clip(res["subject"], 200)
    elif res["tool"].lower() in ("bash", "shell"):
        rec["progs"] = res["progs"][:12]
    rec["ms"] = int((time.time() - t0) * 1000)
    write_log(rec)
    if cfg["mode"] == "enforce" and eff in ("deny", "ask"):
        emit(eff, build_reason(res, cfg))


# ----------------------------------------------------------------- manual subcommands
def read_log(days):
    rows, cutoff = [], time.time() - days * 86400
    for path in (LOG_FILE + ".1", LOG_FILE):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        if time.mktime(time.strptime(r["ts"], "%Y-%m-%dT%H:%M:%S%z")) >= cutoff:
                            rows.append(r)
                    except Exception:
                        continue
        except OSError:
            continue
    return rows


def report(days):
    rows = read_log(days)
    print("guard report: last %d day(s), %d calls, config mode: %s" % (days, len(rows), load_config()["mode"]))
    if not rows:
        return
    errs = [r for r in rows if "error" in r]
    flagged = [r for r in rows if r.get("would") in ("deny", "ask")]
    print("flagged: %d (%.1f%%), internal errors: %d" % (len(flagged), 100.0 * len(flagged) / len(rows), len(errs)))
    by = {}
    for r in flagged:
        for rule in r.get("rules", []):
            if rule in RULES:
                by.setdefault(rule, []).append(r)
    for rule, lst in sorted(by.items(), key=lambda kv: -len(kv[1])):
        lvl, act, _ = RULES[rule]
        print("\n%s  Level %s  default=%s  x%d  (sessions: %d)" % (
            rule, lvl, act, len(lst), len({r.get("sid") for r in lst})))
        for r in lst[-3:]:
            print("   %s  %s  %s" % (r["ts"][5:16], r.get("tool"), clip(r.get("subject", ""), 100)))
    progs = {}
    for r in rows:
        for p in r.get("progs", []):
            progs[p] = progs.get(p, 0) + 1
    if progs:
        print("\nprograms in allowed Bash calls (coverage check):")
        print("  " + ", ".join("%s:%d" % kv for kv in sorted(progs.items(), key=lambda kv: -kv[1])[:20]))
    unm = sorted({r.get("detail", [""])[0] for r in rows if "NOTE-unmonitored-tool" in r.get("rules", [])})
    if unm:
        print("\nunmonitored tools seen: " + ", ".join(unm))
    for e in errs[-3:]:
        print("\nERROR %s: %s" % (e["ts"], e["error"]))


def doctor():
    cfg = load_config()
    print("config file:  %s (%s)" % (CONFIG_FILE, "exists" if os.path.exists(CONFIG_FILE) else "missing, defaults"))
    print("mode:         %s   ask_supported=%s   ask_fallback=%s" % (cfg["mode"], cfg["ask_supported"], cfg["ask_fallback"]))
    print("zcode dir:    %s (HOME fallback: %s)" % (ZCODE_DIR, os.path.join(HOME, ".zcode")))
    print("level C token:%s" % (" FRESH (Level C currently allowed)" if token_fresh(cfg) else " absent/expired"))
    zc = os.path.join(ZCODE_DIR, "cli", "config.json")
    try:
        with open(zc, encoding="utf-8") as f:
            h = json.load(f).get("hooks", {})
        print("hooks.enabled=%s   guard registered in PreToolUse: %s" % (
            h.get("enabled"), "hooks/guard/guard.py" in json.dumps(h.get("events", {}).get("PreToolUse", []))))
    except Exception as e:
        print("cannot read hooks section of %s: %s" % (zc, e.__class__.__name__))
    rows = read_log(1)
    print("calls logged in last 24h: %d" % len(rows))
    if rows:
        print("last call: %s   errors: %d" % (rows[-1]["ts"], sum(1 for r in rows if "error" in r)))
    zlog = os.path.join(ZCODE_DIR, "cli", "log", "zcode-%s.jsonl" % time.strftime("%Y-%m-%d"))
    bad = 0
    try:
        with open(zlog, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "config.file.invalid" in line or "hook.run.failed" in line:
                    bad += 1
        print("ZCode log today: %d line(s) with config.file.invalid / hook.run.failed" % bad)
    except OSError:
        print("ZCode log today: not found (%s)" % zlog.replace(HOME, "~"))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--report":
        report(int(sys.argv[2]) if len(sys.argv) > 2 else 7)
    elif len(sys.argv) > 1 and sys.argv[1] == "--doctor":
        doctor()
    else:
        try:
            hook_main()
        except Exception:
            pass
        sys.exit(0)
