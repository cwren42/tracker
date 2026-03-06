#!/usr/bin/env python3
"""
Add Security Configuration Standards Policy to policy database
"""

import sqlite3
from datetime import datetime

# Connect to database
conn = sqlite3.connect('/var/www/tracker/assets.db')
cursor = conn.cursor()

# Read the policy content
with open('/var/www/tracker/static/evidence/manual/IS-CIRQ-P-044-G_Security_Configuration_Standards_20260302.md', 'r') as f:
    policy_content = f.read()

# Policy metadata
policy_data = {
    'document_id': 'IS-CIRQ-P-044-G',
    'title': 'Security Configuration Standards',
    'category': 'Policy',
    'division': 'Information Technology',
    'standard_type': 'General',
    'version': '1.0',
    'effective_date': '2026-03-02',
    'review_date': '2027-03-02',
    'approved_by': 'CEO',
    'content': policy_content,
    'created_at': datetime.now().isoformat(),
    'updated_at': datetime.now().isoformat()
}

# Insert policy
cursor.execute('''
    INSERT INTO policy (
        document_id, title, category, division, standard_type, version, 
        effective_date, review_date, approved_by, content, created_at, updated_at
    ) VALUES (
        :document_id, :title, :category, :division, :standard_type, :version,
        :effective_date, :review_date, :approved_by, :content, :created_at, :updated_at
    )
''', policy_data)

policy_id = cursor.lastrowid

print(f"✅ Policy created: ID {policy_id}")
print(f"   Document ID: {policy_data['document_id']}")
print(f"   Title: {policy_data['title']}")

# Map to Configuration Standards control (ID 17)
control_id = 17
cursor.execute('''
    INSERT INTO policy_control_mapping (policy_id, control_id)
    VALUES (?, ?)
''', (policy_id, control_id))

print(f"✅ Mapped to Control #{control_id}: Configuration Standards")

conn.commit()
conn.close()

print("\n✅ Security Configuration Standards Policy added successfully!")
