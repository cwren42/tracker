#!/usr/bin/env python3
"""Extracts route functions from app.py into blueprint files."""

import ast, os, re
from collections import defaultdict

# ─── Blueprint assignment rules ────────────────────────────────────────────
# Checked in order; first match wins. Prefix must NOT start with '/' for '/' special case.
URL_RULES = [
    ('/', 'dashboard'),               # exact root only
    ('/dashboard', 'dashboard'),
    ('/api/dashboard', 'dashboard'),
    ('/login', 'auth'),
    ('/logout', 'auth'),
    ('/users', 'auth'),
    ('/csat/', 'auth'),
    ('/settings', 'settings'),
    ('/api/settings', 'settings'),
    ('/api/cloudflare', 'settings'),
    ('/api/unifi', 'settings'),
    ('/api/ad/', 'settings'),
    ('/init-db', 'settings'),
    ('/api/license', 'settings'),
    ('/soc2', 'soc2'),
    ('/api/soc2', 'soc2'),
    ('/compliance', 'soc2'),
    ('/system-description', 'soc2'),
    ('/policies', 'soc2'),
    ('/controls', 'soc2'),
    ('/risks', 'soc2'),
    ('/licenses', 'licenses'),
    ('/tickets', 'tickets'),
    ('/api/support-tickets', 'tickets'),
    ('/monitoring', 'monitoring'),
    ('/backups', 'monitoring'),
    ('/api/proxmox', 'monitoring'),
    ('/alerts/', 'monitoring'),
    ('/api/alerts/', 'monitoring'),
    ('/api/notifications/', 'monitoring'),
    ('/agent/', 'monitoring'),
    ('/rmm', 'rmm'),
    ('/terminal/', 'rmm'),
    ('/api/terminal/', 'rmm'),
    ('/api/linux-agent/', 'rmm'),
    ('/api/rmm', 'rmm'),
    ('/download/', 'rmm'),
    ('/employees', 'employees'),
    ('/remote-sessions/', 'assets'),
    ('/asset/', 'assets'),
    ('/assets', 'assets'),
    ('/api/asset/', 'assets'),
    ('/search', 'assets'),
    ('/reports', 'reports'),
    ('/api/reports/', 'reports'),
    ('/api/ai', 'ai'),
    ('/workflows', 'ai'),
    ('/api/workflows', 'ai'),
    ('/vulnerabilities', 'vulnerabilities'),
    ('/api/vulnerabilities', 'vulnerabilities'),
]

HELPER_TO_BP = {
    'get_default_widgets': 'dashboard',
    'get_dashboard_data': 'dashboard',
    'get_available_widgets': 'dashboard',
    '_get_cloudflare_service_status': 'settings',
    '_mask_secret': 'settings',
    '_decode_cloudflare_tunnel_token': 'settings',
    '_read_cloudflare_server_config': 'settings',
    '_normalize_script_file_type': 'settings',
    '_ensure_rmm_script_library_table': 'settings',
    '_send_script_to_agent': 'settings',
    '_wait_for_script_result': 'settings',
    '_verify_agent_token': 'rmm',
    '_dt_iso': 'rmm',
    '_agent_tz_offset_minutes': 'rmm',
    '_eagle_date_params': 'rmm',
    'perform_intune_asset_sync': 'assets',
    '_get_or_create_site_enrollment_token': 'rmm',
    '_ticket_sla_check': 'tickets',
    '_eagle_report_scheduler': 'rmm',
    '_asset_eol_check': 'assets',
    'get_db': None,
}

STAY_IN_APP = {
    'add_security_headers', 'inject_impersonation_state',
    'page_not_found', 'internal_server_error', 'forbidden',
    'get_db', '_valid_agent_key',
}

def url_to_bp(url):
    for prefix, bp in URL_RULES:
        if prefix == '/':
            if url == '/':
                return bp
        else:
            if url == prefix:
                return bp
            # If prefix ends with '/', don't add another slash
            if prefix.endswith('/'):
                if url.startswith(prefix):
                    return bp
            else:
                if url.startswith(prefix + '/') or url.startswith(prefix + '<'):
                    return bp
    return 'misc'

# ─── Standard imports header ───────────────────────────────────────────────
BP_HEADER = '''import json
import os
import re
import subprocess
import threading
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, abort, current_app, flash, g, jsonify,
                   redirect, render_template, request, send_file, session,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import func, or_, text

from extensions import db, limiter
from models import (
    AuditTrail, Asset, AssetHistory, Control, CustomReport, DashboardWidget,
    Employee, License, LicenseAssignment, LicenseInfo, MaintenanceWindow,
    MonitoringAlert, MonitoringCheck, MonitoringProfile, Policy, PolicySection,
    ProxmoxBackupJob, ProxmoxZfsPool, RemoteSession, Risk, Setting,
    SupportTicket, TicketActivity, TicketNote, User, now_mst, allowed_file,
    SystemDescription, AzureIntegrationConfig, ControlRiskMapping,
)
from soc2_models import SOC2Control, EvidenceSnapshot, EvidenceFile
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
)
'''

# ─── Parse app.py ──────────────────────────────────────────────────────────
with open('/var/www/tracker/app.py') as f:
    src = f.read()
