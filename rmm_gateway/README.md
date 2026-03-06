# Tracker RMM Gateway (MVP)

Purpose: provide WebSocket connectivity for interactive RMM features without forcing Flask/gunicorn to act as a WS server.

This gateway:
- Accepts agent WebSocket connections (`/ws/agent/{agent_id}`)
- Accepts technician WebSocket connections (`/ws/tech/{agent_id}`)
- Creates an audited `rmm_session` for each tech connection
- Logs all messages to `rmm_event` (commands + output)

## Install (recommended: separate venv)

```bash
cd /var/www/tracker
python3 -m venv rmm_gateway/.venv
rmm_gateway/.venv/bin/pip install -r rmm_gateway/requirements.txt
```

## Run

```bash
cd /var/www/tracker
rmm_gateway/.venv/bin/uvicorn rmm_gateway.main:app --host 0.0.0.0 --port 8765
```

## Production (TLS + systemd)

Recommended approach:
- Keep uvicorn bound to `127.0.0.1:8765`
- Terminate TLS + proxy WebSockets in Nginx
- Run gateway as a `systemd` service

Templates to copy:
- `deploy/rmm-gateway.service`
- `deploy/nginx-rmm-gateway.conf`

Notes:
- The current MVP passes agent token + tech API key via query string during the WS handshake.
	Avoid logging request URIs with args. The systemd service template uses `--no-access-log`,
	and the nginx template disables access logging for the vhost.

### Windows certificate (PFX) conversion

If your cert is a `.pfx`/`.p12`, Nginx needs a PEM key + cert chain.

Example (creates a key + fullchain in `/etc/nginx/certs`):

```bash
sudo mkdir -p /etc/nginx/certs
sudo chmod 700 /etc/nginx/certs

# Key (you will be prompted for the PFX password)
sudo openssl pkcs12 -in rmm.cirque.com.pfx -nocerts -nodes -out /etc/nginx/certs/rmm.cirque.com.key

# Cert chain
sudo openssl pkcs12 -in rmm.cirque.com.pfx -clcerts -nokeys -out /etc/nginx/certs/rmm.cirque.com.fullchain.crt

sudo chmod 600 /etc/nginx/certs/rmm.cirque.com.key /etc/nginx/certs/rmm.cirque.com.fullchain.crt
```

## DB setup

Run once (safe to rerun):

```bash
/var/www/tracker/venv/bin/python /var/www/tracker/migrate_add_rmm_gateway.py
```

## Enroll an agent

```bash
/var/www/tracker/venv/bin/python /var/www/tracker/scripts/enroll_rmm_agent.py --agent-id PC-01 --asset-id 123
```

This prints a one-time token. Store it on the endpoint.

## Create a technician API key

The tech websocket auth uses the shared API key system (`api_keys` table). You need a key with permission `rmm_connect`.

```bash
/var/www/tracker/venv/bin/python /var/www/tracker/scripts/create_api_key.py --user-id 4 --name "RMM Tech" --permissions rmm_connect
```

## Protocol (current MVP)

Tech -> agent:
- `{ "type": "exec", "command": "whoami" }`

Agent -> tech:
- `{ "type": "exec_result", "session_id": 12, "stdout": "...", "stderr": "...", "exit_code": 0 }`

The agent currently only allows a small set of commands (see `rmm_agent/agent_client.py`).
