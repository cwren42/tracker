"""
Cirque RMM — Workflow Engine
Executes visual workflow definitions stored in workflow_definitions table.
Runs as a background thread inside the Flask app.
"""
import json, logging, threading, time, traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

from pg_db import pg_connect

log = logging.getLogger("workflow_engine")


# ──────────────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────────────
def _db():
    return pg_connect()


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# ──────────────────────────────────────────────────────────────────────────────
# Action handlers — each returns (success: bool, output: dict)
# ──────────────────────────────────────────────────────────────────────────────

def _action_create_ticket(config: dict, ctx: dict) -> tuple:
    """Create a support ticket."""
    title   = _render(config.get("title", "Automated Ticket"), ctx)
    body    = _render(config.get("body", "Created by workflow."), ctx)
    priority = config.get("priority", "medium")
    category = config.get("category", "general")
    try:
        db = _db()
        now = _now()
        cur = db.execute(
            """INSERT INTO support_ticket (title, description, priority, category, status, created_at, updated_at)
               VALUES (?,?,?,?,'open',?,?)""",
            (title, body, priority, category, now, now)
        )
        ticket_id = cur.lastrowid
        db.commit(); db.close()
        return True, {"ticket_id": ticket_id, "title": title}
    except Exception as e:
        return False, {"error": str(e)}


def _action_send_notification(config: dict, ctx: dict) -> tuple:
    """Add an in-app bell notification."""
    message = _render(config.get("message", "Workflow notification"), ctx)
    try:
        db = _db()
        users = db.execute("SELECT id FROM user WHERE role IN ('admin','manager')").fetchall()
        for u in users:
            db.execute(
                "INSERT INTO notification_bell (user_id, message, type, created_at) VALUES (?,?,'workflow',?)",
                (u["id"], message, _now())
            )
        db.commit(); db.close()
        return True, {"notified": len(users), "message": message}
    except Exception as e:
        return False, {"error": str(e)}


def _action_send_email(config: dict, ctx: dict) -> tuple:
    """Send an email via configured SMTP."""
    to      = _render(config.get("to", ""), ctx)
    subject = _render(config.get("subject", "Workflow Alert"), ctx)
    body    = _render(config.get("body", ""), ctx)
    if not to:
        return False, {"error": "No 'to' address configured"}
    try:
        import smtplib
        from email.mime.text import MIMEText
        db = _db()
        cfg = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM setting").fetchall()}
        db.close()
        msg = MIMEText(body, "html")
        msg["Subject"] = subject
        msg["From"]    = cfg.get("mail_sender", "noreply@cirque.com")
        msg["To"]      = to
        host = cfg.get("mail_server", "localhost")
        port = int(cfg.get("mail_port", 25))
        with smtplib.SMTP(host, port, timeout=10) as s:
            if cfg.get("mail_use_tls") == "1":
                s.starttls()
            if cfg.get("mail_username"):
                s.login(cfg["mail_username"], cfg.get("mail_password", ""))
            s.send_message(msg)
        return True, {"to": to, "subject": subject}
    except Exception as e:
        return False, {"error": str(e)}


def _action_disable_ad_user(config: dict, ctx: dict) -> tuple:
    """Disable a user in Active Directory via LDAP."""
    username = _render(config.get("username", ctx.get("username", "")), ctx)
    if not username:
        return False, {"error": "No username provided"}
    try:
        import ldap3, ssl
        db = _db()
        ad = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM setting WHERE key LIKE 'ad_%'").fetchall()}
        db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        server = ldap3.Server(ad["ad_server"], port=int(ad.get("ad_port", 636)),
                              use_ssl=ad.get("ad_use_ssl") == "1",
                              get_info=ldap3.ALL)
        from secret_store import decrypt_secret
        conn = ldap3.Connection(server, ad["ad_bind_username"], decrypt_secret(ad["ad_bind_password"]), auto_bind=True)
        conn.search(ad["ad_base_dn"], f"(sAMAccountName={username})",
                    attributes=["distinguishedName", "userAccountControl"])
        if not conn.entries:
            return False, {"error": f"User {username} not found in AD"}
        dn  = conn.entries[0].distinguishedName.value
        uac = int(conn.entries[0].userAccountControl.value)
        # Set bit 2 (0x0002) to disable
        conn.modify(dn, {"userAccountControl": [(ldap3.MODIFY_REPLACE, [uac | 2])]})
        conn.unbind()
        return True, {"username": username, "dn": dn, "disabled": True}
    except Exception as e:
        return False, {"error": str(e)}


