"""SWE-bench Verified tasks for the live agent: select candidates, build one arm64 image each, validate with the
gold patch.

    python -m lab.live.tasks select   [--n 16]
    python -m lab.live.tasks build    [--ids ...] [--force]
    python -m lab.live.tasks validate [--ids ...] [--keep 10]
    python -m lab.live.tasks list
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.request

from lab.live.sandbox import Sandbox
from lab.transcript import ROOT

DATA = ROOT / "data" / "live"
HF_ROWS = "https://datasets-server.huggingface.co/rows?dataset=princeton-nlp/SWE-bench_Verified&config=default&split=test"
BASE_IMAGE = "csl-live-base:py{py}"
TEST_TIMEOUT = 900
MAX_TEST_SECONDS = 300  # validated tasks with slower hidden tests are not used
QUOTA = {"pytest-dev/pytest": 6, "sympy/sympy": 6, "psf/requests": 4}  # candidates per repo
KEEP = {"pytest-dev/pytest": 4, "sympy/sympy": 4, "psf/requests": 2}    # validated tasks per repo
# sympy test files that take minutes under bin/test; avoided at selection
SLOW_TESTS = {"test_polytools.py", "test_arit.py", "test_latex.py", "test_pretty.py", "test_matrices.py", "test_solveset.py",
              "test_continuous_rv.py", "test_numbers.py", "test_sets.py", "test_simplify.py", "test_fu.py", "test_codegen.py"}
NON_TEST_EXTS = (".json", ".png", "csv", ".txt", ".md", ".jpg", ".jpeg", ".pkl", ".yml", ".yaml", ".toml")

# ------------------------------------------------------------------------------------------------ environment specs
# Ported from SWE-bench v4.1.0 swebench/harness/constants/python.py (SPECS_PYTEST / SPECS_SYMPY / SPECS_REQUESTS) and
# the per-instance environment.yml pins in swebench/resources/swebench-og (all three repos use Python 3.9).

_P = {"attrs": "attrs==23.1.0", "mi": "more-itertools==10.1.0", "pk": "packaging==23.1", "pl13": "pluggy==0.13.1",
      "py": "py==1.11.0", "six": "six==1.16.0", "wc": "wcwidth==0.2.6", "aw": "atomicwrites==1.4.1", "ini": "iniconfig==2.0.0",
      "toml": "toml==0.10.2", "tomli": "tomli==2.0.1"}
_PYTEST_PINS = {
    "4.4": ["aw", "attrs", "mi", "pl13", "py", "setuptools==68.0.0", "six"],
    "4.5": ["aw", "attrs", "mi", "pluggy==0.11.0", "py", "setuptools==68.0.0", "six", "wc"],
    "4.6": ["aw", "attrs", "mi", "pk", "pl13", "py", "six", "wc"],
    **{k: ["aw", "attrs", "mi", "pk", "pl13", "py", "wc"] for k in ("5.0", "5.1", "5.2")},
    "5.3": ["attrs", "mi", "pk", "pl13", "py", "wc"],
    "5.4": ["py", "pk", "attrs", "mi", "pl13"],
    "6.0": ["attrs", "ini", "mi", "pk", "pl13", "py", "toml"],
    **{k: ["attrs", "ini", "pk", "pl13", "py", "toml"] for k in ("6.2", "6.3")},
    "7.0": ["attrs", "ini", "pk", "pl13", "py"],
    **{k: ["attrs", "ini", "pk", "pl13", "py", "tomli"] for k in ("7.1", "7.2")},
    **{k: ["ini", "pk", "pluggy==1.3.0", "exceptiongroup==1.1.3", "tomli"] for k in ("7.4", "8.0", "8.1", "8.2", "8.3", "8.4")},
}
_REQUESTS_NEW = ["certifi==2024.8.30", "charset-normalizer==2.0.12", "idna==3.10", "urllib3==1.26.20"]


def spec_for(repo: str, version: str) -> dict:
    """{python, pre_install, pip, install, test_cmd, parser, env}"""
    if repo == "pytest-dev/pytest":
        pins = [_P.get(p, p) for p in _PYTEST_PINS.get(version, _PYTEST_PINS["7.2"])]
        return {"python": "3.9", "pre_install": ["sed -i 's/>=>=/>=/' setup.cfg"] if version == "6.3" else [], "pip": pins,
                "install": "pip install -e .", "test_cmd": "pytest -rA", "parser": "pytest",
                # a depth-1 clone has no tags: give setuptools-scm the release line so `minversion` checks still pass
                "env": {"SETUPTOOLS_SCM_PRETEND_VERSION": f"{version}.0"}}
    if repo == "sympy/sympy":
        return {"python": "3.9", "pre_install": [], "pip": ["mpmath==1.3.0", "flake8-comprehensions==3.15.0"],
                "install": "pip install -e .", "parser": "sympy", "env": {},
                "test_cmd": "PYTHONWARNINGS='ignore::UserWarning,ignore::SyntaxWarning' bin/test -C --verbose"}
    if repo == "psf/requests":
        new = tuple(int(x) for x in version.split(".")) >= (2, 26)
        return {"python": "3.9", "pre_install": [], "pip": ["pytest==7.4.4"] + (_REQUESTS_NEW if new else []),
                "install": "pip install -e .", "test_cmd": "pytest -rA", "parser": "pytest_options", "env": {}}
    raise KeyError(f"no environment spec for {repo}")


# ------------------------------------------------------------------------------------------------ log parsers
# Ported from SWE-bench v4.1.0 swebench/harness/log_parsers/python.py.

STATUSES = ("FAILED", "PASSED", "SKIPPED", "ERROR", "XFAIL")


def parse_log_pytest(log: str) -> dict[str, str]:
    out = {}
    for line in log.split("\n"):
        if line.startswith(STATUSES):
            if line.startswith("FAILED"):
                line = line.replace(" - ", " ")
            parts = line.split()
            if len(parts) > 1:
                out[parts[1]] = parts[0]
    return out


def parse_log_pytest_options(log: str) -> dict[str, str]:
    option = re.compile(r"(.*?)\[(.*)\]")
    out = {}
    for line in log.split("\n"):
        if line.startswith(STATUSES):
            if line.startswith("FAILED"):
                line = line.replace(" - ", " ")
            parts = line.split()
            if len(parts) <= 1:
                continue
            m = option.search(parts[1])
            if m:
                main, opt = m.groups()
                if opt.startswith("/") and not opt.startswith("//") and "*" not in opt:
                    opt = "/" + opt.split("/")[-1]
                out[f"{main}[{opt}]"] = parts[0]
            else:
                out[parts[1]] = parts[0]
    return out


def parse_log_sympy(log: str) -> dict[str, str]:
    out = {}
    for m in re.findall(r"(_*) (.*)\.py:(.*) (_*)", log):
        out[f"{m[1]}.py:{m[2]}"] = "FAILED"
    for line in log.split("\n"):
        line = line.strip()
        if line.startswith("test_"):
            name = line.split()[0]
            if line.endswith(" E"):
                out[name] = "ERROR"
            if line.endswith(" F"):
                out[name] = "FAILED"
            if line.endswith(" ok"):
                out[name] = "PASSED"
    return out


PARSERS = {"pytest": parse_log_pytest, "pytest_options": parse_log_pytest_options, "sympy": parse_log_sympy}


def grade(status: dict[str, str], f2p: list[str], p2p: list[str]) -> dict:
    """SWE-bench grading: pass = PASSED/XFAIL; fail = FAILED/ERROR or missing; SKIPPED counts as neither."""
    def split(tests):
        ok = [t for t in tests if status.get(t) in ("PASSED", "XFAIL")]
        bad = [t for t in tests if t not in status or status[t] in ("FAILED", "ERROR")]
        return ok, bad
    f_ok, f_bad = split(f2p)
    p_ok, p_bad = split(p2p)
    return {"f2p_pass": f_ok, "f2p_fail": f_bad, "p2p_pass": p_ok, "p2p_fail": p_bad,
            "resolved": bool(f2p) and not f_bad and not p_bad}


# ------------------------------------------------------------------------------------------------ patches / tests

def patch_files(patch: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"^diff --git a/.* b/(.*)$", patch, flags=re.M)))


def new_files(patch: str) -> list[str]:
    """Files a patch creates (they do not exist at the base commit)."""
    out, cur = [], None
    for line in patch.split("\n"):
        m = re.match(r"^diff --git a/.* b/(.*)$", line)
        if m:
            cur = m.group(1)
        elif line.startswith("new file mode") and cur:
            out.append(cur)
    return out


def test_directives(test_patch: str) -> list[str]:
    return [f for f in patch_files(test_patch) if not f.endswith(NON_TEST_EXTS)]


def is_test_path(path: str) -> bool:
    p = path.lower()
    name = p.rsplit("/", 1)[-1]
    return (bool(re.search(r"(^|/)(tests?|testing)/", p)) or name.startswith("test_") or name.endswith("_test.py")
            or name == "conftest.py")


def reset_tests_cmd(task: dict) -> str:
    """Restore the test files the test patch touches to HEAD (and remove the ones it creates)."""
    created = set(new_files(task["test_patch"]))
    existing = [f for f in task["test_files"] if f not in created]
    parts = []
    if existing:
        parts.append("git checkout HEAD -- " + " ".join(existing))
    if created:
        parts.append("rm -f " + " ".join(created))
    return " && ".join(parts) or "true"


def run_tests(sb: Sandbox, task: dict, timeout: int = TEST_TIMEOUT) -> dict:
    """Reset test files, apply the test patch, run the task's test command; parsed per-test statuses."""
    code, out = sb.exec(reset_tests_cmd(task), timeout=120)
    if code != 0:
        return {"ok": False, "error": f"reset tests failed: {out[-500:]}", "status": {}}
    ok, out = sb.apply_patch(task["test_patch"])
    if not ok:
        return {"ok": False, "error": f"test patch failed: {out[-800:]}", "status": {}}
    t0 = time.time()
    code, log = sb.exec(task["test_cmd"], timeout=timeout, cap=None)
    status = PARSERS[task["parser"]](log)
    return {"ok": True, "exit": code, "duration_s": round(time.time() - t0, 1), "status": status, "log_tail": log[-4000:],
            "timed_out": code == 124}


