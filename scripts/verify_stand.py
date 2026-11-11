#!/usr/bin/env python3
"""End-to-end smoke verification of a running PPM stand (REST API only, standard library).

Usage (on the VM after `docker compose up -d --build`):
    python3 scripts/verify_stand.py                       # https://localhost:8443/api, self-signed TLS accepted
    python3 scripts/verify_stand.py --lockout             # also runs the 5-failed-logins lockout check (locks `expert2` for 15 min)
    python3 scripts/verify_stand.py --base http://127.0.0.1:8000/api --mailpit http://127.0.0.1:8025

IMPORTANT
* Run it on a FRESH database (`docker compose down -v && docker compose up -d --build`): the scenario step changes
  project statuses, so a second run on the same data will fail at auto-select.
* The TC mapping is indicative. Copy real PASS/FAIL results into the LR6 table; do not mark rows you did not run.
"""
import argparse, concurrent.futures, itertools, json, ssl, sys, urllib.error, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="https://localhost:8443/api")
ap.add_argument("--mailpit", default="http://127.0.0.1:8025", help="Mailpit HTTP API; use '' to skip")
ap.add_argument("--lockout", action="store_true")
args = ap.parse_args()
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
USERS = {"initiator": "initiator123", "expert1": "expert12345", "expert2": "expert23456", "manager": "manager123",
         "committee1": "committee123", "committee2": "committee234", "committee3": "committee345", "admin": "admin12345"}
rows = []