def _action_azure_sync(config: dict, ctx: dict) -> tuple:
    """Trigger Azure AD Connect delta sync via AD DS."""
    try:
        db = _db()
        ad = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM setting WHERE key LIKE 'ad_%'").fetchall()}
        db.close()
        # Attempt via RMM agent on DC if agent is registered
        dc_host = ad.get("ad_server", "")
        db2 = _db()
        agent = db2.execute(
            "SELECT agent_id FROM rmm_agent WHERE hostname LIKE ? AND online=1 LIMIT 1",
            (dc_host.split(".")[0] + "%",)
        ).fetchone()
        db2.close()
        if agent:
            # Queue a script run via RMM
            db3 = _db()
            db3.execute(
                "INSERT INTO rmm_event (agent_id, event_type, payload, created_at) VALUES (?,?,?,?)",
                (agent["agent_id"], "run_powershell",
                 json.dumps({"script": "Import-Module ADSync; Start-ADSyncSyncCycle -PolicyType Delta"}),
                 _now())
            )
            db3.commit(); db3.close()
            return True, {"method": "rmm_agent", "agent_id": agent["agent_id"]}
        return False, {"error": "No online RMM agent found on domain controller"}
    except Exception as e:
        return False, {"error": str(e)}


def _action_webhook(config: dict, ctx: dict) -> tuple:
    """POST to an external webhook URL."""
    url     = _render(config.get("url", ""), ctx)
    payload = json.loads(_render(config.get("payload", "{}"), ctx))
    if not url:
        return False, {"error": "No URL configured"}
    try:
        import urllib.request, urllib.error
        data = json.dumps({**payload, "_workflow_ctx": ctx}).encode()
        req  = urllib.request.Request(url, data=data,
                                       headers={"Content-Type": "application/json"},
                                       method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return True, {"status": r.status, "url": url}
    except Exception as e:
        return False, {"error": str(e)}


def _action_deploy_patch(config: dict, ctx: dict) -> tuple:
    """Trigger CVE patch deployment for a device."""
    cve_id   = _render(config.get("cve_id", ctx.get("cve_id", "")), ctx)
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    if not cve_id:
        return False, {"error": "No CVE ID"}
    try:
        import urllib.request
        url  = f"http://127.0.0.1:8000/api/vulnerabilities/{cve_id}/deploy"
        body = json.dumps({"asset_id": asset_id} if asset_id else {}).encode()
        req  = urllib.request.Request(url, data=body,
                                       headers={"Content-Type": "application/json", "X-Internal": "workflow"},
                                       method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read())
        return True, result
    except Exception as e:
        return False, {"error": str(e)}


def _action_wait(config: dict, ctx: dict) -> tuple:
    """Sleep for N seconds (up to 1 hour)."""
    seconds = min(int(config.get("seconds", 60)), 3600)
    time.sleep(seconds)
    return True, {"waited_seconds": seconds}


def _action_update_ticket(config: dict, ctx: dict) -> tuple:
    """Update a ticket's status/assigned_to."""
    ticket_id = config.get("ticket_id") or ctx.get("ticket_id")
    if not ticket_id:
        return False, {"error": "No ticket_id"}
    updates, vals = [], []
    for f in ["status", "priority", "assigned_to"]:
        if f in config:
            updates.append(f"{f}=?")
            vals.append(config[f])
    if not updates:
        return False, {"error": "No fields to update"}
    vals += [_now(), ticket_id]
    try:
        db = _db()
        db.execute(f"UPDATE support_ticket SET {','.join(updates)}, updated_at=? WHERE id=?", vals)
        db.commit(); db.close()
        return True, {"ticket_id": ticket_id, "updated": config}
    except Exception as e:
        return False, {"error": str(e)}


def _action_ai_suggest(config: dict, ctx: dict) -> tuple:
    """Generate an AI suggestion (ticket assistant) — needs API key pre-configured."""
    try:
        db = _db()
        api_key = db.execute("SELECT value FROM setting WHERE key='openai_api_key'").fetchone()
        db.close()
        if not api_key or not api_key["value"]:
            return False, {"error": "OpenAI API key not configured — add it in Settings → AI"}
        ticket_id = ctx.get("ticket_id")
        if not ticket_id:
            return False, {"error": "No ticket_id in context"}
        db2 = _db()
        ticket = db2.execute("SELECT * FROM support_ticket WHERE id=?", (ticket_id,)).fetchone()
        db2.close()
        if not ticket:
            return False, {"error": "Ticket not found"}
        import urllib.request
        prompt = (
            f"You are an IT support assistant. A ticket has been created:\n\n"
            f"Title: {ticket['title']}\nDescription: {ticket['description']}\n"
            f"Priority: {ticket['priority']}\nCategory: {ticket.get('category','')}\n\n"
            f"Provide: 1) A brief diagnosis. 2) Step-by-step resolution. 3) Whether this can be auto-resolved.\n"
            f"Be concise and technical."
        )
        payload = json.dumps({
            "model": config.get("model", "gpt-4o"),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {__import__('secret_store').decrypt_secret(api_key['value'])}"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
        suggestion = resp["choices"][0]["message"]["content"]
        db3 = _db()
        db3.execute(
            "INSERT INTO ai_ticket_suggestions (ticket_id, model, suggestion, status) VALUES (?,?,?,'pending')",
            (ticket_id, config.get("model", "gpt-4o"), suggestion)
        )
        db3.commit(); db3.close()
        return True, {"ticket_id": ticket_id, "suggestion_created": True}
    except Exception as e:
        return False, {"error": str(e)}



# ── HELPERS ────────────────────────────────────────────────────────────────────
def _get_ad_settings(db):
    rows = db.execute("SELECT key, value FROM setting WHERE key LIKE 'ad_%'").fetchall()
    return {r["key"]: r["value"] for r in rows}


def _ad_connect(ad):
    import ldap3
    server = ldap3.Server(
        ad["ad_server"],
        port=int(ad.get("ad_port", 636)),
        use_ssl=ad.get("ad_use_ssl") == "1",
        get_info=ldap3.ALL,
    )
    from secret_store import decrypt_secret
    return ldap3.Connection(server, ad["ad_bind_username"], decrypt_secret(ad["ad_bind_password"]), auto_bind=True)


def _queue_rmm(agent_id: str, cmd_type: str, payload: dict) -> bool:
    """Queue a command to an online RMM agent via rmm_event table."""
    try:
        db = _db()
        db.execute(
            "INSERT INTO rmm_event (agent_id, event_type, payload, created_at) VALUES (?,?,?,?)",
            (agent_id, cmd_type, json.dumps(payload), _now()),
        )
        db.commit(); db.close()
        return True
    except Exception:
        return False


def _find_agent(asset_id=None, hostname=None):
    """Return online agent_id for an asset or hostname, or None."""
    db = _db()
    agent = None
    if asset_id:
        agent = db.execute(
            "SELECT agent_id FROM rmm_agent WHERE asset_id=? AND online=1 LIMIT 1",
            (asset_id,),
        ).fetchone()
    if not agent and hostname:
        agent = db.execute(
            "SELECT agent_id FROM rmm_agent WHERE hostname LIKE ? AND online=1 LIMIT 1",
            (hostname + "%",),
        ).fetchone()
    db.close()
    return agent["agent_id"] if agent else None


# ── NEW ACTION HANDLERS ────────────────────────────────────────────────────────

def _action_create_user(config: dict, ctx: dict) -> tuple:
    """Create a new user in Active Directory."""
    username   = _render(config.get("username", ""), ctx)
    first_name = _render(config.get("first_name", ""), ctx)
    last_name  = _render(config.get("last_name", ""), ctx)
    password   = _render(config.get("password", ""), ctx)
    ou         = _render(config.get("ou", ""), ctx)
    if not username:
        return False, {"error": "username is required"}
    try:
        import ldap3
        db = _db()
        ad = _get_ad_settings(db); db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        conn = _ad_connect(ad)
        base_ou = ou or ad.get("ad_base_dn", "")
        dn = f"CN={first_name} {last_name},{base_ou}".strip(",")
        attrs = {
            "objectClass": ["top", "person", "organizationalPerson", "user"],
            "sAMAccountName": username,
            "userPrincipalName": f"{username}@{ad.get('ad_domain', '')}",
            "givenName": first_name,
            "sn": last_name,
            "displayName": f"{first_name} {last_name}".strip(),
        }
        conn.add(dn, attributes=attrs)
        if password:
            # Set password and enable account
            conn.modify(dn, {"unicodePwd": [(ldap3.MODIFY_REPLACE, [f'"{password}"'.encode("utf-16-le")])]})
            conn.modify(dn, {"userAccountControl": [(ldap3.MODIFY_REPLACE, [512])]})
        conn.unbind()
        return True, {"username": username, "dn": dn, "created": True}
    except Exception as e:
        return False, {"error": str(e)}


def _action_enable_ad_user(config: dict, ctx: dict) -> tuple:
    """Enable a disabled Active Directory user account."""
    username = _render(config.get("username", ctx.get("username", "")), ctx)
    if not username:
        return False, {"error": "No username provided"}
    try:
        import ldap3
        db = _db()
        ad = _get_ad_settings(db); db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        conn = _ad_connect(ad)
        conn.search(ad["ad_base_dn"], f"(sAMAccountName={username})",
                    attributes=["distinguishedName", "userAccountControl"])
        if not conn.entries:
            return False, {"error": f"User {username} not found"}
        dn  = conn.entries[0].distinguishedName.value
        uac = int(conn.entries[0].userAccountControl.value)
        conn.modify(dn, {"userAccountControl": [(ldap3.MODIFY_REPLACE, [uac & ~2])]})
        conn.unbind()
        return True, {"username": username, "dn": dn, "enabled": True}
    except Exception as e:
        return False, {"error": str(e)}


def _action_reset_password(config: dict, ctx: dict) -> tuple:
    """Reset an AD user's password."""
    username    = _render(config.get("username", ctx.get("username", "")), ctx)
    new_password = _render(config.get("new_password", ""), ctx)
    if not username or not new_password:
        return False, {"error": "username and new_password are required"}
    try:
        import ldap3
        db = _db()
        ad = _get_ad_settings(db); db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        conn = _ad_connect(ad)
        conn.search(ad["ad_base_dn"], f"(sAMAccountName={username})",
                    attributes=["distinguishedName"])
        if not conn.entries:
            return False, {"error": f"User {username} not found"}
        dn = conn.entries[0].distinguishedName.value
        pw_encoded = f'"{new_password}"'.encode("utf-16-le")
        conn.modify(dn, {"unicodePwd": [(ldap3.MODIFY_REPLACE, [pw_encoded])]})
        conn.unbind()
        return True, {"username": username, "password_reset": True}
    except Exception as e:
        return False, {"error": str(e)}


def _action_unlock_account(config: dict, ctx: dict) -> tuple:
    """Unlock a locked-out Active Directory account."""
    username = _render(config.get("username", ctx.get("username", "")), ctx)
    if not username:
        return False, {"error": "No username provided"}
    try:
        import ldap3
        db = _db()
        ad = _get_ad_settings(db); db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        conn = _ad_connect(ad)
        conn.search(ad["ad_base_dn"], f"(sAMAccountName={username})",
                    attributes=["distinguishedName"])
        if not conn.entries:
            return False, {"error": f"User {username} not found"}
        dn = conn.entries[0].distinguishedName.value
        conn.modify(dn, {"lockoutTime": [(ldap3.MODIFY_REPLACE, [0])]})
        conn.unbind()
        return True, {"username": username, "unlocked": True}
    except Exception as e:
        return False, {"error": str(e)}


def _action_add_to_group(config: dict, ctx: dict) -> tuple:
    """Add an AD user to a security/distribution group."""
    username   = _render(config.get("username", ctx.get("username", "")), ctx)
    group_name = _render(config.get("group_name", ""), ctx)
    if not username or not group_name:
        return False, {"error": "username and group_name are required"}
    try:
        import ldap3
        db = _db()
        ad = _get_ad_settings(db); db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        conn = _ad_connect(ad)
        # Find user DN
        conn.search(ad["ad_base_dn"], f"(sAMAccountName={username})", attributes=["distinguishedName"])
        if not conn.entries:
            return False, {"error": f"User {username} not found"}
        user_dn = conn.entries[0].distinguishedName.value
        # Find group DN
        conn.search(ad["ad_base_dn"], f"(cn={group_name})", attributes=["distinguishedName"])
        if not conn.entries:
            return False, {"error": f"Group {group_name} not found"}
        group_dn = conn.entries[0].distinguishedName.value
        conn.modify(group_dn, {"member": [(ldap3.MODIFY_ADD, [user_dn])]})
        conn.unbind()
        return True, {"username": username, "group": group_name, "added": True}
    except Exception as e:
        return False, {"error": str(e)}


def _action_remove_from_group(config: dict, ctx: dict) -> tuple:
    """Remove an AD user from a security/distribution group."""
    username   = _render(config.get("username", ctx.get("username", "")), ctx)
    group_name = _render(config.get("group_name", ""), ctx)
    if not username or not group_name:
        return False, {"error": "username and group_name are required"}
    try:
        import ldap3
        db = _db()
        ad = _get_ad_settings(db); db.close()
        if not ad.get("ad_enabled") == "1":
            return False, {"error": "AD integration not enabled"}
        conn = _ad_connect(ad)
        conn.search(ad["ad_base_dn"], f"(sAMAccountName={username})", attributes=["distinguishedName"])
        if not conn.entries:
            return False, {"error": f"User {username} not found"}
        user_dn = conn.entries[0].distinguishedName.value
        conn.search(ad["ad_base_dn"], f"(cn={group_name})", attributes=["distinguishedName"])
        if not conn.entries:
            return False, {"error": f"Group {group_name} not found"}
        group_dn = conn.entries[0].distinguishedName.value
        conn.modify(group_dn, {"member": [(ldap3.MODIFY_DELETE, [user_dn])]})
        conn.unbind()
        return True, {"username": username, "group": group_name, "removed": True}
    except Exception as e:
        return False, {"error": str(e)}


def _action_reboot_device(config: dict, ctx: dict) -> tuple:
    """Reboot a device via RMM agent."""
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    delay_s  = int(config.get("delay_seconds", 0))
    agent_id = _find_agent(asset_id=asset_id)
    if not agent_id:
        return False, {"error": "No online RMM agent found for device"}
    script = f"Start-Sleep -Seconds {delay_s}; Restart-Computer -Force" if delay_s else "Restart-Computer -Force"
    ok = _queue_rmm(agent_id, "run_powershell", {"script": script})
    return (True, {"agent_id": agent_id, "action": "reboot"}) if ok else (False, {"error": "Failed to queue command"})


def _action_shutdown_device(config: dict, ctx: dict) -> tuple:
    """Shut down a device via RMM agent."""
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    agent_id = _find_agent(asset_id=asset_id)
    if not agent_id:
        return False, {"error": "No online RMM agent found for device"}
    ok = _queue_rmm(agent_id, "run_powershell", {"script": "Stop-Computer -Force"})
    return (True, {"agent_id": agent_id, "action": "shutdown"}) if ok else (False, {"error": "Failed to queue command"})


def _action_lock_device(config: dict, ctx: dict) -> tuple:
    """Lock workstation or enable BitLocker on a device via RMM agent."""
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    mode     = config.get("mode", "lock")   # lock | bitlocker
    agent_id = _find_agent(asset_id=asset_id)
    if not agent_id:
        return False, {"error": "No online RMM agent found for device"}
    if mode == "bitlocker":
        script = "Enable-BitLocker -MountPoint 'C:' -EncryptionMethod XtsAes256 -UsedSpaceOnlyEncryption -TpmProtector"
    else:
        script = "rundll32.exe user32.dll,LockWorkStation"
    ok = _queue_rmm(agent_id, "run_powershell", {"script": script})
    return (True, {"agent_id": agent_id, "action": mode}) if ok else (False, {"error": "Failed to queue command"})


def _action_deploy_software(config: dict, ctx: dict) -> tuple:
    """Deploy software to a device via RMM agent (Chocolatey, MSI, or EXE)."""
    asset_id  = config.get("asset_id") or ctx.get("asset_id")
    method    = config.get("method", "chocolatey")   # chocolatey | msi | exe | winget
    package   = _render(config.get("package", ""), ctx)
    args      = _render(config.get("args", ""), ctx)
    if not package:
        return False, {"error": "Package name/path is required"}
    agent_id = _find_agent(asset_id=asset_id)
    if not agent_id:
        return False, {"error": "No online RMM agent found for device"}
    if method == "chocolatey":
        script = f"choco install {package} -y --no-progress {args}".strip()
    elif method == "winget":
        script = f"winget install --id {package} --silent --accept-source-agreements --accept-package-agreements {args}".strip()
    elif method == "msi":
        script = f"msiexec /i \"{package}\" /qn /norestart {args}".strip()
    else:  # exe
        script = f"Start-Process -FilePath \"{package}\" -ArgumentList \"{args}\" -Wait -NoNewWindow"
    ok = _queue_rmm(agent_id, "run_powershell", {"script": script})
    return (True, {"agent_id": agent_id, "package": package, "method": method}) if ok else (False, {"error": "Failed to queue command"})


def _action_uninstall_software(config: dict, ctx: dict) -> tuple:
    """Uninstall software from a device via RMM agent."""
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    method   = config.get("method", "chocolatey")
    package  = _render(config.get("package", ""), ctx)
    if not package:
        return False, {"error": "Package name is required"}
    agent_id = _find_agent(asset_id=asset_id)
    if not agent_id:
        return False, {"error": "No online RMM agent found for device"}
    if method == "chocolatey":
        script = f"choco uninstall {package} -y --no-progress"
    elif method == "winget":
        script = f"winget uninstall --id {package} --silent"
    else:
        script = f"Get-WmiObject -Class Win32_Product | Where-Object {{ $_.Name -like '*{package}*' }} | ForEach-Object {{ $_.Uninstall() }}"
    ok = _queue_rmm(agent_id, "run_powershell", {"script": script})
    return (True, {"agent_id": agent_id, "package": package, "uninstalled": True}) if ok else (False, {"error": "Failed to queue command"})


def _action_run_script(config: dict, ctx: dict) -> tuple:
    """Run a PowerShell or Bash script on a device via RMM agent."""
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    script   = _render(config.get("script", ""), ctx)
    lang     = config.get("language", "powershell")
    if not script:
        return False, {"error": "Script content is required"}
    agent_id = _find_agent(asset_id=asset_id)
    if not agent_id:
        return False, {"error": "No online RMM agent found for device"}
    cmd_type = "run_powershell" if lang == "powershell" else "run_bash"
    ok = _queue_rmm(agent_id, cmd_type, {"script": script})
    return (True, {"agent_id": agent_id, "language": lang, "dispatched": True}) if ok else (False, {"error": "Failed to queue command"})


def _action_apply_gpo(config: dict, ctx: dict) -> tuple:
    """Force a Group Policy update on a device or DC via RMM agent."""
    asset_id = config.get("asset_id") or ctx.get("asset_id")
    target   = config.get("target", "device")   # device | dc
    force    = config.get("force", True)
    agent_id = _find_agent(asset_id=asset_id)
    if not agent_id:
        return False, {"error": "No online RMM agent found for device"}
    flag = "/force" if force else ""
    script = f"gpupdate {flag}" if target == "device" else f"Invoke-GPUpdate -Computer $env:COMPUTERNAME {'/Force' if force else ''}"
    ok = _queue_rmm(agent_id, "run_powershell", {"script": script})
    return (True, {"agent_id": agent_id, "action": "gpupdate", "target": target}) if ok else (False, {"error": "Failed to queue command"})


def _action_send_teams(config: dict, ctx: dict) -> tuple:
    """Send a message to a Microsoft Teams channel via webhook."""
    webhook_url = _render(config.get("webhook_url", ""), ctx)
    title       = _render(config.get("title", "Workflow Alert"), ctx)
    message     = _render(config.get("message", ""), ctx)
    color       = config.get("color", "0078D4")
    if not webhook_url:
        return False, {"error": "Teams webhook URL is required"}
    try:
        import urllib.request
        payload = json.dumps({
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": color,
            "summary": title,
            "sections": [{"activityTitle": title, "activityText": message}],
        }).encode()
        req = urllib.request.Request(webhook_url, data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return True, {"status": r.status, "title": title}
    except Exception as e:
        return False, {"error": str(e)}


def _action_send_slack(config: dict, ctx: dict) -> tuple:
    """Send a message to a Slack channel via webhook."""
    webhook_url = _render(config.get("webhook_url", ""), ctx)
    message     = _render(config.get("message", ""), ctx)
    channel     = config.get("channel", "")
    if not webhook_url:
        return False, {"error": "Slack webhook URL is required"}
    try:
        import urllib.request
        payload = {"text": message}
        if channel:
            payload["channel"] = channel
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(webhook_url, data=data,
                                      headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return True, {"status": r.status, "message": message}
    except Exception as e:
        return False, {"error": str(e)}


def _action_close_ticket(config: dict, ctx: dict) -> tuple:
    """Close a support ticket."""
    ticket_id = config.get("ticket_id") or ctx.get("ticket_id")
    resolution = _render(config.get("resolution", "Resolved by workflow"), ctx)
    if not ticket_id:
        return False, {"error": "No ticket_id"}
    try:
        db = _db()
        db.execute(
            "UPDATE support_ticket SET status='closed', updated_at=? WHERE id=?",
            (_now(), ticket_id),
        )
        db.commit(); db.close()
        return True, {"ticket_id": ticket_id, "closed": True, "resolution": resolution}
    except Exception as e:
        return False, {"error": str(e)}


def _action_assign_ticket(config: dict, ctx: dict) -> tuple:
    """Assign a ticket to a user."""
    ticket_id  = config.get("ticket_id") or ctx.get("ticket_id")
    assigned_to = _render(config.get("assigned_to", ""), ctx)
    if not ticket_id:
        return False, {"error": "No ticket_id"}
    if not assigned_to:
        return False, {"error": "assigned_to is required"}
    try:
        db = _db()
        db.execute(
            "UPDATE support_ticket SET assigned_to=?, status='in_progress', updated_at=? WHERE id=?",
            (assigned_to, _now(), ticket_id),
        )
        db.commit(); db.close()
        return True, {"ticket_id": ticket_id, "assigned_to": assigned_to}
    except Exception as e:
        return False, {"error": str(e)}


def _action_http_request(config: dict, ctx: dict) -> tuple:
    """Generic HTTP request to any URL."""
    url     = _render(config.get("url", ""), ctx)
    method  = config.get("method", "GET").upper()
    body    = _render(config.get("body", ""), ctx)
    headers_raw = config.get("headers", {})
    headers = {k: _render(v, ctx) for k, v in (headers_raw.items() if isinstance(headers_raw, dict) else {})}
    headers.setdefault("Content-Type", "application/json")
    if not url:
        return False, {"error": "URL is required"}
    try:
        import urllib.request
        data = body.encode() if body and method not in ("GET", "HEAD") else None
        req  = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=20) as r:
            resp_body = r.read().decode(errors="replace")[:2000]
            return True, {"status": r.status, "url": url, "response": resp_body}
    except Exception as e:
        return False, {"error": str(e)}


# ──────────────────────────────────────────────────────────────────────────────
# Condition evaluator
# ──────────────────────────────────────────────────────────────────────────────
def _evaluate_condition(config: dict, ctx: dict) -> bool:
    field    = config.get("field", "")
    operator = config.get("operator", "==")
    value    = str(config.get("value", ""))
    actual   = str(ctx.get(field, ""))
    ops = {
        "==": actual == value,
        "!=": actual != value,
        ">":  float(actual or 0) > float(value or 0),
        "<":  float(actual or 0) < float(value or 0),
        "contains": value.lower() in actual.lower(),
        "not_contains": value.lower() not in actual.lower(),
    }
    return ops.get(operator, False)


# ──────────────────────────────────────────────────────────────────────────────
# Template renderer (simple {{key}} substitution)
# ──────────────────────────────────────────────────────────────────────────────
def _render(template: str, ctx: dict) -> str:
    import re
    def replacer(m):
        key = m.group(1).strip()
        return str(ctx.get(key, m.group(0)))
    return re.sub(r"\{\{(.+?)\}\}", replacer, str(template))


# ──────────────────────────────────────────────────────────────────────────────
# Action dispatcher
# ──────────────────────────────────────────────────────────────────────────────
ACTION_MAP = {
    "create_ticket":      _action_create_ticket,
    "update_ticket":      _action_update_ticket,
    "close_ticket":       _action_close_ticket,
    "assign_ticket":      _action_assign_ticket,
    "send_notification":  _action_send_notification,
    "send_email":         _action_send_email,
    "send_teams":         _action_send_teams,
    "send_slack":         _action_send_slack,
    "disable_ad_user":    _action_disable_ad_user,
    "enable_ad_user":     _action_enable_ad_user,
    "create_user":        _action_create_user,
    "reset_password":     _action_reset_password,
    "unlock_account":     _action_unlock_account,
    "add_to_group":       _action_add_to_group,
    "remove_from_group":  _action_remove_from_group,
    "azure_sync":         _action_azure_sync,
    "webhook":            _action_webhook,
    "http_request":       _action_http_request,
    "deploy_patch":       _action_deploy_patch,
    "deploy_software":    _action_deploy_software,
    "uninstall_software": _action_uninstall_software,
    "run_script":         _action_run_script,
    "reboot_device":      _action_reboot_device,
    "shutdown_device":    _action_shutdown_device,
    "lock_device":        _action_lock_device,
    "apply_gpo":          _action_apply_gpo,
    "wait":               _action_wait,
    "ai_suggest":         _action_ai_suggest,
}


# ──────────────────────────────────────────────────────────────────────────────
# Core executor
# ──────────────────────────────────────────────────────────────────────────────
def execute_workflow(workflow_id: int, trigger_data: dict = None) -> int:
    """
    Start executing a workflow. Returns run_id.
    Executed in a background thread.
    """
    trigger_data = trigger_data or {}
    db = _db()
    wf = db.execute("SELECT * FROM workflow_definitions WHERE id=? AND enabled=1", (workflow_id,)).fetchone()
    if not wf:
        db.close()
        raise ValueError(f"Workflow {workflow_id} not found or disabled")
    nodes = json.loads(wf["nodes"])
    edges = json.loads(wf["edges"])
    cur = db.execute(
        "INSERT INTO workflow_runs (workflow_id, trigger_data, status, started_at) VALUES (?,?,?,?)",
        (workflow_id, json.dumps(trigger_data), "running", _now())
    )
    run_id = cur.lastrowid
    db.commit(); db.close()

    # Run in background
    t = threading.Thread(target=_run_workflow, args=(run_id, nodes, edges, trigger_data), daemon=True)
    t.start()
    return run_id


def _run_workflow(run_id: int, nodes: list, edges: list, ctx: dict):
    try:
        # Build adjacency: node_id -> list of target node_ids
        adj = {}
        edge_conditions = {}  # (src, tgt) -> condition label (true/false)
        for e in edges:
            src, tgt = e.get("source"), e.get("target")
            if src and tgt:
                adj.setdefault(src, []).append(tgt)
                if e.get("label"):
                    edge_conditions[(src, tgt)] = e["label"].lower()

        # Find trigger node (type == "trigger")
        node_map = {n["id"]: n for n in nodes}
        start_nodes = [n for n in nodes if n.get("type") == "trigger"]
        if not start_nodes:
            start_nodes = nodes[:1]

        visited = set()
        queue   = [n["id"] for n in start_nodes]

        while queue:
            node_id = queue.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)
            node = node_map.get(node_id)
            if not node:
                continue

            ntype  = node.get("type", "action")
            config = node.get("config", {})
            label  = node.get("label", ntype)

            db = _db()
            cur = db.execute(
                "INSERT INTO workflow_run_steps (run_id, node_id, node_type, node_label, status, started_at) VALUES (?,?,?,?,?,?)",
                (run_id, node_id, ntype, label, "running", _now())
            )
            db.commit()
            step_id = cur.lastrowid
            db.close()

            success, output = True, {}

            if ntype == "trigger":
                # Trigger node just passes through with context
                success, output = True, ctx

            elif ntype == "condition":
                result = _evaluate_condition(config, ctx)
                output = {"result": result}
                # Only follow matching edges
                next_nodes = []
                for tgt in adj.get(node_id, []):
                    label_req = edge_conditions.get((node_id, tgt), "")
                    if not label_req:
                        next_nodes.append(tgt)
                    elif label_req == "true" and result:
                        next_nodes.append(tgt)
                    elif label_req == "false" and not result:
                        next_nodes.append(tgt)
                queue = next_nodes + queue
                _update_step(step_id, "completed", output)
                continue

            elif ntype == "action":
                action_type = node.get("action") or config.get("action_type", "")
                handler = ACTION_MAP.get(action_type)
                if handler:
                    try:
                        success, output = handler(config, ctx)
                    except Exception as e:
                        success, output = False, {"error": str(e)}
                else:
                    success, output = False, {"error": f"Unknown action: {action_type}"}
                # Merge output into context
                ctx = {**ctx, **{k: v for k, v in output.items() if not k.startswith("_")}}

            status = "completed" if success else "failed"
            _update_step(step_id, status, output)

            if not success:
                _finish_run(run_id, "failed", output.get("error"))
                return

            # Enqueue next nodes
            for tgt in adj.get(node_id, []):
                if tgt not in visited:
                    queue.append(tgt)

        _finish_run(run_id, "completed")
    except Exception as e:
        log.exception("Workflow run %s crashed", run_id)
        _finish_run(run_id, "failed", traceback.format_exc())


def _update_step(step_id: int, status: str, output: dict):
    db = _db()
    db.execute(
        "UPDATE workflow_run_steps SET status=?, output_data=?, completed_at=? WHERE id=?",
        (status, json.dumps(output), _now(), step_id)
    )
    db.commit(); db.close()


def _finish_run(run_id: int, status: str, error: str = None):
    db = _db()
    db.execute(
        "UPDATE workflow_runs SET status=?, completed_at=?, error=? WHERE id=?",
        (status, _now(), error, run_id)
    )
    db.commit(); db.close()
    log.info("Workflow run %s → %s", run_id, status)


# ──────────────────────────────────────────────────────────────────────────────
# Trigger listeners (called from app event hooks)
# ──────────────────────────────────────────────────────────────────────────────
def fire_trigger(trigger_type: str, context: dict):
    """
    Called by the Flask app when an event happens.
    Finds all enabled workflows with a matching trigger and runs them.
    """
    try:
        db = _db()
        workflows = db.execute(
            "SELECT id, trigger_config FROM workflow_definitions WHERE trigger_type=? AND enabled=1",
            (trigger_type,)
        ).fetchall()
        db.close()
        for wf in workflows:
            try:
                tcfg = json.loads(wf["trigger_config"] or "{}")
                # Apply any trigger-level filters
                if _trigger_matches(tcfg, context):
                    execute_workflow(wf["id"], context)
            except Exception:
                log.exception("Failed to fire workflow %s for trigger %s", wf["id"], trigger_type)
    except Exception:
        log.exception("fire_trigger crashed for %s", trigger_type)


def _trigger_matches(tcfg: dict, ctx: dict) -> bool:
    """Check if trigger config filters match the event context."""
    for key, expected in tcfg.items():
        if key.startswith("_"):
            continue
        actual = str(ctx.get(key, ""))
        if str(expected) not in actual:
            return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Schedule runner — polls every 60s for schedule-triggered workflows
# ──────────────────────────────────────────────────────────────────────────────
_schedule_thread = None
_stop_event      = threading.Event()


def start_schedule_runner():
    global _schedule_thread
    if _schedule_thread and _schedule_thread.is_alive():
        return
    _stop_event.clear()
    _schedule_thread = threading.Thread(target=_schedule_loop, daemon=True, name="wf-scheduler")
    _schedule_thread.start()
    log.info("Workflow schedule runner started")


def _schedule_loop():
    while not _stop_event.wait(60):
        try:
            db = _db()
            workflows = db.execute(
                "SELECT id, trigger_config FROM workflow_definitions WHERE trigger_type='schedule' AND enabled=1"
            ).fetchall()
            db.close()
            now = datetime.utcnow()
            for wf in workflows:
                tcfg = json.loads(wf["trigger_config"] or "{}")
                if _should_run_now(tcfg, now):
                    execute_workflow(wf["id"], {"scheduled_at": now.isoformat()})
        except Exception:
            log.exception("Schedule loop error")


def _should_run_now(tcfg: dict, now: datetime) -> bool:
    """Simple schedule check: hourly, daily at HH:MM, weekly on day."""
    freq = tcfg.get("frequency", "daily")
    if freq == "hourly":
        return now.minute == int(tcfg.get("minute", 0))
    if freq == "daily":
        h, m = map(int, tcfg.get("time", "08:00").split(":"))
        return now.hour == h and now.minute == m
    if freq == "weekly":
        day = tcfg.get("day", "monday").lower()
        days = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
        h, m = map(int, tcfg.get("time", "08:00").split(":"))
        return days[now.weekday()] == day and now.hour == h and now.minute == m
    return False
