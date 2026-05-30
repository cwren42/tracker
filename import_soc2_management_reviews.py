"""Seed a baseline SOC 2 management review record for the Type 1 audit window."""
import sys
from datetime import date

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from models import SOC2ManagementReview, SOC2ManagementReviewAction


def main():
    with app.app_context():
        try:
            review = SOC2ManagementReview.query.filter_by(review_key='MR-2026-Q2-001').first()
            created = review is None
            if created:
                review = SOC2ManagementReview(review_key='MR-2026-Q2-001')
                db.session.add(review)

            review.title = 'Q2 2026 SOC 2 Type 1 Management Review'
            review.review_date = date(2026, 6, 20)
            review.review_period_start = date(2026, 4, 1)
            review.review_period_end = date(2026, 6, 30)
            review.chairperson = 'Executive Committee'
            review.minute_taker = 'Chris Wren'
            review.location = 'Main Conference Room / Virtual'
            review.status = 'Planned'
            review.attendees = 'Executive Committee; Chris Wren; Brenda Milian; relevant department heads'
            review.agenda_summary = 'Review Q2 audit readiness, internal audit status, risk treatments, vendor posture, and resource adequacy.'
            review.decisions_summary = 'To be completed during the management review meeting.'
            review.effectiveness_summary = 'Pending formal review.'
            review.resource_summary = 'Pending management decisions on audit preparation resources.'
            review.evidence_reference = 'Management review package to be attached after meeting completion.'
            db.session.flush()

            if created and not review.actions:
                db.session.add(SOC2ManagementReviewAction(
                    review_id=review.id,
                    action_key='MRA-2026-0001',
                    title='Finalize management review materials for SOC 2 Type 1 readiness discussion',
                    owner='chris.wren@cirque.com',
                    due_date=date(2026, 6, 15),
                    status='Open',
                    notes='Compile readiness queue, internal audit summary, vendor review status, and resource requests.',
                ))

            db.session.commit()
            print(f"✓ Seeded SOC 2 management reviews ({SOC2ManagementReview.query.count()} total)")
            return True
        except Exception as exc:
            db.session.rollback()
            print(f"Error: {exc}")
            return False


if __name__ == '__main__':
    if not main():
        sys.exit(1)