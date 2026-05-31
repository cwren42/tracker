---
name: ship
description: Verify and deploy a change to the live Tracker. Use after editing app code, templates, or CSS — runs compile/parse + tag-balance checks, restarts the tracker service, scans logs for errors, then commits + pushes with the repo conventions.
allowed-tools: Bash
---
# Ship a Tracker change

The standard safe change→deploy loop. App lives at `/var/www/tracker`, runs under
systemd **`tracker.service`** (5 gunicorn workers on 127.0.0.1:8000). Related units:
`rmm-gateway.service`, `cirquermm-agent.service`.

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

## Gotchas that bite here
- **`.secrets.env`** (gitignored) is loaded by systemd `EnvironmentFile` — env config only takes effect after a restart, and all 5 workers read it identically. Runtime config written to the DB at request time does NOT persist across restarts or propagate to other workers.
- **Cache-busting**: static CSS/JS must use `?v={{ asset_version }}` (git short SHA). A hardcoded `?v=N` or a missing version string caches forever in the browser — fix it.
- App is **created at import** (threads start at import) — not a true factory. Importing `app` has side effects.