# ------------------------------------------------------------------------------------------------ select

def fetch_verified() -> list[dict]:
    cache = DATA / "verified.json"
    if cache.exists():
        return json.loads(cache.read_text())
    rows, offset = [], 0
    while True:
        with urllib.request.urlopen(f"{HF_ROWS}&offset={offset}&length=100", timeout=120) as r:
            page = json.loads(r.read())
        rows += [x["row"] for x in page["rows"]]
        offset += 100
        if offset >= page["num_rows_total"] or not page["rows"]:
            break
    DATA.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows))
    return rows


def _candidate_rank(r: dict) -> tuple | None:
    f2p, p2p = json.loads(r["FAIL_TO_PASS"]), json.loads(r["PASS_TO_PASS"])
    tests = test_directives(r["test_patch"])
    gold = patch_files(r["patch"])
    if r["difficulty"] not in ("15 min - 1 hour", "<15 min fix") or not 1 <= len(f2p) <= 3 or len(gold) > 2:
        return None
    if len(tests) > 2 or any(t.rsplit("/", 1)[-1] in SLOW_TESTS for t in tests) or len(p2p) > 200:
        return None
    if not 150 <= len(r["problem_statement"]) <= 6000:
        return None
    return (r["difficulty"] != "15 min - 1 hour", len(f2p), len(tests), len(p2p))


