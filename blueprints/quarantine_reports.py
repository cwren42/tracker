"""Quarantine reporting routes (campaigns, IOC export, phish-block PS, report)
for the quarantine blueprint. Split from blueprints/quarantine.py.
_parse_headers is re-exported from blueprints.quarantine for the preview route.
"""
"""
blueprints/quarantine.py — Exchange Online Quarantine viewer & analysis.

Routes:
  GET  /quarantine                — list + search
  POST /quarantine/sync           — pull fresh data from Exchange
  GET  /quarantine/<msg_id>       — detail / header analysis
  POST /quarantine/<msg_id>/release — release to inbox
  POST /quarantine/<msg_id>/delete  — permanent delete
  GET  /quarantine/campaigns      — campaign grouping view
  GET  /quarantine/export/iocs    — CSV export of IOCs
  GET  /quarantine/api/stats      — JSON stats for dashboard widget
"""
import csv
import io
import json
import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, Response, flash, jsonify, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from sqlalchemy import case, func, or_, text

from extensions import db
from models import (AzureIntegrationConfig, QuarantineIOC, QuarantineMessage,
                    now_mst)
from utils import admin_required, email_access_required

logger = logging.getLogger(__name__)

from blueprints.quarantine import bp


@bp.route("/quarantine/campaigns")
@login_required
@admin_required
def quarantine_campaigns():
    days = request.args.get("days", "30", type=str)
    cutoff = datetime.utcnow() - timedelta(days=int(days) if days.isdigit() else 30)

    # Group by campaign_id (=sender_domain), count messages, get threat types
    rows = (
        db.session.query(
            QuarantineMessage.campaign_id,
            QuarantineMessage.sender_domain,
            func.count(QuarantineMessage.id).label("msg_count"),
            func.min(QuarantineMessage.received_time).label("first_seen"),
            func.max(QuarantineMessage.received_time).label("last_seen"),
            func.count(QuarantineMessage.id.distinct()).label("unique_recipients"),
        )
        .filter(
            QuarantineMessage.received_time >= cutoff,
            QuarantineMessage.campaign_id.isnot(None),
        )
        .group_by(QuarantineMessage.campaign_id, QuarantineMessage.sender_domain)
        .having(func.count(QuarantineMessage.id) >= 2)
        .order_by(func.count(QuarantineMessage.id).desc())
        .all()
    )

    # For each campaign, grab a sample message and dominant threat type
    campaigns = []
    for row in rows:
        sample = (
            QuarantineMessage.query
            .filter_by(campaign_id=row.campaign_id)
            .filter(QuarantineMessage.received_time >= cutoff)
            .order_by(QuarantineMessage.received_time.desc())
            .first()
        )
        threat_counts = (
            db.session.query(
                QuarantineMessage.threat_type,
                func.count(QuarantineMessage.id).label("c"),
            )
            .filter_by(campaign_id=row.campaign_id)
            .filter(QuarantineMessage.received_time >= cutoff)
            .group_by(QuarantineMessage.threat_type)
            .order_by(func.count(QuarantineMessage.id).desc())
            .first()
        )
        campaigns.append({
            "campaign_id": row.campaign_id,
            "sender_domain": row.sender_domain,
            "msg_count": row.msg_count,
            "first_seen": row.first_seen,
            "last_seen": row.last_seen,
            "dominant_threat": threat_counts.threat_type if threat_counts else "Unknown",
            "sample": sample,
        })

    return render_template(
        "quarantine_campaigns.html",
        campaigns=campaigns,
        days=days,
        now=datetime.utcnow(),
    )


