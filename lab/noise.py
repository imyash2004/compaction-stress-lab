"""Seeded generators for realistic, noisy coding-agent tool output.

Every function takes a `random.Random` so the same seed always produces the same
bytes. Generators never emit the scenario's answer values on their own; the
scenario injects those explicitly through `inserts` / `specials` arguments.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

INCIDENT_DAY = datetime(2026, 10, 8, 13, 50, 0, tzinfo=timezone.utc)

# Values the scenario uses as answers or decoys. Random filler must never produce them.
RESERVED_INTS = {5432, 5433, 6432, 9100, 9187, 4200, 4020, 1284, 1248, 412, 409, 408, 405, 768, 512, 800, 250, 2500, 41, 42, 40, 25,
                 137, 4821, 4812, 4790, 422, 423}

PACKAGES = [
    ("fastapi", "0.115.6"), ("starlette", "0.41.3"), ("pydantic", "2.9.2"), ("pydantic-core", "2.23.4"),
    ("pydantic-settings", "2.4.0"), ("uvicorn", "0.32.1"), ("sqlalchemy", "2.0.36"), ("alembic", "1.14.0"),
    ("redis", "5.2.0"), ("httpx", "0.27.2"), ("anyio", "4.6.2.post1"), ("sniffio", "1.3.1"), ("idna", "3.10"),
    ("certifi", "2024.8.30"), ("h11", "0.14.0"), ("click", "8.1.7"), ("orjson", "3.10.11"), ("structlog", "24.4.0"),
    ("prometheus-client", "0.21.0"), ("opentelemetry-api", "1.28.2"), ("opentelemetry-sdk", "1.28.2"),
    ("opentelemetry-instrumentation-fastapi", "0.49b2"), ("tenacity", "9.0.0"), ("stripe", "11.2.0"),
    ("cryptography", "43.0.3"), ("cffi", "1.17.1"), ("pycparser", "2.22"), ("python-jose", "3.3.0"),
    ("ecdsa", "0.19.0"), ("rsa", "4.9"), ("pyasn1", "0.6.1"), ("boto3", "1.35.63"), ("botocore", "1.35.63"),
    ("jmespath", "1.0.1"), ("s3transfer", "0.10.3"), ("urllib3", "2.2.3"), ("python-dateutil", "2.9.0.post0"),
    ("six", "1.16.0"), ("pytest", "8.3.3"), ("pytest-asyncio", "0.24.0"), ("pytest-cov", "6.0.0"),
    ("coverage", "7.6.7"), ("pytest-xdist", "3.6.1"), ("execnet", "2.1.1"), ("freezegun", "1.5.1"),
    ("factory-boy", "3.3.1"), ("faker", "33.0.0"), ("respx", "0.21.1"), ("mypy", "1.13.0"),
    ("mypy-extensions", "1.0.0"), ("typing-extensions", "4.12.2"), ("ruff", "0.7.4"), ("greenlet", "3.1.1"),
    ("mako", "1.3.6"), ("markupsafe", "3.0.2"), ("packaging", "24.2"), ("pluggy", "1.5.0"),
    ("iniconfig", "2.0.0"), ("deprecated", "1.2.15"), ("wrapt", "1.16.0"), ("importlib-metadata", "8.5.0"),
    ("zipp", "3.21.0"), ("protobuf", "5.28.3"), ("googleapis-common-protos", "1.66.0"), ("grpcio", "1.68.0"),
    ("opentelemetry-exporter-otlp", "1.28.2"), ("opentelemetry-proto", "1.28.2"), ("asgiref", "3.8.1"),
    ("watchfiles", "0.24.0"), ("websockets", "14.1"), ("httptools", "0.6.4"), ("uvloop", "0.21.0"),
    ("python-multipart", "0.0.17"), ("email-validator", "2.2.0"), ("dnspython", "2.7.0"), ("hiredis", "3.0.0"),
    ("tzdata", "2024.2"), ("psutil", "6.1.0"), ("rich", "13.9.4"), ("markdown-it-py", "3.0.0"), ("mdurl", "0.1.2"),
    ("pygments", "2.18.0"), ("tomli", "2.1.0"), ("pyyaml", "6.0.2"), ("jinja2", "3.1.4"), ("itsdangerous", "2.2.0"),
    ("aiofiles", "24.1.0"), ("cachetools", "5.5.0"), ("google-auth", "2.36.0"), ("kubernetes", "31.0.0"),
    ("requests", "2.32.3"), ("requests-oauthlib", "2.0.0"), ("oauthlib", "3.2.2"), ("charset-normalizer", "3.4.0"),
]

SERVICES_K8S = [
    "payments-api", "ledger-worker", "checkout-web", "refund-service", "fraud-scorer", "notification-relay",
    "invoice-renderer", "fx-rates", "tax-engine", "audit-exporter", "settlement-batch", "risk-gateway",
    "customer-profile", "merchant-portal", "webhook-dispatcher", "kyc-adapter", "pricing-rules", "coupon-service",
]

SRC_MODULES = [
    "src/payments/api.py", "src/payments/db.py", "src/payments/settings.py", "src/payments/models.py",
    "src/payments/schemas.py", "src/payments/ledger.py", "src/payments/webhooks.py", "src/payments/refunds.py",
    "src/payments/fx.py", "src/payments/fraud.py", "src/payments/cache.py", "src/payments/metrics.py",
    "src/payments/tracing.py", "src/payments/auth.py", "src/payments/errors.py", "src/payments/clients/stripe.py",
    "src/payments/clients/ledger_rpc.py", "src/payments/jobs/backfill.py", "src/payments/jobs/reconcile.py",
    "src/ledger_worker/consumer.py", "src/ledger_worker/handlers.py", "src/ledger_worker/settings.py",
]

TEST_MODULES = [
    "tests/payments/test_api.py", "tests/payments/test_ledger.py", "tests/payments/test_webhooks.py",
    "tests/payments/test_refunds.py", "tests/payments/test_fx.py", "tests/payments/test_fraud.py",
    "tests/payments/test_settings.py", "tests/payments/test_schemas.py", "tests/payments/test_cache.py",
    "tests/payments/test_auth.py", "tests/payments/test_backfill.py", "tests/ledger_worker/test_consumer.py",
    "tests/ledger_worker/test_handlers.py", "tests/integration/test_checkout_flow.py",
    "tests/integration/test_refund_flow.py", "tests/integration/test_webhook_flow.py",
]

WORDS = [
    "amount", "currency", "merchant", "order", "customer", "capture", "authorize", "refund", "ledger", "entry",
    "balance", "settlement", "payout", "invoice", "tax", "fx_rate", "webhook", "signature", "retry", "backoff",
    "timeout", "session", "token", "cursor", "batch", "partition", "offset", "checksum", "status", "reason",
    "region", "tenant", "account", "reference", "metadata", "created_at", "updated_at", "trace_id", "span",
]


def rint(rng: random.Random, lo: int, hi: int) -> int:
    """Random int in [lo, hi] that is never one of the scenario's reserved values."""
    while True:
        v = rng.randint(lo, hi)
        if v not in RESERVED_INTS:
            return v


