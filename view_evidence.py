"""
View all collected SOC2 evidence
"""
from app import app, db
from soc2_models import SOC2Control, EvidenceSnapshot, M365User, IntuneDevice, AdminRoleSnapshot
from datetime import datetime
import json

def view_all_evidence():
    """Display comprehensive view of all collected evidence"""
    
    with app.app_context():
        print("=" * 80)
        print("SOC2 EVIDENCE COLLECTION SUMMARY")
        print("=" * 80)
        
        # 1. M365 Users
        print("\n📊 MICROSOFT 365 USERS")
        print("-" * 80)
        total_users = M365User.query.filter_by(is_current=True).count()
        admin_users = M365User.query.filter_by(is_current=True, is_admin=True).count()
        print(f"Total Users: {total_users}")
        print(f"Admin Users: {admin_users}")
        
        print("\n👥 Sample Admin Users:")
        admins = M365User.query.filter_by(is_current=True, is_admin=True).limit(5).all()
        for admin in admins:
            roles = json.loads(admin.admin_roles) if admin.admin_roles else []
            print(f"  • {admin.display_name} ({admin.user_principal_name})")
            print(f"    Roles: {', '.join(roles)}")
        
        # 2. Intune Devices
        print("\n💻 INTUNE MANAGED DEVICES")
        print("-" * 80)
        total_devices = IntuneDevice.query.filter_by(is_current=True).count()
        compliant = IntuneDevice.query.filter_by(is_current=True, compliance_state='compliant').count()
        non_compliant = IntuneDevice.query.filter_by(is_current=True, compliance_state='noncompliant').count()
        print(f"Total Devices: {total_devices}")
        print(f"Compliant: {compliant} ({compliant/total_devices*100:.1f}%)")
        print(f"Non-Compliant: {non_compliant}")
        
        print("\n💻 Sample Devices:")
        devices = IntuneDevice.query.filter_by(is_current=True).limit(5).all()
        for device in devices:
            print(f"  • {device.device_name}")
            print(f"    OS: {device.os_version}, Compliance: {device.compliance_state}")
            print(f"    User: {device.user_display_name}")
        
        # 3. Evidence Snapshots
        print("\n📸 EVIDENCE SNAPSHOTS")
        print("-" * 80)
        snapshots = EvidenceSnapshot.query.order_by(EvidenceSnapshot.snapshot_date.desc()).all()
        print(f"Total Snapshots: {len(snapshots)}")
        
        # Group by control
        from sqlalchemy import func
        snapshot_by_control = db.session.query(
            SOC2Control.control_name,
            func.count(EvidenceSnapshot.id).label('count'),
            func.max(EvidenceSnapshot.snapshot_date).label('latest')
        ).join(
            EvidenceSnapshot, SOC2Control.id == EvidenceSnapshot.control_id
        ).group_by(SOC2Control.control_name).all()
        
        print("\n📋 Snapshots by Control:")
        for control_name, count, latest in snapshot_by_control:
            print(f"  • {control_name}: {count} snapshots")
            print(f"    Latest: {latest.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 4. Recent Evidence Details
        print("\n🔍 RECENT EVIDENCE DETAILS")
        print("-" * 80)
        recent = EvidenceSnapshot.query.order_by(EvidenceSnapshot.snapshot_date.desc()).limit(5).all()
        
        for snap in recent:
            control = SOC2Control.query.get(snap.control_id)
            print(f"\n  📄 {control.control_name}")
            print(f"     Date: {snap.snapshot_date.strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"     Type: {snap.evidence_type}")
            print(f"     Records: {snap.record_count}")
            print(f"     Status: {snap.status}")
            
            # Parse and show evidence data
            try:
                data = json.loads(snap.evidence_data)
                print(f"     Data: {json.dumps(data, indent=6)}")
            except:
                pass
        
        # 5. Admin Role History
        print("\n🔐 ADMIN ROLE SNAPSHOTS")
        print("-" * 80)
        admin_snaps = AdminRoleSnapshot.query.filter_by(status='active').all()
        print(f"Total Active Admin Roles: {len(admin_snaps)}")
        
        # Group by role
        from collections import defaultdict
        roles_by_type = defaultdict(list)
        for snap in admin_snaps:
            roles_by_type[snap.role_name].append(snap.user_principal_name)
        
        print("\n👑 Roles Breakdown:")
        for role_name, users in roles_by_type.items():
            print(f"  • {role_name}: {len(users)} users")
            for user in users[:3]:  # Show first 3
                print(f"    - {user}")
            if len(users) > 3:
                print(f"    ... and {len(users) - 3} more")
        
        # 6. Controls Status
        print("\n📋 CONTROLS STATUS")
        print("-" * 80)
        controls = SOC2Control.query.all()
        
        in_place = [c for c in controls if c.control_progress == 'In Place']
        partial = [c for c in controls if c.control_progress == 'Partially In Place']
        not_in_place = [c for c in controls if c.control_progress == 'Not In Place']
        automated = [c for c in controls if c.automation_enabled]
        
        print(f"Total Controls: {len(controls)}")
        print(f"In Place: {len(in_place)}")
        print(f"Partially In Place: {len(partial)}")
        print(f"Not In Place: {len(not_in_place)}")
        print(f"Automated: {len(automated)}")
        
        print("\n✅ Controls with Evidence:")
        for control in controls:
            snap_count = EvidenceSnapshot.query.filter_by(control_id=control.id).count()
            if snap_count > 0:
                latest = EvidenceSnapshot.query.filter_by(control_id=control.id).order_by(EvidenceSnapshot.snapshot_date.desc()).first()
                print(f"  • {control.control_name}")
                print(f"    Snapshots: {snap_count}, Latest: {latest.snapshot_date.strftime('%Y-%m-%d %H:%M')}")
        
        print("\n" + "=" * 80)
        print("✅ EVIDENCE COLLECTION COMPLETE")
        print("=" * 80)
        
        print("\nTo view in dashboard:")
        print("  1. Restart tracker service: sudo systemctl restart tracker")
        print("  2. Login as admin")
        print("  3. Click 'SOC2' in navigation menu")
        print("  4. View controls, evidence, and export reports")
        
        print("\nDirect database queries:")
        print("  sqlite3 assets.db 'SELECT * FROM m365_user WHERE is_admin=1 LIMIT 5;'")
        print("  sqlite3 assets.db 'SELECT * FROM intune_device WHERE is_current=1 LIMIT 5;'")
        print("  sqlite3 assets.db 'SELECT * FROM evidence_snapshot ORDER BY snapshot_date DESC;'")

if __name__ == '__main__':
    view_all_evidence()