def select(n: int | None = None) -> list[dict]:
    rows = fetch_verified()
    out = []
    for repo, quota in QUOTA.items():
        ranked = sorted(((k, r) for r in rows if r["repo"] == repo and (k := _candidate_rank(r))), key=lambda kr: (kr[0], kr[1]["instance_id"]))
        picked, versions = [], set()
        for spread in (True, False):  # first one per release line, then fill
            for _, r in ranked:
                if len(picked) >= quota:
                    break
                if r in picked or (spread and r["version"] in versions):
                    continue
                picked.append(r)
                versions.add(r["version"])
        out += picked
    if n:
        out = out[:n]
    cands = [{"instance_id": r["instance_id"], "repo": r["repo"], "version": r["version"], "base_commit": r["base_commit"],
              "difficulty": r["difficulty"], "problem_statement": r["problem_statement"], "patch": r["patch"],
              "test_patch": r["test_patch"], "test_files": patch_files(r["test_patch"]),
              "FAIL_TO_PASS": json.loads(r["FAIL_TO_PASS"]), "PASS_TO_PASS": json.loads(r["PASS_TO_PASS"]),
              "image": image_tag(r["instance_id"])} for r in out]
    (DATA / "candidates.json").write_text(json.dumps(cands, indent=1))
    for c in cands:
        print(f"{c['instance_id']:<28} v{c['version']:<5} {c['difficulty']:<16} f2p={len(c['FAIL_TO_PASS'])} "
              f"p2p={len(c['PASS_TO_PASS'])} tests={c['test_files']}")
    print(f"{len(cands)} candidates -> {(DATA / 'candidates.json').relative_to(ROOT)}")
    return cands


# ------------------------------------------------------------------------------------------------ build

def image_tag(instance_id: str) -> str:
    return f"csl-live:{instance_id.lower()}"


