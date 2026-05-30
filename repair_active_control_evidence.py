"""Repair active SOC 2 evidence rows by deduping and relinking/regenerating artifacts."""
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from blueprints.soc2 import _find_cached_evidence_file, _resolve_evidence_file_path
from evidence_file_service import EvidenceFileService
from soc2_models import SOC2Control, StrikeGraphEvidence


STALE_ACTIVE_EVIDENCE = {
    (8, 'Backup Policy'),
    (22, 'Record Retention Schedule'),
    (50, 'Access Removal Procedures/Checklist'),
}


def _pick_canonical(items):
    def sort_key(item):
        resolved = _resolve_evidence_file_path(item.file_path)
        cached = _find_cached_evidence_file(item)
        return (
            1 if resolved else 0,
            1 if cached else 0,
            1 if item.submission_status == 'Submitted' else 0,
            -(item.id or 0),
        )

    return max(items, key=sort_key)


def repair_active_control_evidence():
    service = EvidenceFileService()
    active_ids = [control.id for control in SOC2Control.query.filter_by(is_active=True).all()]
    evidence_rows = StrikeGraphEvidence.query.filter(StrikeGraphEvidence.control_id.in_(active_ids)).order_by(
        StrikeGraphEvidence.control_id,
        StrikeGraphEvidence.evidence_name,
        StrikeGraphEvidence.id,
    ).all()

    grouped = defaultdict(list)
    for row in evidence_rows:
        grouped[(row.control_id, row.evidence_name.strip().lower())].append(row)

    repaired = 0
    generated = 0
    deduped = 0

    for items in grouped.values():
        canonical = _pick_canonical(items)

        if (canonical.control_id, canonical.evidence_name) in STALE_ACTIVE_EVIDENCE:
            db.session.delete(canonical)
            deduped += 1
            continue

        for duplicate in items:
            if duplicate.id == canonical.id:
                continue
            db.session.delete(duplicate)
            deduped += 1

        resolved_path = _resolve_evidence_file_path(canonical.file_path)
        if resolved_path:
            if canonical.file_path != resolved_path:
                canonical.file_path = resolved_path
                canonical.updated_at = datetime.utcnow()
                repaired += 1
            continue

        cached_path = _find_cached_evidence_file(canonical)
        if cached_path and os.path.exists(cached_path):
            canonical.file_path = cached_path
            canonical.updated_at = datetime.utcnow()
            repaired += 1
            continue

        if canonical.automation_source == 'ISMS':
            generated_path = service.generate_evidence_file_by_name(canonical.evidence_name)
            if generated_path and os.path.exists(generated_path):
                canonical.file_path = generated_path
                canonical.updated_at = datetime.utcnow()
                generated += 1

    return {
        'repaired': repaired,
        'generated': generated,
        'deduped': deduped,
    }


def main():
    with app.app_context():
        result = repair_active_control_evidence()
        db.session.commit()
        print(f"✓ Repaired {result['repaired']} active evidence paths")
        print(f"✓ Generated {result['generated']} missing ISMS artifacts")
        print(f"✓ Removed {result['deduped']} duplicate evidence rows")


if __name__ == '__main__':
    main()