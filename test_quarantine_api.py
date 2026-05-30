#!/usr/bin/env python3
"""
Direct test of Defender Advanced Hunting for quarantined mail.
Run with: set -a && source /var/www/tracker/.secrets.env && set +a && /var/www/tracker/.venv/bin/python /var/www/tracker/test_quarantine_api.py
"""
import sys, json
sys.path.insert(0, "/var/www/tracker")
from app import app

with app.app_context():
    from models import AzureIntegrationConfig
    cfg = AzureIntegrationConfig.query.filter_by(enabled=True).first()
    if not cfg:
        print("ERROR: No enabled AzureIntegrationConfig row found in DB.")
        sys.exit(1)

    print(f"Using tenant: {cfg.tenant_id}")
    print(f"Client ID:    {cfg.client_id}")
    print()

    from quarantine_service import QuarantineService
    svc = QuarantineService(cfg.tenant_id, cfg.client_id, cfg.client_secret)

    # Test using the actual service method (uses the fixed KQL)
    print("=== Testing get_quarantine_messages_via_hunting(days=30) ===")
    messages = svc.get_quarantine_messages_via_hunting(days=30)
    print(f"Messages returned: {len(messages)}")
    if messages:
        m = messages[0]
        print(f"First: sender={m.get('sender_address')} subject={m.get('subject','')[:60]}")
        print(f"       threat={m.get('threat_type')} latest_action={m.get('latest_delivery_action','?')}")

    # Also try 90 days
    print()
    print("=== Testing get_quarantine_messages_via_hunting(days=90) ===")
    messages90 = svc.get_quarantine_messages_via_hunting(days=90)
    print(f"Messages returned (90d): {len(messages90)}")