def hexs(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(n))


def iso(dt: datetime, ms: bool = False) -> str:
    if ms:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def ident(rng: random.Random, k: int = 2) -> str:
    return "_".join(rng.choice(WORDS) for _ in range(k))


def insert_at(lines: list[str], inserts: list[tuple[float, str]]) -> list[str]:
    """Insert lines at relative positions (0.0 = top, 1.0 = bottom), stable for equal positions."""
    out = list(lines)
    for frac, text in sorted(inserts, key=lambda x: x[0], reverse=True):
        idx = max(0, min(len(out), round(frac * len(out))))
        out[idx:idx] = text.split("\n")
    return out


def numbered(text: str, start: int = 1) -> str:
    """Render file content the way a read-file tool does: right-aligned line numbers + tab."""
    return "\n".join(f"{i:>6}\t{line}" for i, line in enumerate(text.split("\n"), start))


# ---------------------------------------------------------------- pip

def pip_install(rng: random.Random, n_pkgs: int, conflict: str, freeze_lines: int, freeze_must: dict[str, str]) -> str:
    pkgs = PACKAGES[:]
    rng.shuffle(pkgs)
    pkgs = (pkgs * (n_pkgs // len(pkgs) + 1))[:n_pkgs]
    lines = ["$ pip install -r requirements.txt -r requirements-dev.txt && pip freeze"]
    for name, ver in pkgs:
        if rng.random() < 0.45:
            parent = rng.choice(PACKAGES)[0]
            lines.append(f"Requirement already satisfied: {name}=={ver} in ./.venv/lib/python3.12/site-packages (from {parent}) ({ver})")
            continue
        size = rng.randint(12, 4800)
        unit = "kB" if size < 1000 else "MB"
        shown = size if size < 1000 else round(size / 1000, 1)
        lines.append(f"Collecting {name}=={ver}")
        lines.append(f"  Downloading {name.replace('-', '_')}-{ver}-py3-none-any.whl.metadata ({rng.randint(2, 40)} kB)")
        lines.append(f"  Downloading {name.replace('-', '_')}-{ver}-py3-none-any.whl ({shown} {unit})")
        lines.append(f"     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ {shown}/{shown} {unit} {round(rng.uniform(1, 60), 1)} MB/s eta 0:00:00")
    lines.append("Installing collected packages: " + ", ".join(n for n, _ in pkgs[: min(60, len(pkgs))]))
    lines.append("  Attempting uninstall: psycopg")
    lines.append("    Found existing installation: psycopg 3.2.1")
    lines.append("    Uninstalling psycopg-3.2.1:")
    lines.append("      Successfully uninstalled psycopg-3.2.1")
    lines.extend(conflict.split("\n"))
    lines.append("Successfully installed " + " ".join(f"{n}-{v}" for n, v in pkgs[: min(45, len(pkgs))]))
    freeze = {n: v for n, v in PACKAGES}
    freeze.update(freeze_must)
    names = sorted(freeze)
    extra = [f"{ident(rng, 1)}-{ident(rng, 1)}".replace("_", "-") for _ in range(max(0, freeze_lines - len(names)))]
    for e in extra:
        freeze.setdefault(e, f"{rng.randint(0, 9)}.{rng.randint(0, 30)}.{rng.randint(0, 12)}")
    for name in sorted(freeze)[: max(freeze_lines, len(freeze_must))]:
        lines.append(f"{name}=={freeze[name]}")
    for name, ver in freeze_must.items():  # make sure required pins are visible even when freeze is truncated
        line = f"{name}=={ver}"
        if line not in lines:
            lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------- pytest

def _test_names(rng: random.Random, n: int) -> list[str]:
    names = []
    for i in range(n):
        mod = TEST_MODULES[i % len(TEST_MODULES)]
        names.append(f"{mod}::test_{ident(rng, rng.randint(2, 4))}_{i}")
    return names


def pytest_run(
    rng: random.Random,
    passed: int,
    failed: list[tuple[str, str]],
    skipped: int,
    verbose: bool,
    cmd: str = "pytest -q",
    seconds: float | None = None,
) -> str:
    total = passed + len(failed) + skipped
    names = _test_names(rng, passed + skipped)
    lines = [f"$ {cmd}", "============================= test session starts ==============================",
             "platform linux -- Python 3.12.7, pytest-8.3.3, pluggy-1.5.0",
             "rootdir: /workspace/payments-api", "configfile: pyproject.toml",
             "plugins: asyncio-0.24.0, cov-6.0.0, xdist-3.6.1, anyio-4.6.2.post1",
             f"collected {total} items", ""]
    statuses = ["PASSED"] * passed + ["SKIPPED"] * skipped
    rng.shuffle(statuses)
    fail_names = [f for f, _ in failed]
    if verbose:
        rows = list(zip(names, statuses)) + [(f, "FAILED") for f in fail_names]
        rng.shuffle(rows)
        for i, (n, s) in enumerate(rows, 1):
            lines.append(f"{n} {s} [{round(100 * i / len(rows)):>3}%]")
    else:
        chars = ["." if s == "PASSED" else "s" for s in statuses] + ["F"] * len(failed)
        rng.shuffle(chars)
        for i in range(0, len(chars), 80):
            chunk = chars[i:i + 80]
            lines.append("".join(chunk) + f" [{round(100 * min(len(chars), i + 80) / len(chars)):>3}%]")
    if failed:
        lines += ["", "=================================== FAILURES ==================================="]
        for name, err in failed:
            short = name.split("::")[-1]
            lines.append(f"_{'_' * 20} {short} {'_' * 20}")
            lines.extend(err.split("\n"))
        lines += ["", "=========================== short test summary info ============================"]
        for name, err in failed:
            lines.append(f"FAILED {name} - {err.strip().split(chr(10))[-1][:110]}")
    secs = seconds if seconds is not None else round(rng.uniform(40, 140), 2)
    parts = []
    if failed:
        parts.append(f"{len(failed)} failed")
    parts.append(f"{passed} passed")
    if skipped:
        parts.append(f"{skipped} skipped")
    lines.append(f"================== {', '.join(parts)} in {secs}s ==================")
    return "\n".join(lines)


# ---------------------------------------------------------------- logs

def service_logs(rng: random.Random, start: datetime, end: datetime, n_lines: int,
                 inserts: list[tuple[datetime, str]], cmd: str) -> str:
    """Interleaved Kubernetes-style service logs between `start` and `end`.

    `inserts` are (timestamp, text) pairs merged in time order; a `{ts}` placeholder in the text
    is replaced by the formatted timestamp.
    """
    span_ms = int((end - start).total_seconds() * 1000)
    times = sorted(start + timedelta(milliseconds=rng.randint(0, span_ms)) for _ in range(n_lines))
    rows: list[tuple[datetime, str]] = []
    for t in times:
        rows.append((t, _log_line(rng, t)))
    for t, text in inserts:
        rows.append((t, text.replace("{ts}", iso(t, ms=True))))
    rows.sort(key=lambda r: r[0])
    return f"$ {cmd}\n" + "\n".join(text for _, text in rows)


def _log_line(rng: random.Random, t: datetime) -> str:
    svc = rng.choices(["payments-api", "ledger-worker", "pgbouncer", "redis"], [6, 3, 1, 1])[0]
    ts = iso(t, ms=True)
    if svc == "payments-api":
        r = rng.random()
        ip = f"10.244.{rng.randint(0, 9)}.{rng.randint(2, 250)}:{rint(rng, 30000, 65000)}"
        if r < 0.55:
            path = rng.choice(["/v1/checkout", "/v1/payments/capture", "/v1/refunds", "/healthz", "/readyz", "/v1/payments/authorize"])
            code = rng.choice(["200 OK", "200 OK", "200 OK", "201 Created", "204 No Content"])
            return f"{ts} payments-api INFO:     {ip} - \"{rng.choice(['POST', 'GET'])} {path} HTTP/1.1\" {code}"
        if r < 0.8:
            return (f"{ts} payments-api INFO  [payments.ledger] posted entry id=le_{hexs(rng, 12)} amount={rng.randint(100, 99999)} "
                    f"currency={rng.choice(['EUR', 'GBP', 'USD', 'PLN', 'SEK'])} trace={hexs(rng, 16)}")
        return (f"{ts} payments-api DEBUG [payments.cache] cache {rng.choice(['hit', 'miss'])} "
                f"key=merchant:{rng.randint(10000, 99999)}:{ident(rng, 1)} ttl={rng.choice([30, 60, 300, 900])}")
    if svc == "ledger-worker":
        return (f"{ts} ledger-worker DEBUG consumed topic=ledger.events partition={rng.randint(0, 11)} "
                f"offset={rng.randint(10_000_000, 99_999_999)} lag={rng.randint(0, 400)}")
    if svc == "pgbouncer":
        return (f"{ts} pgbouncer LOG stats: {rint(rng, 20, 900)} xacts/s, {rint(rng, 20, 1200)} queries/s, "
                f"in {rint(rng, 10_000, 900_000)} B/s, out {rint(rng, 10_000, 900_000)} B/s, xact {rint(rng, 300, 9000)} us, "
                f"query {rint(rng, 200, 7000)} us, wait {rint(rng, 0, 900)} us")
    return (f"{ts} redis 1:M {t.strftime('%d %b %Y %H:%M:%S')}.{t.microsecond // 1000:03d} * "
            f"{rng.choice([100, 1000, 10000])} changes in {rng.choice([60, 300, 900])} seconds. Saving...")


def postgres_errors(rng: random.Random, n_lines: int, inserts: list[tuple[float, str]], cmd: str) -> str:
    lines = []
    t = INCIDENT_DAY + timedelta(hours=1)
    for _ in range(n_lines):
        t += timedelta(milliseconds=rng.randint(50, 3000))
        pid = rint(rng, 1000, 99999)
        stamp = t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 1000:03d} UTC"
        r = rng.random()
        if r < 0.5:
            lines.append(f"{stamp} [{pid}] LOG:  duration: {round(rng.uniform(0.2, 900), 3)} ms  statement: SELECT {ident(rng)} FROM {rng.choice(['ledger_entries', 'payments', 'refunds', 'merchants'])} WHERE id = ${rng.randint(1, 4)}")
        elif r < 0.75:
            lines.append(f"{stamp} [{pid}] LOG:  connection authorized: user=payments database=payments application_name={rng.choice(SERVICES_K8S)}")
        else:
            lines.append(f"{stamp} [{pid}] LOG:  disconnection: session time: 0:00:{rng.randint(1, 59):02d}.{rng.randint(0, 999):03d} user=payments database=payments host=10.244.{rng.randint(0, 9)}.{rng.randint(2, 250)} port={rint(rng, 30000, 65000)}")
    return f"$ {cmd}\n" + "\n".join(insert_at(lines, inserts))


# ---------------------------------------------------------------- traceback

LIB_FRAMES = [
    ("/usr/local/lib/python3.12/site-packages/starlette/routing.py", "handle", "await self.app(scope, receive, send)"),
    ("/usr/local/lib/python3.12/site-packages/starlette/routing.py", "app", "response = await f(request)"),
    ("/usr/local/lib/python3.12/site-packages/fastapi/routing.py", "run_endpoint_function", "return await dependant.call(**values)"),
    ("/usr/local/lib/python3.12/site-packages/starlette/middleware/base.py", "call_next", "raise app_exc"),
    ("/usr/local/lib/python3.12/site-packages/starlette/middleware/exceptions.py", "__call__", "await self.app(scope, receive, sender)"),
    ("/usr/local/lib/python3.12/site-packages/opentelemetry/instrumentation/asgi/__init__.py", "__call__", "await self.app(scope, otel_receive, otel_send)"),
    ("/usr/local/lib/python3.12/site-packages/tenacity/asyncio/__init__.py", "__call__", "result = await fn(*args, **kwargs)"),
    ("/usr/local/lib/python3.12/site-packages/tenacity/__init__.py", "iter", "result = action(retry_state)"),
    ("/usr/local/lib/python3.12/site-packages/sqlalchemy/ext/asyncio/session.py", "flush", "await greenlet_spawn(self.sync_session.flush, objects=objects)"),
    ("/usr/local/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", "greenlet_spawn", "result = context.switch(*args, **kwargs)"),
    ("/usr/local/lib/python3.12/site-packages/sqlalchemy/orm/session.py", "flush", "self._flush(objects)"),
    ("/usr/local/lib/python3.12/site-packages/sqlalchemy/orm/session.py", "_flush", "with util.safe_reraise():"),
    ("/usr/local/lib/python3.12/site-packages/sqlalchemy/util/langhelpers.py", "__exit__", "raise exc_value.with_traceback(exc_tb)"),
    ("/usr/local/lib/python3.12/site-packages/sqlalchemy/orm/unitofwork.py", "execute", "rec.execute(self)"),
    ("/usr/local/lib/python3.12/site-packages/sqlalchemy/orm/persistence.py", "save_obj", "_emit_insert_statements("),
    ("/usr/local/lib/python3.12/site-packages/sqlalchemy/engine/base.py", "execute", "return meth("),
    ("/usr/local/lib/python3.12/site-packages/sqlalchemy/engine/base.py", "_execute_context", "return self._exec_single_context("),
    ("/usr/local/lib/python3.12/site-packages/sqlalchemy/engine/base.py", "_exec_single_context", "self._handle_dbapi_exception("),
    ("/usr/local/lib/python3.12/site-packages/sqlalchemy/engine/default.py", "do_execute", "cursor.execute(statement, parameters)"),
    ("/usr/local/lib/python3.12/site-packages/psycopg/cursor.py", "execute", "raise ex.with_traceback(None)"),
]


def traceback_block(
    rng: random.Random, n_frames: int, key_frames: list[tuple[float, str, int, str, str]], exc: str, header: str = ""
) -> str:
    """A long Python traceback. `key_frames` are (relative position, path, line, func, code)."""
    frames = []
    for _ in range(n_frames):
        path, func, code = rng.choice(LIB_FRAMES)
        frames.append(f'  File "{path}", line {rint(rng, 60, 2400)}, in {func}\n    {code}')
    for frac, path, line, func, code in sorted(key_frames, key=lambda k: k[0], reverse=True):
        idx = max(0, min(len(frames), round(frac * len(frames))))
        frames.insert(idx, f'  File "/app/{path}", line {line}, in {func}\n    {code}')
    out = [header] if header else []
    out.append("Traceback (most recent call last):")
    out.extend(frames)
    out.extend(exc.split("\n"))
    return "\n".join(out)


# ---------------------------------------------------------------- git

AUTHORS = ["Priya Raman <priya.raman@acme-pay.dev>", "Tomasz Nowak <tomasz.nowak@acme-pay.dev>",
           "Lena Fischer <lena.fischer@acme-pay.dev>", "Diego Alvarez <diego.alvarez@acme-pay.dev>",
           "Aisha Bello <aisha.bello@acme-pay.dev>", "Kenji Sato <kenji.sato@acme-pay.dev>",
           "renovate[bot] <bot@renovateapp.com>"]
SUBJECT_TEMPLATES = [
    "feat({a}): add {b} to {c}", "fix({a}): handle {b} when {c} is missing", "chore(deps): bump {b} in /{a}",
    "refactor({a}): extract {b} helper", "test({a}): cover {b} edge case", "docs: update {a} runbook",
    "perf({a}): cache {b} lookups", "ci: retry flaky {a} job", "fix({a}): correct {b} rounding",
]


def git_log(rng: random.Random, n: int, start: datetime, specials: list[tuple[float, dict]], cmd: str) -> str:
    entries = []
    t = start
    for _ in range(n):
        t -= timedelta(minutes=rng.randint(20, 900))
        subj = rng.choice(SUBJECT_TEMPLATES).format(a=rng.choice(["payments", "ledger", "webhooks", "refunds", "fx", "ci", "api"]),
                                                    b=ident(rng, 1), c=ident(rng, 1))
        if rng.random() < 0.35:
            subj += f" (PAY-{rint(rng, 3000, 4999)})"
        entries.append({"hash": hexs(rng, 40), "author": rng.choice(AUTHORS), "date": t, "subject": subj, "body": ""})
    for frac, e in sorted(specials, key=lambda s: s[0], reverse=True):
        idx = max(0, min(len(entries), round(frac * len(entries))))
        e = dict(e)
        e.setdefault("author", rng.choice(AUTHORS[:-1]))
        e.setdefault("date", entries[max(0, idx - 1)]["date"] if entries else start)
        e.setdefault("body", "")
        entries.insert(idx, e)
    out = [f"$ {cmd}"]
    for e in entries:
        out.append(f"commit {e['hash']}")
        out.append(f"Author: {e['author']}")
        out.append(f"Date:   {e['date'].strftime('%a %b %d %H:%M:%S %Y +0000')}")
        out.append("")
        out.append(f"    {e['subject']}")
        if e["body"]:
            out.append("")
            out.extend(f"    {b}" for b in e["body"].split("\n"))
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- kubernetes

def kubectl_pods_wide(rng: random.Random, n: int, specials: list[str], nodes: list[str]) -> str:
    header = "NAME                                   READY   STATUS             RESTARTS        AGE     IP             NODE                                NOMINATED NODE   READINESS GATES"
    rows = []
    for _ in range(n):
        svc = rng.choice(SERVICES_K8S[2:])
        name = f"{svc}-{hexs(rng, 9)}-{hexs(rng, 5)}"
        rows.append(f"{name:<38} 1/1     Running            {rng.randint(0, 3):<15} {rng.randint(1, 40)}d     10.244.{rng.randint(0, 9)}.{rng.randint(2, 250):<5} {rng.choice(nodes):<35} <none>           <none>")
    for s in specials:
        rows.insert(rng.randint(0, len(rows)), s)
    return "$ kubectl -n payments get pods -o wide\n" + header + "\n" + "\n".join(rows)


def kubectl_events(rng: random.Random, n: int) -> str:
    reasons = [("Normal", "Scheduled", "Successfully assigned payments/{p} to {node}"),
               ("Normal", "Pulled", "Container image \"acmepay.azurecr.io/{svc}:{tag}\" already present on machine"),
               ("Normal", "Created", "Created container {svc}"), ("Normal", "Started", "Started container {svc}"),
               ("Warning", "Unhealthy", "Readiness probe failed: Get \"http://10.244.{a}.{b}:8080/readyz\": context deadline exceeded"),
               ("Normal", "Killing", "Stopping container {svc}"), ("Normal", "SuccessfulCreate", "Created pod: {p}"),
               ("Normal", "ScalingReplicaSet", "Scaled up replica set {svc}-{h} to {n}")]
    lines = ["$ kubectl -n payments get events --sort-by=.lastTimestamp", "LAST SEEN   TYPE      REASON              OBJECT                                      MESSAGE"]
    for _ in range(n):
        typ, reason, msg = rng.choice(reasons)
        svc = rng.choice(SERVICES_K8S[2:])
        p = f"{svc}-{hexs(rng, 9)}-{hexs(rng, 5)}"
        msg = msg.format(p=p, node=f"aks-general-{rng.randint(10000000, 99999999)}-vmss0000{rng.randint(0, 9)}", svc=svc,
                         tag=f"2026.10.{rng.randint(1, 8)}-{rng.randint(1, 9)}", a=rng.randint(0, 9), b=rng.randint(2, 250),
                         h=hexs(rng, 9), n=rng.randint(2, 8))
        lines.append(f"{rng.randint(1, 59)}m{'':<8}{typ:<9} {reason:<19} pod/{p:<40} {msg}")
    return "\n".join(lines)


def kubectl_svc(rng: random.Random, n: int, specials: list[str]) -> str:
    lines = ["$ kubectl -n payments get svc -o wide",
             "NAME                   TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)              AGE    SELECTOR"]
    rows = []
    for svc in rng.sample(SERVICES_K8S[2:], min(n, len(SERVICES_K8S) - 2)):
        ports = f"{rint(rng, 8000, 8099)}/TCP,{rint(rng, 9101, 9186)}/TCP"
        rows.append(f"{svc:<22} ClusterIP   10.0.{rng.randint(0, 255)}.{rng.randint(1, 254):<7} <none>        {ports:<20} {rng.randint(20, 400)}d   app={svc}")
    for s in specials:
        rows.insert(rng.randint(0, len(rows)), s)
    return "\n".join(lines + rows)


# ---------------------------------------------------------------- repo noise

def ls_tree(rng: random.Random, n: int) -> str:
    dirs = ["src/payments", "src/payments/clients", "src/payments/jobs", "src/ledger_worker", "tests/payments",
            "tests/integration", "tests/ledger_worker", "migrations/versions", "deploy/helm/payments/templates",
            "deploy/k8s", "config", "scripts", "docs/adr", ".github/workflows"]
    out = ["$ find . -type f -not -path './.git/*' -not -path './.venv/*' | sort"]
    files = set(SRC_MODULES + TEST_MODULES)
    while len(files) < n:
        d = rng.choice(dirs)
        ext = rng.choice([".py", ".py", ".py", ".yaml", ".md", ".json", ".sql"])
        files.add(f"{d}/{ident(rng, rng.randint(1, 3))}{ext}")
    out.extend(f"./{f}" for f in sorted(files))
    return "\n".join(out)


def grep_hits(rng: random.Random, pattern: str, n: int, specials: list[str]) -> str:
    out = [f"$ grep -rn \"{pattern}\" src tests"]
    rows = []
    for _ in range(n):
        f = rng.choice(SRC_MODULES + TEST_MODULES)
        rows.append(f"{f}:{rint(rng, 3, 900)}:    {rng.choice(['', 'self.', 'await '])}{ident(rng)}({pattern}={ident(rng, 1)})")
    for s in specials:
        rows.insert(rng.randint(0, len(rows)), s)
    return "\n".join(out + rows)


def python_module(rng: random.Random, n_lines: int, inject: dict[int, str]) -> str:
    """Plausible Python source; `inject` maps 1-based line numbers to exact code lines."""
    lines = ['"""Auto-maintained module. See docs/adr for design notes."""', "from __future__ import annotations", "",
             "import logging", "from dataclasses import dataclass", "from typing import Any", "",
             "log = logging.getLogger(__name__)", ""]
    while len(lines) < n_lines:
        fn = ident(rng, rng.randint(2, 3))
        args = ", ".join(ident(rng, 1) for _ in range(rng.randint(1, 3)))
        lines.append(f"def {fn}({args}) -> Any:")
        lines.append(f'    """{rng.choice(["Return", "Compute", "Load", "Validate", "Normalize"])} the {ident(rng, 1)} for a {ident(rng, 1)}."""')
        for _ in range(rng.randint(2, 9)):
            v = ident(rng, 1)
            lines.append(rng.choice([
                f"    {v} = {ident(rng, 1)}.get(\"{ident(rng, 1)}\")",
                f"    if not {v}:\n        raise ValueError(\"missing {v}\")",
                f"    log.debug(\"{fn}: %s\", {v})",
                f"    {v} = round({ident(rng, 1)} * {rint(rng, 2, 99)} / {rint(rng, 100, 1000)}, 2)",
                f"    for item in {ident(rng, 1)}:\n        {v}.append(item)",
            ]))
        lines.append(f"    return {ident(rng, 1)}")
        lines.append("")
    text = "\n".join(lines).split("\n")[:max(n_lines, max(inject or {0: 0}))]
    while len(text) < max(inject or {0: 0}):
        text.append("")
    for ln, code in inject.items():
        text[ln - 1] = code
    return "\n".join(text)


def yaml_config(rng: random.Random, service: str, n_keys: int, fixed: dict[str, str]) -> str:
    lines = [f"# {service} configuration", f"service: {service}", ""]
    keys = dict(fixed)
    while len(keys) < n_keys:
        keys.setdefault(f"{ident(rng, 2)}", rng.choice([str(rint(rng, 2, 9000)), "true", "false", f'"{ident(rng, 1)}"']))
    for k, v in keys.items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


def docker_build(rng: random.Random, steps: int, image: str) -> str:
    out = [f"$ docker build -t {image} .", "#0 building with \"default\" instance using docker driver", "",
           "#1 [internal] load build definition from Dockerfile", "#1 transferring dockerfile: 1.42kB done", "#1 DONE 0.0s"]
    for i in range(2, steps + 2):
        out.append(f"#{i} [{i - 1}/{steps}] {rng.choice(['RUN pip install --no-cache-dir -r requirements.txt', 'COPY src/ /app/src/', 'RUN apt-get update && apt-get install -y --no-install-recommends libpq5', 'WORKDIR /app', 'COPY pyproject.toml requirements.txt /app/'])}")
        out.append(f"#{i} sha256:{hexs(rng, 64)} {round(rng.uniform(0.1, 60), 2)}MB / {round(rng.uniform(60, 200), 2)}MB {round(rng.uniform(0.2, 9), 1)}s")
        out.append(f"#{i} CACHED" if rng.random() < 0.4 else f"#{i} DONE {round(rng.uniform(0.1, 40), 1)}s")
    out.append(f"#{steps + 2} exporting to image")
    out.append(f"#{steps + 2} writing image sha256:{hexs(rng, 64)} done")
    out.append(f"#{steps + 2} naming to docker.io/library/{image} done")
    return "\n".join(out)


def helm_history(rows: list[tuple[int, str, str, str, str]]) -> str:
    out = ["$ helm -n payments history payments --max 15",
           "REVISION\tUPDATED                 \tSTATUS    \tCHART          \tAPP VERSION   \tDESCRIPTION"]
    for rev, when, status, app, desc in rows:
        out.append(f"{rev:<8}\t{when}\t{status:<10}\tpayments-3.14.0\t{app:<14}\t{desc}")
    return "\n".join(out)


def json_blob(rng: random.Random, n_items: int) -> str:
    items = []
    for _ in range(n_items):
        items.append("  {" + ", ".join(f"\"{ident(rng, 1)}\": {rng.choice([str(rint(rng, 1, 99999)), 'null', 'true', chr(34) + hexs(rng, 10) + chr(34)])}" for _ in range(rng.randint(4, 9))) + "}")
    return "[\n" + ",\n".join(items) + "\n]"


# ---------------------------------------------------------------- detours (pure filler steps)

def detour(rng: random.Random, level: dict) -> tuple[str, str, dict, str]:
    """One unrelated-but-plausible tool call: (assistant text, tool name, args, output). Contains no answers."""
    kind = rng.choice(["read", "grep", "events", "build", "unit", "config", "top", "curl", "typo", "pull"])
    if kind == "read":
        path = rng.choice([m for m in SRC_MODULES if "retry" not in m and "idempotency" not in m and "settings" not in m])
        body = python_module(rng, level["file_lines"], {})
        return (f"Let me check {path} for related logic.", "read_file", {"path": path}, numbered(body))
    if kind == "grep":
        pat = ident(rng, 1)
        return (f"Searching for other uses of `{pat}`.", "bash", {"command": f"grep -rn \"{pat}\" src tests"},
                grep_hits(rng, pat, level["grep_hits"], []))
    if kind == "events":
        return ("Checking recent cluster events.", "bash", {"command": "kubectl -n payments get events --sort-by=.lastTimestamp"},
                kubectl_events(rng, level["events"]))
    if kind == "build":
        return ("Rebuilding the image to make sure it still builds.", "bash", {"command": "docker build -t payments-api:dev ."},
                docker_build(rng, level["build_steps"], "payments-api:dev"))
    if kind == "unit":
        mod = rng.choice([m for m in TEST_MODULES if "integration" not in m])
        n = rng.randint(15, 60)
        return (f"Running the unit tests in {mod}.", "bash", {"command": f"pytest {mod} -v"},
                pytest_run(rng, n, [], rng.randint(0, 3), True, cmd=f"pytest {mod} -v"))
    if kind == "config":
        svc = rng.choice(SERVICES_K8S[4:])
        return (f"Looking at the {svc} config for comparison.", "read_file", {"path": f"config/{svc}.yaml"},
                numbered(yaml_config(rng, svc, level["config_keys"], {})))
    if kind == "top":
        rows = ["$ kubectl -n payments top pods", "NAME                                   CPU(cores)   MEMORY(bytes)"]
        for _ in range(level["events"] // 2):
            svc = rng.choice(SERVICES_K8S[2:])
            rows.append(f"{svc}-{hexs(rng, 9)}-{hexs(rng, 5):<12} {rint(rng, 2, 900)}m{'':<9}{rint(rng, 40, 700)}Mi")
        return ("Checking resource usage across pods.", "bash", {"command": "kubectl -n payments top pods"}, "\n".join(rows))
    if kind == "curl":
        return ("Pulling a sample of merchant settings from the admin API.", "bash",
                {"command": "curl -s http://localhost:8080/admin/merchants?limit=50 | jq ."}, json_blob(rng, level["json_items"]))
    if kind == "typo":
        return ("Listing pods in the namespace.", "bash", {"command": "kubectl -n payment get pods"},
                "$ kubectl -n payment get pods\nNo resources found in payment namespace.\n(exit code 0)")
    lines = ["$ docker compose pull"]
    for _ in range(level["events"]):
        svc = rng.choice(["postgres", "redis", "pgbouncer", "payments-api", "ledger-worker", "jaeger", "prometheus"])
        lines.append(f" {svc} Pulling ")
        lines.append(f" {hexs(rng, 12)} Downloading [{'=' * rng.randint(1, 49)}>{' ' * rng.randint(0, 10)}]  {rng.randint(1, 90)}.{rng.randint(0, 9)}MB/{rng.randint(91, 300)}.{rng.randint(0, 9)}MB")
        lines.append(f" {hexs(rng, 12)} Pull complete ")
    return ("Pulling the latest local images.", "bash", {"command": "docker compose pull"}, "\n".join(lines))
