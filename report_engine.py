"""
Cirque RMM — Report Engine
Generates in-browser, CSV, and PDF reports.
PDF uses weasyprint if available, falls back to HTML.
"""
import csv, io, json, logging, os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from pg_db import pg_connect

log = logging.getLogger("report_engine")
REPORT_DIR = "/var/www/tracker/static/reports"


def _db():
    return pg_connect()


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


os.makedirs(REPORT_DIR, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# Data fetchers per report type
# ──────────────────────────────────────────────────────────────────────────────
def _fetch_vulnerability(cfg: dict) -> tuple:
    db = _db()
    severity_filter = cfg.get("severity")  # None = all
    status_filter   = cfg.get("status", "open")
    sql = """
        SELECT dv.cve_id, dv.severity, dv.status, dv.plan_date,
               a.name as asset_name, a.asset_tag,
               dv.updated_at
        FROM device_vulnerability dv
        JOIN asset a ON a.id = dv.asset_id
        WHERE 1=1
    """
    params = []
    if severity_filter:
        sql += " AND LOWER(dv.severity)=?"
        params.append(severity_filter.lower())
    if status_filter:
        sql += " AND dv.status=?"
        params.append(status_filter)
    sql += " ORDER BY CASE dv.severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 WHEN 'Medium' THEN 3 ELSE 4 END, dv.cve_id"
    rows = db.execute(sql, params).fetchall()
    db.close()
    cols = ["CVE ID","Severity","Status","Plan Date","Asset","Asset Tag","Last Updated"]
    return cols, [list(r) for r in rows]


def _fetch_patch_compliance(cfg: dict) -> tuple:
    db = _db()
    rows = db.execute("""
        SELECT a.name as asset_name, a.asset_tag,
               COUNT(p.id) as total_patches,
               SUM(CASE WHEN p.status='installed' THEN 1 ELSE 0 END) as installed,
               SUM(CASE WHEN p.status!='installed' THEN 1 ELSE 0 END) as missing,
               ra.last_seen, ra.online
        FROM rmm_agent ra
        JOIN asset a ON a.id = ra.asset_id
        LEFT JOIN rmm_patch p ON p.agent_id = ra.agent_id
        GROUP BY ra.agent_id
        ORDER BY missing DESC
    """).fetchall()
    db.close()
    data = []
    for r in rows:
        total    = r["total_patches"] or 0
        installed = r["installed"] or 0
        pct = f"{int(installed/total*100)}%" if total else "N/A"
        data.append([r["asset_name"], r["asset_tag"], total, installed,
                     r["missing"] or 0, pct,
                     "Online" if r["online"] else "Offline",
                     r["last_seen"] or "Never"])
    cols = ["Asset","Asset Tag","Total Patches","Installed","Missing","Compliance %","Agent Status","Last Seen"]
    return cols, data


def _fetch_asset_inventory(cfg: dict) -> tuple:
    db = _db()
    rows = db.execute("""
        SELECT a.name, a.asset_tag, a.category, a.manufacturer, a.model,
               a.serial_number, a.status, a.lifecycle_status,
               COALESCE(e.first_name||' '||e.last_name, '') as assigned_to,
               a.warranty_expiry, a.purchase_date, a.purchase_cost,
               a.location
        FROM asset a
        LEFT JOIN employee e ON e.id = a.assigned_employee_id
        ORDER BY a.category, a.name
    """).fetchall()
    db.close()
    cols = ["Name","Tag","Category","Manufacturer","Model","Serial","Status","Lifecycle",
            "Assigned To","Warranty Expiry","Purchase Date","Cost","Location"]
    return cols, [list(r) for r in rows]


def _fetch_tickets(cfg: dict) -> tuple:
    db = _db()
    days = int(cfg.get("days", 30))
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.execute("""
        SELECT t.id, t.title, t.status, t.priority, t.category,
               t.created_at, t.updated_at,
               EXTRACT(EPOCH FROM (COALESCE(t.updated_at, NOW()) - t.created_at::timestamp)) / 3600 AS age_hours
        FROM support_ticket t
        WHERE t.created_at >= ?
        ORDER BY t.created_at DESC
    """, (since,)).fetchall()
    db.close()
    cols = ["ID","Title","Status","Priority","Category","Created","Last Updated","Age (hrs)"]
    return cols, [list(r) for r in rows]


def _fetch_alerts(cfg: dict) -> tuple:
    db = _db()
    days = int(cfg.get("days", 30))
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.execute("""
        SELECT al.rule_name, al.severity, al.message,
               al.asset_name, al.triggered_at
        FROM alert_log al
        WHERE al.triggered_at >= ?
        ORDER BY al.triggered_at DESC
    """, (since,)).fetchall()
    db.close()
    cols = ["Rule","Severity","Message","Asset","Triggered At"]
    return cols, [list(r) for r in rows]


def _fetch_user_activity(cfg: dict) -> tuple:
    db = _db()
    days = int(cfg.get("days", 30))
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.execute("""
        SELECT action, username, details, ip_address, timestamp
        FROM audit_trail
        WHERE timestamp >= ?
        ORDER BY timestamp DESC
        LIMIT 2000
    """, (since,)).fetchall()
    db.close()
    cols = ["Action","User","Details","IP","Timestamp"]
    return cols, [list(r) for r in rows]


def _fetch_rmm_status(cfg: dict) -> tuple:
    db = _db()
    rows = db.execute("""
        SELECT a.name, a.asset_tag, ra.agent_id,
               ra.online, ra.last_seen, ra.agent_version,
               COUNT(pj.id) as patch_jobs,
               SUM(CASE WHEN pj.status='completed' THEN 1 ELSE 0 END) as jobs_done
        FROM rmm_agent ra
        JOIN asset a ON a.id = ra.asset_id
        LEFT JOIN rmm_patch_job pj ON pj.agent_id = ra.agent_id
        GROUP BY ra.agent_id
        ORDER BY ra.online DESC, a.name
    """).fetchall()
    db.close()
    cols = ["Asset","Tag","Agent ID","Online","Last Seen","Version","Patch Jobs","Completed"]
    data = []
    for r in rows:
        data.append([r["name"], r["asset_tag"], r["agent_id"],
                     "Yes" if r["online"] else "No",
                     r["last_seen"] or "Never", r["agent_version"] or "?",
                     r["patch_jobs"] or 0, r["jobs_done"] or 0])
    return cols, data


FETCHERS = {
    "vulnerability":    _fetch_vulnerability,
    "patch_compliance": _fetch_patch_compliance,
    "asset_inventory":  _fetch_asset_inventory,
    "tickets":          _fetch_tickets,
    "alerts":           _fetch_alerts,
    "user_activity":    _fetch_user_activity,
    "rmm_status":       _fetch_rmm_status,
}


# ──────────────────────────────────────────────────────────────────────────────
# CSV generator
# ──────────────────────────────────────────────────────────────────────────────
def generate_csv(cols: list, rows: list) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────────
# HTML report generator (also basis for PDF)
# ──────────────────────────────────────────────────────────────────────────────
def generate_html(name: str, report_type: str, cols: list, rows: list) -> str:
    sev_colors = {"critical":"#dc3545","high":"#fd7e14","medium":"#ffc107","low":"#198754","info":"#0dcaf0"}
    row_html = ""
    for r in rows:
        cells = ""
        for i, cell in enumerate(r):
            val  = str(cell) if cell is not None else ""
            cl   = ""
            style= ""
            # Color severity column
            if cols[i].lower() in ("severity","risk_level"):
                color = sev_colors.get(val.lower(), "")
                if color:
                    style = f"color:{color};font-weight:600;"
            cells += f'<td style="{style}">{val}</td>'
        row_html += f"<tr>{cells}</tr>"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body{{font-family:Arial,sans-serif;font-size:12px;color:#222;margin:20px;}}
h1{{font-size:18px;margin-bottom:4px;}}
.meta{{color:#666;font-size:11px;margin-bottom:16px;}}
table{{border-collapse:collapse;width:100%;}}
th{{background:#1a2634;color:#fff;padding:6px 10px;text-align:left;font-size:11px;}}
td{{padding:5px 10px;border-bottom:1px solid #e0e0e0;vertical-align:top;}}
tr:nth-child(even){{background:#f8f9fa;}}
</style></head>
<body>
<h1>&#128202; {name}</h1>
<div class="meta">Generated: {_now()} UTC &nbsp;|&nbsp; {len(rows)} records &nbsp;|&nbsp; Type: {report_type}</div>
<table>
<thead><tr>{"".join(f"<th>{c}</th>" for c in cols)}</tr></thead>
<tbody>{row_html}</tbody>
</table>
</body></html>"""


# ──────────────────────────────────────────────────────────────────────────────
# PDF generator (weasyprint or fallback HTML)
# ──────────────────────────────────────────────────────────────────────────────
def generate_pdf(html: str, filename: str) -> str:
    path = os.path.join(REPORT_DIR, filename)
    try:
        from weasyprint import HTML as WeasyHTML
        WeasyHTML(string=html).write_pdf(path)
        return path
    except ImportError:
        # Fallback: save HTML and let browser print to PDF
        html_path = path.replace(".pdf", ".html")
        with open(html_path, "w") as f:
            f.write(html)
        return html_path


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────
def run_report(run_id: int, template_id: Optional[int], name: str,
               report_type: str, config: dict, generated_by: str):
    """Execute a report and save files. Updates report_runs table."""
    db = _db()
    db.execute("UPDATE report_runs SET status='generating' WHERE id=?", (run_id,))
    db.commit(); db.close()
    try:
        fetcher = FETCHERS.get(report_type)
        if not fetcher:
            raise ValueError(f"Unknown report type: {report_type}")
        cols, rows = fetcher(config)

        ts      = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe    = "".join(c if c.isalnum() else "_" for c in name)
        csv_fn  = f"{safe}_{ts}.csv"
        pdf_fn  = f"{safe}_{ts}.pdf"

        # CSV
        csv_data = generate_csv(cols, rows)
        csv_path = os.path.join(REPORT_DIR, csv_fn)
        with open(csv_path, "w", newline="") as f:
            f.write(csv_data)

        # HTML → PDF
        html = generate_html(name, report_type, cols, rows)
        pdf_path = generate_pdf(html, pdf_fn)

        db2 = _db()
        db2.execute(
            """UPDATE report_runs
               SET status='ready', file_csv=?, file_pdf=?, row_count=?, completed_at=?
               WHERE id=?""",
            (csv_fn, os.path.basename(pdf_path), len(rows), _now(), run_id)
        )
        db2.commit(); db2.close()
        return {"cols": cols, "rows": rows, "csv": csv_fn, "pdf": os.path.basename(pdf_path)}
    except Exception as e:
        log.exception("Report run %s failed", run_id)
        db3 = _db()
        db3.execute("UPDATE report_runs SET status='failed', error=? WHERE id=?", (str(e), run_id))
        db3.commit(); db3.close()
        raise
