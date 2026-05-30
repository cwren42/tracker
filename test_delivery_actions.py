#!/usr/bin/env python3
"""Check what DeliveryAction values exist in EmailEvents over 90 days."""
import sys, json, requests
sys.path.insert(0, "/var/www/tracker")
from app import app

with app.app_context():
    from models import AzureIntegrationConfig
    from quarantine_service import QuarantineService
    cfg = AzureIntegrationConfig.query.filter_by(enabled=True).first()
    svc = QuarantineService(cfg.tenant_id, cfg.client_id, cfg.client_secret)
    token = svc._security_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.security.microsoft.com/api/advancedhunting/run"

    # Distribution of DeliveryAction values in last 90 days
    kql = "EmailEvents | where Timestamp >= ago(90d) | summarize count() by DeliveryAction | order by count_ desc"
    r = requests.post(url, json={"Query": kql}, headers=headers, timeout=30)
    print(f"HTTP {r.status_code}")
    print("DeliveryAction distribution (90 days):")
    for row in r.json().get("Results", []):
        print(f"  '{row.get('DeliveryAction','?')}'  ->  {row.get('count_',0)}")

    # Also check LatestDeliveryAction
    kql2 = "EmailEvents | where Timestamp >= ago(90d) | summarize count() by LatestDeliveryAction | order by count_ desc"
    r2 = requests.post(url, json={"Query": kql2}, headers=headers, timeout=30)
    print("\nLatestDeliveryAction distribution (90 days):")
    for row in r2.json().get("Results", []):
        print(f"  '{row.get('LatestDeliveryAction','?')}'  ->  {row.get('count_',0)}")
