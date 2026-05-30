"""Delete inactive SOC 2 controls and their linked evidence artifacts."""
import sys

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from soc2_models import SOC2Control, StrikeGraphEvidence, EvidenceSnapshot


def cleanup_inactive_controls():
    inactive_controls = SOC2Control.query.filter_by(is_active=False).all()
    deleted = {
        'controls': 0,
        'evidence': 0,
        'snapshots': 0,
    }

    for control in inactive_controls:
        evidence_items = StrikeGraphEvidence.query.filter_by(control_id=control.id).all()
        snapshots = EvidenceSnapshot.query.filter_by(control_id=control.id).all()

        for evidence in evidence_items:
            db.session.delete(evidence)
            deleted['evidence'] += 1

        for snapshot in snapshots:
            db.session.delete(snapshot)
            deleted['snapshots'] += 1

        db.session.delete(control)
        deleted['controls'] += 1

    return deleted


def main():
    with app.app_context():
        deleted = cleanup_inactive_controls()
        db.session.commit()
        print(f"✓ Deleted {deleted['controls']} inactive SOC2 controls")
        print(f"✓ Deleted {deleted['evidence']} linked evidence records")
        print(f"✓ Deleted {deleted['snapshots']} linked evidence snapshots")


if __name__ == '__main__':
    main()