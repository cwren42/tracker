#!/usr/bin/env python3
"""
Load StrikeGraph SOC2 controls from CSV and replace existing controls
"""
import csv
import sys
import os
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from soc2_models import SOC2Control

def load_controls(csv_path):
    """Load StrikeGraph controls from CSV"""
    
    with app.app_context():
        print("Loading StrikeGraph controls...")
        
        # Clear existing controls (this will cascade delete evidence snapshots due to relationship)
        print("Clearing existing controls...")
        SOC2Control.query.delete()
        db.session.commit()
        
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            count = 0
            
            for row in reader:
                control = SOC2Control(
                    control_name=row['Control Name'],
                    control_description=row['Control Description'],
                    control_frequency=row['Control Frequency'],
                    control_owner=row['Control Owner'] if row['Control Owner'] else None,
                    control_progress=row['Control Progress'],
                    is_active=row['Inactive/Active'] == 'TRUE',
                    audit_alignment=row['Audit Alignment'],
                    automation_enabled=False  # Will update based on evidence mappings
                )
                
                db.session.add(control)
                count += 1
                
                if count % 10 == 0:
                    print(f"  Loaded {count} controls...")
            
            db.session.commit()
            print(f"\n✓ Successfully loaded {count} controls")
            
            # Show statistics
            print("\nStatistics:")
            total = SOC2Control.query.count()
            active = SOC2Control.query.filter_by(is_active=True).count()
            
            print(f"  Total controls: {total}")
            print(f"  Active controls: {active}")
            
            # By progress
            print("\nBy Progress:")
            for progress in ['In Place', 'Partially In Place', 'Not In Place']:
                count = SOC2Control.query.filter_by(control_progress=progress).count()
                print(f"  {progress}: {count}")
            
            # By frequency
            print("\nBy Frequency:")
            for freq in ['Continuous', 'Daily', 'Weekly', 'Monthly', 'Quarterly', 'Annually', 'As Needed']:
                count = SOC2Control.query.filter_by(control_frequency=freq).count()
                if count > 0:
                    print(f"  {freq}: {count}")
            
            # By owner
            print("\nBy Owner:")
            from sqlalchemy import func
            owners = db.session.query(SOC2Control.control_owner, func.count(SOC2Control.id)).group_by(SOC2Control.control_owner).all()
            for owner, count in owners:
                if owner:
                    print(f"  {owner}: {count}")
                else:
                    print(f"  Unassigned: {count}")

def main():
    csv_path = "/home/webuser/cirque_corporation-controls-1-9-2026-sg (1).csv"
    
    if not os.path.exists(csv_path):
        print(f"Error: File not found: {csv_path}")
        return
    
    load_controls(csv_path)
    print("\n✓ StrikeGraph controls loaded successfully")
    print("\nNEXT: Run load_strikegraph_evidence.py to re-map evidence to new controls")

if __name__ == '__main__':
    main()