lines = src.split('\n')
tree = ast.parse(src)

funcs = []
for node in ast.iter_child_nodes(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.col_offset == 0:
        urls = []
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and hasattr(deco.func, 'attr') and deco.func.attr == 'route':
                if deco.args:
                    urls.append(ast.literal_eval(deco.args[0]))
        deco_start = node.decorator_list[0].lineno if node.decorator_list else node.lineno
        funcs.append({
            'name': node.name, 'urls': urls,
            'deco_start': deco_start,        # 1-indexed
            'line_start': node.lineno,        # 1-indexed
            'line_end': node.end_lineno,      # 1-indexed
            'is_route': bool(urls)
        })

# Assign each function to a blueprint
for f in funcs:
    if f['name'] in STAY_IN_APP:
        f['bp'] = None
    elif not f['is_route']:
        f['bp'] = HELPER_TO_BP.get(f['name'], None)
    else:
        f['bp'] = url_to_bp(f['urls'][0])

# ─── Extract source block for each function ────────────────────────────────
def extract_func(f):
    start = f['deco_start'] - 1   # 0-indexed
    end   = f['line_end']          # exclusive 0-indexed (since line_end is 1-indexed last line)
    block = '\n'.join(lines[start:end])
    block = re.sub(r'\bapp\.route\b', 'bp.route', block)
    block = re.sub(r'\bapp\.logger\b', 'current_app.logger', block)
    block = re.sub(r'\bapp\.config\b', 'current_app.config', block)
    block = re.sub(r'\bapp\.root_path\b', 'current_app.root_path', block)
    return block

# ─── Collect interstitial module-level code between functions ──────────────
all_bp_funcs = sorted([f for f in funcs if f.get('bp')], key=lambda x: x['deco_start'])

# Find where our "routes" section begins — first function at L160
routes_section_start = min(f['deco_start'] for f in funcs if f.get('bp'))

interstitial = defaultdict(list)
for i, f in enumerate(all_bp_funcs):
    # Gap before this function
    prev_end = all_bp_funcs[i-1]['line_end'] if i > 0 else routes_section_start - 1
    gap_start_idx = prev_end       # 0-indexed (prev_end is 1-indexed last line of prev = 0-indexed idx)
    gap_end_idx   = f['deco_start'] - 1  # 0-indexed exclusive (deco_start is 1-indexed)

    if gap_end_idx > gap_start_idx:
        gap_text = '\n'.join(lines[gap_start_idx:gap_end_idx]).strip()
        if gap_text:
            interstitial[f['bp']].append(gap_text)

# ─── Group by blueprint ────────────────────────────────────────────────────
bp_funcs = defaultdict(list)
for f in funcs:
    if f.get('bp'):
        bp_funcs[f['bp']].append(f)

# ─── Write blueprint files ─────────────────────────────────────────────────
os.makedirs('/var/www/tracker/blueprints', exist_ok=True)

all_bps = sorted(bp_funcs.keys())
print(f"Blueprints to create: {all_bps}")

# Track url_for mapping for templates
url_for_map = {}

for bp_name in all_bps:
    bp_funcs_list = sorted(bp_funcs[bp_name], key=lambda x: x['deco_start'])
    
    parts = [BP_HEADER, f"\nbp = Blueprint('{bp_name}', __name__)\n"]
    
    # Add any interstitial module-level code
    seen_interstitial = set()
    for chunk in interstitial.get(bp_name, []):
        if chunk not in seen_interstitial:
            parts.append('\n' + chunk + '\n')
            seen_interstitial.add(chunk)
    
    # Add each function
    for f in bp_funcs_list:
        parts.append('\n\n' + extract_func(f))
        if f['is_route']:
            url_for_map[f['name']] = f"{bp_name}.{f['name']}"
    
    content = '\n'.join(parts)
    out_path = f'/var/www/tracker/blueprints/{bp_name}.py'
    with open(out_path, 'w') as fh:
        fh.write(content)
    
    route_count = sum(1 for f in bp_funcs_list if f['is_route'])
    helper_count = sum(1 for f in bp_funcs_list if not f['is_route'])
    print(f"  ✓ blueprints/{bp_name}.py — {route_count} routes, {helper_count} helpers")

# ─── Write blueprints/__init__.py ─────────────────────────────────────────
with open('/var/www/tracker/blueprints/__init__.py', 'w') as fh:
    fh.write('# Blueprint package\n')

print("\nbluprints/__init__.py written")

# ─── Write url_for mapping to a file for the template fix step ────────────
import json
with open('/var/www/tracker/url_for_map.json', 'w') as fh:
    json.dump(url_for_map, fh, indent=2)

print(f"\nURL mapping written: {len(url_for_map)} routes")
print("\nSample mappings:")
for k, v in list(url_for_map.items())[:10]:
    print(f"  '{k}' → '{v}'")

# ─── Verify no routes missed ───────────────────────────────────────────────
misc_routes = [f for f in funcs if f.get('bp') == 'misc' and f['is_route']]
if misc_routes:
    print(f"\n⚠ Unassigned routes ({len(misc_routes)}):")
    for f in misc_routes:
        print(f"  {f['name']} {f['urls']}")
