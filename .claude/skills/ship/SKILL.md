---
name: ship
description: Verify and deploy a change to the live Tracker. Use after editing app code, templates, or CSS — runs compile/parse + tag-balance checks, restarts the tracker service, scans logs for errors, then commits + pushes with the repo conventions.
allowed-tools: Bash
---
# Ship a Tracker change

The standard safe change→deploy loop. App lives at `/var/www/tracker`, runs under
systemd **`tracker.service`** (5 gunicorn workers on 127.0.0.1:8000). Related units:
`rmm-gateway.service`, `cirquermm-agent.service`.

## 0. Preflight — working tree (do this FIRST)
The canary agent build, the RMM gateway, and SOC2 evidence are served from the **on-disk working tree**, so the served files = whatever branch is checked out. Before anything:
```
git branch --show-current ; git status --short
```
- **If not on `main`**: STOP. A feature branch is checked out → production is serving that branch's files, and `git push origin main` would push the unchanged `main`. Either `git checkout main` (then merge the branch deliberately) or confirm with the operator first. Never ship from a stray feature branch.
- **Uncommitted changes**: confirm they're the intended edits, not a half-applied/stale state. Especially watch `rmm_agent/canary/*`, `rmm_gateway/*`, and `evidence_file_service.py` — served-vs-committed drift there is silent and prod-facing.
- After merging a branch to `main`, re-run `git log --oneline -1` to confirm `main` actually advanced before pushing (the "Already up to date" / "Everything up-to-date" surprise = you were on the wrong ref).
- If the change touches `rmm_gateway/*`, also restart **`rmm-gateway.service`**; if it touches agent files under `rmm_agent/canary/*`, no restart — agents pull on their cycle (see the **canary-deploy** skill).
- **DB migration ordering:** if the branch adds a `migrate_*.py`, the migration file itself only exists on the branch until merged — so **merge first, THEN run the migration, THEN restart the dependent service.** A service that references a new column will error if restarted before the migration runs (bit us once: gateway restarted before `rmm_remediation_queue.ticket_id` existed). For an additive nullable column the safe order is: merge → `venv/bin/python migrate_*.py` → restart gateway/tracker.

## 1. Verify BEFORE deploy
- **Python**: `venv/bin/python -m py_compile <files>`
- **Jinja template**: `venv/bin/python -c "from jinja2 import Environment, FileSystemLoader; e=Environment(loader=FileSystemLoader('templates')); e.parse(open('templates/<f>').read()); print('OK')"`
  For large structural edits, also confirm tag balance: counts of `{% if %}`/`{% endif %}`, `{% for %}`/`{% endfor %}`, and `<div>`/`</div>`.
- **CSS**: confirm `{` count == `}` count.

## 2. Restart + health
Restart needs sudo (operator supplies the password; non-interactive form: `echo <pw> | sudo -S -p '' ...`). Then:
```
sudo systemctl restart tracker ; sleep 4
echo "active: $(sudo systemctl is-active tracker) | GET / -> $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/)"
sudo journalctl -u tracker --since "12 seconds ago" --no-pager | grep -iE "error|traceback" | grep -vE "INFO|SIGTERM" | head
```
Healthy = `active`, a 2xx/3xx code, no traceback/error lines. `SIGTERM` lines are just old workers exiting — ignore them.

## 3. Commit + push
- Work happens on **`main`** (operator deploys from it). Branch first only if explicitly asked.
- Message: imperative subject + a short why. End every commit with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- `git push origin main`. PAT is in `~/.git-credentials` (fine-grained: Contents+Workflows+Actions RW, Secrets read). Sanitize any token out of echoed URLs: `sed -E 's#https://[^@ ]*@#https://#g'`.

## 4. Verify CI is green (REQUIRED — local-green ≠ CI-green)
A push is NOT done until the CI run for it passes. Local `py_compile`/`pytest` passing does **not** mean CI passes: CI (`ci.yml`) runs `pytest -ra` on a clean ubuntu / Py3.12 after `pip install -r requirements-dev.txt`, so it catches what your box hides — **hardcoded absolute paths** (e.g. a module doing `os.makedirs("/var/www/tracker/...")` at import → `PermissionError` on the runner where that path isn't writable), missing deps, and import-time side effects. (CI was red a full day this way while local pytest was green — the path was writable here, not on the runner.) After pushing, **watch the run to completion**:
```
TOK=$(sed -nE 's#https://[^:]*:?([A-Za-z0-9_]+)@github.com.*#\1#p' ~/.git-credentials | head -1)
H=$(git rev-parse HEAD)
for i in $(seq 1 30); do
  S=$(curl -s -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/cwren42/tracker/actions/workflows/ci.yml/runs?head_sha=$H&per_page=1" \
      | venv/bin/python -c "import sys,json;r=json.load(sys.stdin).get('workflow_runs',[]);print((r[0]['status']+' '+str(r[0]['conclusion'])) if r else 'no-run-yet')")
  echo "$S"; echo "$S" | grep -q completed && break; sleep 20
done
```
Must end **`completed success`**. If `completed failure`, pull the failing step's log and fix before moving on:
`runs/<run_id>/jobs` → the failed job id → `curl -sL -H "Authorization: Bearer $TOK" ".../actions/jobs/<job_id>/logs" | grep -iE "error|PermissionError|ModuleNotFound|^E |assert"`.

## Gotchas that bite here
- **Hardcoded absolute paths at import time** (`/var/www/tracker/...` in a module-level `os.makedirs`/`open`) pass locally but break CI and any other checkout. Derive from `os.path.dirname(os.path.abspath(__file__))` or an env var, and wrap import-time `os.makedirs` in try/except.
- **`.secrets.env`** (gitignored) is loaded by systemd `EnvironmentFile` — env config only takes effect after a restart, and all 5 workers read it identically. Runtime config written to the DB at request time does NOT persist across restarts or propagate to other workers.
- **Cache-busting**: static CSS/JS must use `?v={{ asset_version }}` (git short SHA). A hardcoded `?v=N` or a missing version string caches forever in the browser — fix it.
- App is **created at import** (threads start at import) — not a true factory. Importing `app` has side effects.
