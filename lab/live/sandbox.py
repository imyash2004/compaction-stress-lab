"""Docker sandbox for the live agent: one container per run, no network, repository at /testbed."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid

WORKDIR = "/testbed"
OUTPUT_CAP = 12_000
READ_MAX_LINES = 400
LABEL = "csl-live=1"


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def truncate(text: str, cap: int = OUTPUT_CAP) -> str:
    """Keep head and tail; the marker says how much was cut."""
    if len(text) <= cap:
        return text
    head, tail = cap // 2, cap - cap // 2
    return f"{text[:head]}\n[... {len(text) - head - tail} chars truncated ...]\n{text[-tail:]}"


_READ = r"""
import json, sys
a = json.loads(sys.stdin.read())
try:
    with open(a["path"], encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
except IsADirectoryError:
    print("Error: %s is a directory (use bash ls)" % a["path"]); sys.exit(2)
except OSError as e:
    print("Error: %s" % e); sys.exit(2)
if lines and lines[-1] == "":
    lines.pop()
n = len(lines)
s = max(1, a["start"] or 1)
e = min(n, a["end"] or n, s + a["max"] - 1)
if n == 0:
    print("(empty file)"); sys.exit(0)
if s > n:
    print("Error: start_line %d is past the end of the file (%d lines)" % (s, n)); sys.exit(2)
print("\n".join("%6d\t%s" % (i + 1, lines[i]) for i in range(s - 1, e)))
if e < n and (a["end"] is None or e < a["end"]):
    print("[showing lines %d-%d of %d; call read_file with start_line=%d to continue]" % (s, e, n, e + 1))
"""

_EDIT = r"""
import json, os, sys
a = json.loads(sys.stdin.read())
p, old, new = a["path"], a["old"], a["new"]
if old == "":
    if os.path.exists(p):
        print("Error: old must not be empty when the file exists (%s)" % p); sys.exit(2)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(new)
    print("Created %s (%d lines)" % (p, new.count("\n") + 1)); sys.exit(0)
try:
    with open(p, encoding="utf-8") as f:
        text = f.read()
except OSError as e:
    print("Error: %s" % e); sys.exit(2)
if old == new:
    print("Error: old and new are identical; nothing to do"); sys.exit(2)
k = text.count(old)
if k == 0:
    print("Error: old string not found in %s. It must match the file exactly, including whitespace and indentation; read the file again." % p); sys.exit(2)
if k > 1:
    lines = [text[:m.start()].count("\n") + 1 for m in __import__("re").finditer(__import__("re").escape(old), text)]
    print("Error: old string occurs %d times in %s (lines %s); include more surrounding context to make it unique." % (k, p, ", ".join(map(str, lines[:10])))); sys.exit(2)
i = text.index(old)
text = text[:i] + new + text[i + len(old):]
with open(p, "w", encoding="utf-8") as f:
    f.write(text)
first = text[:i].count("\n") + 1
last = first + new.count("\n")
rows = text.split("\n")
lo, hi = max(1, first - 3), min(len(rows), last + 3)
print("Edited %s (lines %d-%d now):" % (p, first, last))
print("\n".join("%6d\t%s" % (j, rows[j - 1]) for j in range(lo, hi + 1)))
"""


class Sandbox:
    """`with Sandbox(image) as sb: sb.exec("pytest -x")`. Commands run as root in /testbed with bash."""

    def __init__(self, image: str, memory: str = "1g", cpus: str = "1", network: bool = False):
        self.image = image
        self.name = f"csl-live-{uuid.uuid4().hex[:10]}"
        cmd = ["docker", "run", "-d", "--name", self.name, "--label", LABEL, "--memory", memory, "--cpus", cpus,
               "-w", WORKDIR, "--entrypoint", "sleep"]
        if not network:
            cmd += ["--network", "none"]
        r = subprocess.run(cmd + [image, "infinity"], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"docker run failed: {r.stderr.strip()[:500]}")
        self.closed = False
        # the starting commit: diffs are taken against it, so work the agent commits (or stages) is still captured
        code, out = self._run(["git", "rev-parse", "-q", "--verify", "HEAD"], 60, merge_stderr=False)
        self.base = out.strip() if code == 0 and out.strip() else "HEAD"

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _run(self, argv: list[str], timeout: int, stdin: str | None = None, merge_stderr: bool = True) -> tuple[int, str]:
        cmd = ["docker", "exec"] + (["-i"] if stdin is not None else []) + ["-w", WORKDIR, self.name] + argv
        try:
            r = subprocess.run(cmd, input=(stdin or "").encode() if stdin is not None else None, capture_output=True,
                               timeout=timeout + 30)
        except subprocess.TimeoutExpired:  # the in-container `timeout` should have fired first
            return 124, f"[command timed out after {timeout}s]"
        out = r.stdout.decode("utf-8", "replace") + (r.stderr.decode("utf-8", "replace") if merge_stderr else "")
        return r.returncode, out

    def exec(self, cmd: str, timeout: int = 120, cap: int | None = OUTPUT_CAP) -> tuple[int, str]:
        """(exit_code, combined stdout+stderr). A timeout kills the whole process group: exit 124 plus a note."""
        t0 = time.time()
        code, out = self._run(["timeout", "-k", "5", str(timeout), "bash", "-c", cmd], timeout)
        # 124/137 from a `timeout` inside the command itself (it returns early) is the command's own exit code
        if code in (124, 137) and time.time() - t0 >= timeout:
            out = out.rstrip("\n") + f"\n[command timed out after {timeout}s and was killed]"
            code = 124
        return code, truncate(out, cap) if cap else out

    def read(self, path: str, start_line: int | None = None, end_line: int | None = None) -> tuple[bool, str]:
        args = {"path": path, "start": start_line, "end": end_line, "max": READ_MAX_LINES}
        code, out = self._run(["python3", "-c", _READ], 60, stdin=json.dumps(args))
        return code == 0, truncate(out)

    def edit(self, path: str, old: str, new: str) -> tuple[bool, str]:
        code, out = self._run(["python3", "-c", _EDIT], 60, stdin=json.dumps({"path": path, "old": old, "new": new}))
        return code == 0, truncate(out)

    def write_file(self, path: str, text: str) -> None:
        code, out = self._run(["bash", "-c", f"cat > {path}"], 60, stdin=text)
        if code != 0:
            raise RuntimeError(f"write {path}: {out[:300]}")

    def apply_patch(self, text: str) -> tuple[bool, str]:
        if not text.strip():
            return True, "(empty patch)"
        code, out = self._run(["git", "apply", "-v", "--whitespace=nowarn", "-"], 120, stdin=text)
        if code != 0:  # retry with 3-way merge (handles small offsets against the setup commit)
            code2, out2 = self._run(["git", "apply", "-v", "--3way", "--whitespace=nowarn", "-"], 120, stdin=text)
            if code2 == 0:
                return True, out2
            out += "\n" + out2
        return code == 0, out

    def _untracked(self) -> list[str]:
        _, out = self._run(["git", "ls-files", "--others", "--exclude-standard", "-z"], 60, merge_stderr=False)
        return [p for p in out.split("\0") if p]

    def changed_files(self) -> dict:
        """Paths relative to /testbed, against the starting commit (committed, staged and unstaged work alike):
        modified, added (new tracked or untracked, not ignored) and deleted files."""
        _, out = self._run(["git", "-c", "core.fileMode=false", "diff", "--name-status", "--no-renames", "-z", self.base], 60,
                           merge_stderr=False)
        parts = [p for p in out.split("\0") if p]
        mod, add, dele = [], [], []
        for st, p in zip(parts[::2], parts[1::2]):
            (add if st == "A" else dele if st == "D" else mod).append(p)
        add += self._untracked()
        return {"modified": sorted(mod), "added": sorted(set(add)), "deleted": sorted(dele)}

    def diff(self) -> str:
        """git diff against the starting commit plus untracked files, as one patch that `git apply` accepts."""
        _, d = self._run(["git", "-c", "core.fileMode=false", "diff", "--binary", self.base], 120, merge_stderr=False)
        for p in self._untracked():
            _, u = self._run(["git", "diff", "--binary", "--no-index", "--", "/dev/null", p], 60, merge_stderr=False)
            d += u
        return d

    def close(self) -> None:
        if not self.closed:
            subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, timeout=120)
            self.closed = True


def cleanup() -> int:
    """Remove leftover sandbox containers (e.g. after a crash)."""
    r = subprocess.run(["docker", "ps", "-aq", "--filter", f"label={LABEL}"], capture_output=True, text=True)
    ids = r.stdout.split()
    if ids:
        subprocess.run(["docker", "rm", "-f", *ids], capture_output=True)
    return len(ids)
