#!/usr/bin/env python3
import sys
sys.path.insert(0, '/var/www/tracker')
from app import app
with app.app_context():
    from alert_service import sync_defender_vulnerabilities
    from pg_db import pg_connect

    print("Starting Defender sync (this may take 1-2 minutes)...")
    vuln_count, dev_count, err = sync_defender_vulnerabilities()
    if err:
        print(f"ERROR: {err}")
        sys.exit(1)
    print(f"Done: {vuln_count} CVEs, {dev_count} device-CVE pairs synced")

    db = pg_connect()

    # Overall exposure breakdown by product
    rows = db.execute("""
        SELECT product_name,
               COUNT(*) FILTER (WHERE status NOT IN ('Remediated','Closed')) as exposed,
               COUNT(*) FILTER (WHERE status = 'Remediated') as remediated
        FROM device_vulnerability
        WHERE product_name IN ('windows_10','windows_11','chrome','firefox','edge_chromium-based','office')
        GROUP BY product_name
        ORDER BY exposed DESC
    """).fetchall()

    print()
    print(f"{'Product':<30} {'Exposed':>8} {'Remediated':>11}")
    print("-"*52)
    for r in rows:
        print(f"  {r['product_name']:<28} {r['exposed']:>8} {r['remediated']:>11}")

    total_exposed = db.execute("""
        SELECT COUNT(*) FROM device_vulnerability WHERE status NOT IN ('Remediated','Closed')
    """).fetchone()[0]
    total_remediated = db.execute("""
        SELECT COUNT(*) FROM device_vulnerability WHERE status = 'Remediated'
    """).fetchone()[0]
    print()
    print(f"Total exposed (all products): {total_exposed}")
    print(f"Total remediated (all products): {total_remediated}")
    db.close()