def _docker_build(tag: str, dockerfile: str, timeout: int = 1800) -> tuple[bool, str]:
    r = subprocess.run(["docker", "build", "--progress=plain", "-t", tag, "-"], input=dockerfile.encode(),
                       capture_output=True, timeout=timeout)
    return r.returncode == 0, (r.stdout + r.stderr).decode("utf-8", "replace")


def _image_exists(tag: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0


def base_dockerfile(py: str) -> str:
    return f"""FROM python:{py}-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep && rm -rf /var/lib/apt/lists/* \\
 && git config --global user.email agent@localhost && git config --global user.name agent \\
 && git config --global init.defaultBranch main && git config --global advice.detachedHead false
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
"""


def dockerfile(c: dict) -> str:
    s = spec_for(c["repo"], c["version"])
    env = " ".join(f"{k}={v}" for k, v in s["env"].items())
    steps = s["pre_install"] + ([f"pip install {' '.join(s['pip'])}"] if s["pip"] else []) + [f"{env} {s['install']}".strip()]
    return f"""FROM {BASE_IMAGE.format(py=s['python'])}
WORKDIR /testbed
RUN git init -q . && git fetch -q --depth 1 https://github.com/{c['repo']} {c['base_commit']} && git checkout -q FETCH_HEAD \\
 && git checkout -q -b main
RUN {' && '.join(steps)}
RUN printf '__pycache__/\\n*.pyc\\n.pytest_cache/\\n*.egg-info/\\n.eggs/\\n' >> .git/info/exclude \\
 && git add -A && git commit -q --allow-empty -m "environment setup" && test -z "$(git status --porcelain)"
"""


def build(ids: list[str] | None = None, force: bool = False) -> dict:
    cands = json.loads((DATA / "candidates.json").read_text())
    cands = [c for c in cands if not ids or c["instance_id"] in ids]
    report_path = DATA / "build.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    for py in sorted({spec_for(c["repo"], c["version"])["python"] for c in cands}):
        tag = BASE_IMAGE.format(py=py)
        if force or not _image_exists(tag):
            ok, log = _docker_build(tag, base_dockerfile(py))
            print(f"{tag}: {'ok' if ok else 'FAILED'}")
            if not ok:
                print(log[-3000:])
                raise SystemExit(1)
    for c in cands:
        tag = c["image"]
        if not force and _image_exists(tag) and report.get(c["instance_id"], {}).get("ok"):
            print(f"{tag}: exists")
            continue
        t0 = time.time()
        ok, log = _docker_build(tag, dockerfile(c))
        report[c["instance_id"]] = {"ok": ok, "image": tag, "seconds": round(time.time() - t0), "log_tail": log[-3000:]}
        report_path.write_text(json.dumps(report, indent=1))
        print(f"{tag}: {'ok' if ok else 'FAILED'} ({time.time() - t0:.0f}s)", flush=True)
        if not ok:
            print("   " + log[-1500:].replace("\n", "\n   "))
    return report


# ------------------------------------------------------------------------------------------------ validate

def validate_one(c: dict) -> dict:
    s = spec_for(c["repo"], c["version"])
    task = {**c, "test_cmd": " ".join([s["test_cmd"], *test_directives(c["test_patch"])]), "parser": s["parser"]}
    res = {"instance_id": c["instance_id"], "ok": False}
    with Sandbox(c["image"]) as sb:  # (a) test patch only: FAIL_TO_PASS must fail
        before = run_tests(sb, task)
    if not before["ok"]:
        return {**res, "reason": before["error"]}
    g0 = grade(before["status"], c["FAIL_TO_PASS"], [])
    res["before"] = {"f2p_pass": g0["f2p_pass"], "duration_s": before["duration_s"], "exit": before["exit"]}
    if g0["f2p_pass"]:
        return {**res, "reason": f"FAIL_TO_PASS already passing before the fix: {g0['f2p_pass']}"}
    with Sandbox(c["image"]) as sb:  # (b) gold patch + test patch: everything must pass
        ok, out = sb.apply_patch(c["patch"])
        if not ok:
            return {**res, "reason": f"gold patch failed: {out[-500:]}"}
        after = run_tests(sb, task)
    if not after["ok"]:
        return {**res, "reason": after["error"]}
    g1 = grade(after["status"], c["FAIL_TO_PASS"], c["PASS_TO_PASS"])
    res["after"] = {"duration_s": after["duration_s"], "exit": after["exit"], "f2p_fail": g1["f2p_fail"],
                    "p2p_dropped": g1["p2p_fail"], "timed_out": after["timed_out"]}
    if after["timed_out"]:
        return {**res, "reason": "tests timed out"}
    if g1["f2p_fail"]:
        return {**res, "reason": f"FAIL_TO_PASS failing with the gold patch: {g1['f2p_fail']}", "log_tail": after["log_tail"][-1500:]}
    p2p = [t for t in c["PASS_TO_PASS"] if t not in g1["p2p_fail"]]
    res.update(ok=True, task={"instance_id": c["instance_id"], "repo": c["repo"], "version": c["version"], "image": c["image"],
                              "problem_statement": c["problem_statement"], "test_patch": c["test_patch"],
                              "test_files": c["test_files"], "FAIL_TO_PASS": c["FAIL_TO_PASS"], "PASS_TO_PASS": p2p,
                              "PASS_TO_PASS_dropped": g1["p2p_fail"], "test_cmd": task["test_cmd"], "parser": s["parser"],
                              "difficulty": c["difficulty"], "test_seconds": after["duration_s"]})
    return res


def validate(ids: list[str] | None = None, keep: int | None = None) -> list[dict]:
    cands = json.loads((DATA / "candidates.json").read_text())
    built = json.loads((DATA / "build.json").read_text()) if (DATA / "build.json").exists() else {}
    report_path = DATA / "validation.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    for c in cands:
        iid = c["instance_id"]
        if (ids and iid not in ids) or not built.get(iid, {}).get("ok"):
            continue
        t0 = time.time()
        try:
            r = validate_one(c)
        except Exception as e:  # noqa: BLE001 - recorded, next candidate
            r = {"instance_id": iid, "ok": False, "reason": f"{type(e).__name__}: {e}"}
        r["seconds"] = round(time.time() - t0)
        report[iid] = r
        report_path.write_text(json.dumps(report, indent=1))
        print(f"{iid:<28} {'VALID' if r['ok'] else 'invalid'}  {r.get('reason', '')[:150]}  "
              f"(tests {r.get('after', {}).get('duration_s', '-')}s, dropped p2p {len(r.get('after', {}).get('p2p_dropped', []))})", flush=True)
    valid = [report[c["instance_id"]]["task"] for c in cands if report.get(c["instance_id"], {}).get("ok")]
    tasks, per = [], {}
    valid = [t for t in valid if t["test_seconds"] <= MAX_TEST_SECONDS]
    for t in valid:  # balanced across repos, in selection-rank order
        if per.get(t["repo"], 0) < KEEP.get(t["repo"], 0):
            tasks.append(t)
            per[t["repo"]] = per.get(t["repo"], 0) + 1
    for t in valid:  # fill up to `keep` if a repo is short
        if len(tasks) < (keep or sum(KEEP.values())) and t not in tasks:
            tasks.append(t)
    tasks = tasks[: keep or sum(KEEP.values())]
    tasks.sort(key=lambda t: t["instance_id"])
    (DATA / "tasks.json").write_text(json.dumps(tasks, indent=1))
    print(f"{len(valid)} valid of {len(report)} checked; {len(tasks)} tasks -> {(DATA / 'tasks.json').relative_to(ROOT)} {per}")
    return tasks


def load_tasks(ids: list[str] | None = None) -> list[dict]:
    tasks = json.loads((DATA / "tasks.json").read_text())
    if ids and ids != ["all"]:
        tasks = [t for t in tasks if t["instance_id"] in ids]
    return tasks


def cmd_list() -> None:
    p = DATA / "tasks.json"
    if not p.exists():
        print("no tasks.json yet (run select, build, validate)")
        return
    for t in json.loads(p.read_text()):
        print(f"{t['instance_id']:<28} {t['difficulty']:<16} f2p={len(t['FAIL_TO_PASS'])} p2p={len(t['PASS_TO_PASS'])} "
              f"(dropped {len(t['PASS_TO_PASS_dropped'])})  tests {t['test_seconds']}s  {t['image']}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="lab.live.tasks")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--n", type=int, default=None)
    b = sub.add_parser("build")
    b.add_argument("--ids", nargs="+")
    b.add_argument("--force", action="store_true")
    v = sub.add_parser("validate")
    v.add_argument("--ids", nargs="+")
    v.add_argument("--keep", type=int, default=None)
    sub.add_parser("list")
    a = p.parse_args(argv)
    DATA.mkdir(parents=True, exist_ok=True)
    if a.cmd == "select":
        select(a.n)
    elif a.cmd == "build":
        build(a.ids, a.force)
    elif a.cmd == "validate":
        validate(a.ids, a.keep)
    else:
        cmd_list()


if __name__ == "__main__":
    main()