@bp.route("/quarantine/export/iocs")
@login_required
@admin_required
def quarantine_export_iocs():
    days = request.args.get("days", "30", type=str)
    cutoff = datetime.utcnow() - timedelta(days=int(days) if days.isdigit() else 30)

    iocs = (
        db.session.query(QuarantineIOC)
        .join(QuarantineMessage, QuarantineMessage.message_id == QuarantineIOC.message_id)
        .filter(QuarantineMessage.received_time >= cutoff)
        .order_by(QuarantineIOC.ioc_type, QuarantineIOC.ioc_value)
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ioc_type", "ioc_value", "threat_label", "seen_count", "first_seen"])
    for ioc in iocs:
        writer.writerow([
            ioc.ioc_type,
            ioc.ioc_value,
            ioc.threat_label or "",
            ioc.seen_count,
            ioc.first_seen.isoformat() if ioc.first_seen else "",
        ])

    output.seek(0)
    filename = f"quarantine_iocs_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/quarantine/api/ps-block-phish")
@login_required
@admin_required
def quarantine_ps_block_phish():
    """
    Return a ready-to-run PowerShell script that blocks all active credential-phishing
    sender domains at the door — Tenant Allow/Block List + Transport Rule hard-reject.
    """
    days_raw = request.args.get("days", "30")
    days = int(days_raw) if days_raw.isdigit() else 30
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Pull phishing sender domains, ordered by volume
    domain_rows = (
        db.session.query(
            QuarantineMessage.sender_domain,
            func.count(QuarantineMessage.id).label("cnt"),
        )
        .filter(
            QuarantineMessage.received_time >= cutoff,
            QuarantineMessage.threat_type == "Phish",
            QuarantineMessage.sender_domain.isnot(None),
            QuarantineMessage.sender_domain != "",
        )
        .group_by(QuarantineMessage.sender_domain)
        .order_by(func.count(QuarantineMessage.id).desc())
        .limit(50)
        .all()
    )

    # Pull phishing sender *addresses* for extra granularity (top 30)
    addr_rows = (
        db.session.query(
            QuarantineMessage.sender_address,
            func.count(QuarantineMessage.id).label("cnt"),
        )
        .filter(
            QuarantineMessage.received_time >= cutoff,
            QuarantineMessage.threat_type == "Phish",
            QuarantineMessage.sender_address.isnot(None),
            QuarantineMessage.sender_address != "",
        )
        .group_by(QuarantineMessage.sender_address)
        .order_by(func.count(QuarantineMessage.id).desc())
        .limit(30)
        .all()
    )

    # Most common phishing subjects (top 10, for reference comments)
    subj_rows = (
        db.session.query(
            QuarantineMessage.subject,
            func.count(QuarantineMessage.id).label("cnt"),
        )
        .filter(
            QuarantineMessage.received_time >= cutoff,
            QuarantineMessage.threat_type == "Phish",
            QuarantineMessage.subject.isnot(None),
        )
        .group_by(QuarantineMessage.subject)
        .order_by(func.count(QuarantineMessage.id).desc())
        .limit(10)
        .all()
    )

    # Total phish message count
    phish_total = sum(r.cnt for r in domain_rows)

    domains  = [r.sender_domain for r in domain_rows]
    addrs    = [r.sender_address for r in addr_rows]
    subjects = [r.subject for r in subj_rows]

    if not domains:
        return jsonify({"error": "No phishing messages found in the last {} days. Run a sync first.".format(days)}), 404

    # Helper: PowerShell string-escape single quotes
    def ps_str(s):
        return s.replace("'", "''") if s else ""

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    date_tag = datetime.utcnow().strftime("%Y%m%d")

    domain_list = "\n".join(f'        "{ps_str(d)}"{"," if i < len(domains)-1 else ""}' for i, d in enumerate(domains))
    addr_list   = "\n".join(f'        "{ps_str(a)}"{"," if i < len(addrs)-1 else ""}' for i, a in enumerate(addrs))
    subj_comment = "\n".join(f"#   [{r.cnt:3d}x]  {r.subject[:80] if r.subject else '(no subject)'}" for r in subj_rows)

    script = f"""# =============================================================================
#  CREDENTIAL PHISHING BLOCK SCRIPT
#  Generated : {now_str} UTC
#  Period    : Last {days} days
#  Phish msgs: {phish_total}  across  {len(domains)} sender domains
#  Run in   : Exchange Online PowerShell (EXO V3 module)
#  Requires : Security Administrator or Exchange Administrator role
# =============================================================================
#
#  TOP PHISHING SUBJECTS (for situational awareness):
{subj_comment}
#
# =============================================================================

#region CONNECT
Connect-ExchangeOnline -UserPrincipalName admin@cirque.com
Connect-IPPSSession    -UserPrincipalName admin@cirque.com
#endregion

# =============================================================================
#  STEP 1 — Block sender domains in Tenant Allow/Block List (TABL)
#           This is the fastest, lowest-overhead block. Takes effect in < 1 min.
# =============================================================================

$phishDomains = @(
{domain_list}
)

Write-Host "Adding $($phishDomains.Count) phishing domains to Tenant Allow/Block List..." -ForegroundColor Yellow

New-TenantAllowBlockListItems `
    -ListType Sender `
    -Block `
    -Entries $phishDomains `
    -NoExpiration `
    -Notes 'Credential phishing block — auto-generated {now_str} UTC ({phish_total} msgs in {days}d)'

Write-Host "TABL entries created." -ForegroundColor Green

# Verify
Get-TenantAllowBlockListItems -ListType Sender -Block | Where-Object {{
    $_.Value -in $phishDomains
}} | Select-Object Value, EntryType, LastModifiedDateTime, Notes | Format-Table -AutoSize


# =============================================================================
#  STEP 2 — Transport Rule: hard-reject with 5.7.1 (backup + visible to sender)
#           Creates an NDR so the attacker knows the domain is blocked.
# =============================================================================

$ruleName = 'SECURITY: Block Credential Phish Senders — SOC {date_tag}'

# Check if rule already exists
if (Get-TransportRule -Identity $ruleName -ErrorAction SilentlyContinue) {{
    Write-Host "Transport rule '$ruleName' already exists — updating sender list." -ForegroundColor Cyan
    Set-TransportRule -Identity $ruleName -SenderDomainIs $phishDomains
}} else {{
    New-TransportRule `
        -Name $ruleName `
        -Enabled $true `
        -SenderDomainIs $phishDomains `
        -RejectMessageReasonText 'This message was rejected by Cirque Corporation security policy. Reference: PHISH-{date_tag}' `
        -RejectMessageEnhancedStatusCode '5.7.1' `
        -Mode Enforce `
        -Priority 0 `
        -Comments 'Credential phishing block — {phish_total} msgs in {days}d period. Generated {now_str} UTC.'
    Write-Host "Transport rule created at priority 0." -ForegroundColor Green
}}


# =============================================================================
#  STEP 3 — Block individual sender addresses (high-precision secondary block)
#           Useful for attackers using shared domains (e.g. gmail, outlook).
# =============================================================================

$phishAddrs = @(
{addr_list}
)

Write-Host "Adding $($phishAddrs.Count) sender addresses to TABL..." -ForegroundColor Yellow

New-TenantAllowBlockListItems `
    -ListType Sender `
    -Block `
    -Entries $phishAddrs `
    -ExpirationDate (Get-Date).AddDays(90) `
    -Notes 'Credential phishing — individual addresses — 90-day block {now_str} UTC'

Write-Host "Done." -ForegroundColor Green


# =============================================================================
#  STEP 4 — Verify the block is active for each domain
# =============================================================================

Write-Host "`nCurrent TABL blocks for phish domains:" -ForegroundColor Cyan
Get-TenantAllowBlockListItems -ListType Sender -Block | Where-Object {{
    $phishDomains -contains $_.Value -or $phishAddrs -contains $_.Value
}} | Select-Object Value, ExpirationDate, Notes | Sort-Object Value | Format-Table -AutoSize


# =============================================================================
#  STEP 5 — Check for any already-delivered phish from these domains
#           (review inbox for false negatives before this period)
# =============================================================================

Write-Host "`nChecking Message Trace for any delivered phish from these domains (last 10 days)..." -ForegroundColor Yellow

$results = Get-MessageTrace `
    -StartDate (Get-Date).AddDays(-10) `
    -EndDate   (Get-Date) `
    -PageSize  100 |
    Where-Object {{ $phishDomains | Where-Object {{ $_ -eq ($_.SenderAddress -split '@')[1] }} }}

if ($results) {{
    Write-Host "WARNING: $($results.Count) messages may have been delivered — review below:" -ForegroundColor Red
    $results | Select-Object Received, SenderAddress, RecipientAddress, Subject, Status | Format-Table -AutoSize
}} else {{
    Write-Host "No delivered messages found from blocked domains in the last 10 days." -ForegroundColor Green
}}


# =============================================================================
#  TO ROLL BACK (if false positives reported)
# =============================================================================
#
#  Remove TABL domain entries:
#    Remove-TenantAllowBlockListItems -ListType Sender -Entries $phishDomains
#
#  Disable transport rule:
#    Disable-TransportRule -Identity '{rule_name}'
#
#  Remove transport rule:
#    Remove-TransportRule  -Identity '{rule_name}'
#
# =============================================================================
Write-Host "`nPhishing block complete. Monitor for false positive reports." -ForegroundColor Green
"""

    return jsonify({
        "script": script,
        "domain_count": len(domains),
        "addr_count": len(addrs),
        "phish_total": phish_total,
        "days": days,
        "generated_at": now_str,
    })


