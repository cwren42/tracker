"""Recompute SOC 2 control automation flags and progress from linked evidence."""
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from soc2_models import SOC2Control, sync_control_automation_flags, sync_control_progress_flags


def main():
    with app.app_context():
        updated_controls = sync_control_automation_flags(db.session)
        updated_progress = sync_control_progress_flags(db.session)
        db.session.commit()

        total_controls = SOC2Control.query.count()
        automated_controls = SOC2Control.query.filter_by(automation_enabled=True).count()
        in_place_controls = SOC2Control.query.filter_by(control_progress='In Place').count()

        print(f"✓ Recomputed automation flags for {len(updated_controls)} controls")
        print(f"✓ Recomputed progress for {len(updated_progress)} controls")
        print(f"✓ Automated controls: {automated_controls}/{total_controls}")
        print(f"✓ In Place controls: {in_place_controls}/{total_controls}")

        if updated_controls:
            print("  Updated controls:")
            for name in updated_controls:
                print(f"    - {name}")

        if updated_progress:
            print("  Progress updated:")
            for name in updated_progress:
                print(f"    - {name}")


if __name__ == '__main__':
    main()