def call(method, path, body=None, token=None, raw=False, base=None):
    req = urllib.request.Request((base or args.base) + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
            data = r.read(); return r.status, (data if raw else (json.loads(data) if data else None))
    except urllib.error.HTTPError as e:
        data = e.read()
        try: return e.code, json.loads(data)
        except Exception: return e.code, data


def check(tc, name, cond, detail=""):
    rows.append((tc, name, bool(cond), detail)); print(("PASS " if cond else "FAIL ") + f"[{tc}] {name} {detail}")


def login(u):
    s, d = call("POST", "/auth/login/", {"username": u, "password": USERS[u]})
    return d["token"] if s == 200 else None


s, d = call("GET", "/health/"); check("health", "GET /api/health/", s == 200, f"-> {s}")
tokens = {u: login(u) for u in USERS}
for u, t in tokens.items(): check("TC-FUNC-01", f"LDAP login of {u}", t is not None)
if not all(tokens.values()): print("\nLogin failed: check `docker compose logs openldap backend`. Stopping."); sys.exit(2)
s, _ = call("POST", "/auth/login/", {"username": "manager", "password": "wrong-password"}); check("TC-SEC-02", "wrong password -> 401", s == 401, f"-> {s}")
s, _ = call("POST", "/scenarios/", {"name": "x", "budget_limit": "1.00"}, tokens["initiator"]); check("TC-SEC-01", "initiator cannot create scenario (403)", s == 403, f"-> {s}")
s, _ = call("GET", "/admin/logs/", None, tokens["manager"]); check("TC-SEC-01", "manager cannot read audit log (403)", s == 403, f"-> {s}")
s, _ = call("GET", "/requests/"); check("TC-SEC-01", "no token -> 401", s in (401, 403), f"-> {s}")

# concurrency: parallel registration must never fail and must never issue a duplicate code (FR-3.1.2); proposed TC-REL-06
draft = {"description": "Заявка для проверки одновременной регистрации кодов.", "department": "ИТ-служба",
         "strategic_goal": "Проверка", "expected_benefit": "1.00", "budget": "1.00", "effort": "1.0"}
ids = [call("POST", "/requests/", {**draft, "title": f"Конкурентная заявка {n:02d}"}, tokens["initiator"])[1].get("id") for n in range(12)]
with concurrent.futures.ThreadPoolExecutor(12) as pool:
    results = list(pool.map(lambda i: call("POST", f"/requests/{i}/submit/", {}, tokens["initiator"]), ids))
codes = [d.get("code") for st, d in results if st == 200]
check("TC-REL-06*", "12 parallel submits: all HTTP 200, codes unique", all(st == 200 for st, _ in results) and len(set(codes)) == len(codes) == 12,
      f"statuses={sorted({st for st, _ in results})} unique={len(set(codes))}/{len(codes)}")

# life cycle of a new request
body = {"title": "Проверка стенда: новая заявка", "description": "Заявка создана скриптом verify_stand.py для проверки жизненного цикла.",
        "department": "ИТ-служба", "strategic_goal": "Проверка", "expected_benefit": "10.00", "budget": "5.00", "effort": "6.0"}
s, d = call("POST", "/requests/", body, tokens["initiator"]); check("TC-FUNC-02", "create draft request", s == 201, f"-> {s}")
rid = d.get("id") if isinstance(d, dict) else None
if rid:
    s, d = call("POST", f"/requests/{rid}/submit/", {}, tokens["initiator"])
    code = (d or {}).get("code") if isinstance(d, dict) else None
    check("TC-FUNC-02", "submit -> code PRJ-YYYY-NNNN", s == 200 and bool(code) and code.startswith("PRJ-"), f"-> {s} {code}")
    s, d = call("PATCH", f"/requests/{rid}/", {"title": "Проверка стенда: правка до оценки"}, tokens["initiator"]); check("TC-FUNC-03", "edit while REGISTERED", s == 200, f"-> {s}")
    s, d = call("POST", f"/requests/{rid}/assign/", {"experts": ["expert1", "expert2"]}, tokens["manager"]); check("TC-FUNC-04", "assign experts -> UNDER_EVALUATION", s == 200, f"-> {s}")
    s, d = call("PATCH", f"/requests/{rid}/", {"title": "после начала оценки"}, tokens["initiator"]); check("TC-REL-05", "edit after evaluation started is refused with HTTP 403", s == 403, f"-> {s}")

# scenario flow on seeded projects
s, d = call("POST", "/scenarios/", {"name": "verify_stand", "budget_limit": "30.00"}, tokens["manager"]); check("TC-BOUND-05", "create scenario 30.00", s == 201, f"-> {s}")
sid = d["scenario"]["id"]
s, d = call("POST", f"/scenarios/{sid}/auto-select/", {}, tokens["manager"]); check("TC-FUNC-06", "auto-select", s == 200, f"-> {s}")
items = d["items"]; inc = [i for i in items if i["included"]]
check("TC-FUNC-06", "sum(budget) <= limit", sum(float(i["budget"]) for i in inc) <= 30.0 + 1e-9)
best = max(sum(float(p["expected_benefit"]) for p in c) for r in range(len(items) + 1) for c in itertools.combinations(items, r) if sum(float(p["budget"]) for p in c) <= 30.0)
check("NFR-6.1.2", "benefit equals brute-force optimum", abs(sum(float(i["expected_benefit"]) for i in inc) - best) < 1e-9, f"{best}")
off = [i for i in items if not i["included"]]
if off:
    call("POST", f"/scenarios/{sid}/toggle/", {"project_id": off[0]["id"], "included": True}, tokens["manager"])
    s, _ = call("POST", f"/scenarios/{sid}/submit/", {}, tokens["manager"]); check("TC-BOUND-05", "over-budget scenario cannot be submitted", s == 400, f"-> {s}")
    call("POST", f"/scenarios/{sid}/toggle/", {"project_id": off[0]["id"], "included": False}, tokens["manager"])
s, _ = call("POST", f"/scenarios/{sid}/submit/", {}, tokens["manager"]); check("FR-3.4.1", "submit to committee", s == 200, f"-> {s}")
for i in inc: call("POST", "/committee/vote/", {"scenario_id": sid, "project_id": i["id"], "decision": "FOR"}, tokens["committee1"])
s, _ = call("POST", "/committee/finalize/", {"scenario_id": sid}, tokens["manager"]); check("TC-BOUND-06", "finalize without quorum (1 of 3) refused", s in (400, 409), f"-> {s}")
for i in inc:
    call("POST", "/committee/vote/", {"scenario_id": sid, "project_id": i["id"], "decision": "FOR"}, tokens["committee2"])
    call("POST", "/committee/vote/", {"scenario_id": sid, "project_id": i["id"], "decision": "AGAINST"}, tokens["committee3"])
s, d = call("POST", "/committee/finalize/", {"scenario_id": sid}, tokens["manager"]); check("TC-FUNC-07", "finalize with quorum (2 FOR, 1 AGAINST)", s == 200, f"-> {s}")
s, raw = call("GET", "/export/portfolio.csv", None, tokens["manager"], raw=True)
text = raw.decode("utf-8-sig") if s == 200 else ""; lines = text.strip().splitlines()
check("TC-FUNC-08", "CSV: BOM + ';' + 10 columns + only approved", s == 200 and raw[:3] == b"\xef\xbb\xbf" and len(lines[0].split(";")) == 10 and len(lines) - 1 == len(inc), f"rows={max(len(lines) - 1, 0)}")
s, logs = call("GET", "/admin/logs/", None, tokens["admin"]); rows_ = logs if isinstance(logs, list) else (logs or {}).get("results", [])
check("TC-FUNC-09", "audit log readable by admin and non-empty", s == 200 and len(rows_) > 0, f"events={len(rows_)}")
if args.mailpit:
    try:
        with urllib.request.urlopen(args.mailpit.rstrip("/") + "/api/v1/messages", timeout=10) as r:
            total = json.loads(r.read()).get("total", 0)
        check("TC-FUNC-02", "e-mails delivered to Mailpit", total > 0, f"messages={total}")
    except Exception as exc:
        check("TC-FUNC-02", "Mailpit API reachable (tunnel to :8025?)", False, type(exc).__name__)
if args.lockout:
    for _ in range(5): call("POST", "/auth/login/", {"username": "expert2", "password": "bad"})
    s, _ = call("POST", "/auth/login/", {"username": "expert2", "password": USERS["expert2"]}); check("TC-SEC-02", "5 failures lock the account for 15 min (423)", s == 423, f"-> {s}")

print("\n| Check (TC) | Result | Detail |\n|---|---|---|")
for tc, name, ok, detail in rows: print(f"| {tc}: {name} | {'PASS' if ok else 'FAIL'} | {detail} |")
failed = [r for r in rows if not r[2]]; print(f"\n{len(rows) - len(failed)}/{len(rows)} checks passed"); sys.exit(1 if failed else 0)