@login_required
@admin_required
def quarantine_api_stats():
    cutoff_7d = datetime.utcnow() - timedelta(days=7)
    cutoff_30d = datetime.utcnow() - timedelta(days=30)
    total_7d = QuarantineMessage.query.filter(QuarantineMessage.received_time >= cutoff_7d).count()
    phish_7d = QuarantineMessage.query.filter(
        QuarantineMessage.received_time >= cutoff_7d,
        QuarantineMessage.threat_type == "Phish",
    ).count()
    malware_7d = QuarantineMessage.query.filter(
        QuarantineMessage.received_time >= cutoff_7d,
        QuarantineMessage.threat_type == "Malware",
    ).count()
    campaigns_30d = (
        db.session.query(func.count(QuarantineMessage.campaign_id.distinct()))
        .filter(QuarantineMessage.received_time >= cutoff_30d)
        .filter(QuarantineMessage.campaign_id.isnot(None))
        .scalar()
    ) or 0
    last_sync = db.session.query(func.max(QuarantineMessage.last_synced)).scalar()

    return jsonify({
        "total_7d": total_7d,
        "phish_7d": phish_7d,
        "malware_7d": malware_7d,
        "campaigns_30d": campaigns_30d,
        "last_sync": last_sync.isoformat() if last_sync else None,
    })



@bp.route("/quarantine/report")
@login_required
@admin_required
def quarantine_report():
    """Analytics & PowerShell remediation report."""
    days_raw = request.args.get("days", "30")
    days = int(days_raw) if days_raw.isdigit() else 30
    cutoff = datetime.utcnow() - timedelta(days=days)

    # ── Daily volume by threat type (Chart.js stacked bar) ────────────────
    daily_rows = (
        db.session.query(
            func.date_trunc("day", QuarantineMessage.received_time).label("day"),
            QuarantineMessage.threat_type,
            func.count(QuarantineMessage.id).label("cnt"),
        )
        .filter(QuarantineMessage.received_time >= cutoff)
        .group_by(func.date_trunc("day", QuarantineMessage.received_time), QuarantineMessage.threat_type)
        .order_by(func.date_trunc("day", QuarantineMessage.received_time))
        .all()
    )
    daily_dict: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in daily_rows:
        if row.day:
            daily_dict[row.day.strftime("%Y-%m-%d")][row.threat_type or "Unknown"] += row.cnt
    all_days = sorted(daily_dict.keys())
    threat_colors = {
        "Phish":   ("rgba(220,53,69,.8)",   "rgba(220,53,69,.2)"),
        "Malware": ("rgba(111,66,193,.8)",  "rgba(111,66,193,.2)"),
        "Spam":    ("rgba(255,193,7,.8)",   "rgba(255,193,7,.2)"),
        "Bulk":    ("rgba(13,202,240,.8)",  "rgba(13,202,240,.2)"),
        "Unknown": ("rgba(108,117,125,.8)", "rgba(108,117,125,.2)"),
    }
    chart_datasets = [
        {
            "label": t,
            "data": [daily_dict.get(d, {}).get(t, 0) for d in all_days],
            "backgroundColor": threat_colors[t][0],
            "borderColor": threat_colors[t][0],
            "borderWidth": 0,
        }
        for t in ["Phish", "Malware", "Spam", "Bulk", "Unknown"]
    ]
    chart_json = json.dumps({"labels": all_days, "datasets": chart_datasets})

    # ── Threat & policy distributions ─────────────────────────────────────
    threat_dist = (
        db.session.query(QuarantineMessage.threat_type, func.count(QuarantineMessage.id).label("cnt"))
        .filter(QuarantineMessage.received_time >= cutoff)
        .group_by(QuarantineMessage.threat_type)
        .order_by(func.count(QuarantineMessage.id).desc())
        .all()
    )
    policy_dist = (
        db.session.query(QuarantineMessage.policy_type, func.count(QuarantineMessage.id).label("cnt"))
        .filter(QuarantineMessage.received_time >= cutoff)
        .group_by(QuarantineMessage.policy_type)
        .order_by(func.count(QuarantineMessage.id).desc())
        .all()
    )

    # ── Top 20 sender domains ──────────────────────────────────────────────
    domain_rows = (
        db.session.query(
            QuarantineMessage.sender_domain,
            func.count(QuarantineMessage.id).label("total"),
            func.count(case((QuarantineMessage.spf_result.in_(["fail", "softfail"]), 1))).label("spf_fails"),
            func.count(case((QuarantineMessage.dmarc_result == "fail", 1))).label("dmarc_fails"),
        )
        .filter(QuarantineMessage.received_time >= cutoff, QuarantineMessage.sender_domain.isnot(None))
        .group_by(QuarantineMessage.sender_domain)
        .order_by(func.count(QuarantineMessage.id).desc())
        .limit(20)
        .all()
    )
    # Add dominant threat per domain (one extra query per domain — acceptable for ≤20 rows)
    top_domains = []
    for row in domain_rows:
        threat_row = (
            db.session.query(QuarantineMessage.threat_type, func.count(QuarantineMessage.id).label("c"))
            .filter(QuarantineMessage.received_time >= cutoff, QuarantineMessage.sender_domain == row.sender_domain)
            .group_by(QuarantineMessage.threat_type)
            .order_by(func.count(QuarantineMessage.id).desc())
            .first()
        )
        top_domains.append({
            "domain":       row.sender_domain,
            "total":        row.total,
            "spf_fails":    row.spf_fails,
            "dmarc_fails":  row.dmarc_fails,
            "dominant_threat": (threat_row.threat_type if threat_row else "Unknown") or "Unknown",
        })

    # ── Top 15 targeted recipients ─────────────────────────────────────────
    top_recipients = (
        db.session.query(
            QuarantineMessage.recipient_address,
            func.count(QuarantineMessage.id).label("cnt"),
        )
        .filter(QuarantineMessage.received_time >= cutoff, QuarantineMessage.recipient_address.isnot(None))
        .group_by(QuarantineMessage.recipient_address)
        .order_by(func.count(QuarantineMessage.id).desc())
        .limit(15)
        .all()
    )

    # ── Auth failure totals ────────────────────────────────────────────────
    total_in_period = QuarantineMessage.query.filter(QuarantineMessage.received_time >= cutoff).count()
    spf_fail_total = (
        QuarantineMessage.query
        .filter(QuarantineMessage.received_time >= cutoff, QuarantineMessage.spf_result.in_(["fail", "softfail"]))
        .count()
    )
    dkim_fail_total = (
        QuarantineMessage.query
        .filter(QuarantineMessage.received_time >= cutoff, QuarantineMessage.dkim_result == "fail")
        .count()
    )
    dmarc_fail_total = (
        QuarantineMessage.query
        .filter(QuarantineMessage.received_time >= cutoff, QuarantineMessage.dmarc_result == "fail")
        .count()
    )

    # ── Risk distribution ─────────────────────────────────────────────────
    # Computed in Python from risk_score property (not stored in DB), so pull a sample
    risk_msgs = (
        QuarantineMessage.query
        .filter(QuarantineMessage.received_time >= cutoff)
        .with_entities(
            QuarantineMessage.threat_type,
            QuarantineMessage.spf_result,
            QuarantineMessage.dkim_result,
            QuarantineMessage.dmarc_result,
            QuarantineMessage.url_count,
            QuarantineMessage.attachment_count,
        )
        .all()
    )
    risk_dist = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}
    for m in risk_msgs:
        score = 0
        tt = (m.threat_type or "").lower()
        if tt == "phish":     score += 40
        elif tt == "malware": score += 50
        elif tt == "spam":    score += 10
        if (m.spf_result or "") in ("fail", "softfail"): score += 20
        if (m.dkim_result or "") == "fail":               score += 20
        if (m.dmarc_result or "") == "fail":              score += 15
        if (m.url_count or 0) > 3:        score += 5
        if (m.attachment_count or 0) > 0: score += 5
        score = min(score, 100)
        if score >= 75:   risk_dist["Critical"] += 1
        elif score >= 50: risk_dist["High"] += 1
        elif score >= 25: risk_dist["Medium"] += 1
        else:             risk_dist["Low"] += 1

    # ── PowerShell data: partition domains by threat category ─────────────
    phish_malware_domains = [d["domain"] for d in top_domains if d["dominant_threat"] in ("Phish", "Malware")][:10]
    spam_bulk_domains     = [d["domain"] for d in top_domains if d["dominant_threat"] in ("Spam", "Bulk")][:8]
    all_top_domains       = [d["domain"] for d in top_domains[:15]]

    last_sync = db.session.query(func.max(QuarantineMessage.last_synced)).scalar()

    return render_template(
        "quarantine_report.html",
        days=days_raw,
        total_in_period=total_in_period,
        threat_dist=threat_dist,
        policy_dist=policy_dist,
        top_domains=top_domains,
        top_recipients=top_recipients,
        spf_fail_total=spf_fail_total,
        dkim_fail_total=dkim_fail_total,
        dmarc_fail_total=dmarc_fail_total,
        risk_dist=risk_dist,
        chart_json=chart_json,
        phish_malware_domains=phish_malware_domains,
        spam_bulk_domains=spam_bulk_domains,
        all_top_domains=all_top_domains,
        last_sync=last_sync,
        now=datetime.utcnow(),
    )


# ─── Utility ─────────────────────────────────────────────────────────────────

def _parse_headers(raw: str) -> list[dict]:
    """Parse raw RFC 2822 headers into [{name, value}, ...] list."""
    headers = []
    if not raw:
        return headers
    current_name = current_value = None
    for line in raw.splitlines():
        if not line:
            continue
        if line[0] in (' ', '\t') and current_name:
            # Continuation line
            current_value = (current_value or "") + " " + line.strip()
        elif ':' in line:
            if current_name:
                headers.append({"name": current_name, "value": current_value or ""})
            parts = line.split(':', 1)
            current_name = parts[0].strip()
            current_value = parts[1].strip() if len(parts) > 1 else ""
        else:
            if current_name:
                current_value = (current_value or "") + " " + line.strip()
    if current_name:
        headers.append({"name": current_name, "value": current_value or ""})
    return headers

