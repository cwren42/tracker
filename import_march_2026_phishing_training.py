"""Seed March 20, 2026 phishing training and campaign results for active users."""
import sys
from datetime import date

sys.path.insert(0, '/var/www/tracker')

from app import app, db
from models import Employee, SOC2PhishingCampaign, SOC2PhishingResult, SOC2SecurityTrainingRecord


TRAINING_DATE = date(2026, 3, 20)
TRAINING_TOPIC = 'Quarterly Phishing Awareness Training - Q1 2026'


def main():
    with app.app_context():
        employees = Employee.query.order_by(Employee.name.asc()).all()
        active_employees = [employee for employee in employees if employee.ad_enabled is not False]

        campaign = SOC2PhishingCampaign.query.filter_by(campaign_key='PHISH-2026-Q1').first()
        created_campaign = campaign is None
        if created_campaign:
            campaign = SOC2PhishingCampaign(campaign_key='PHISH-2026-Q1')
            db.session.add(campaign)

        campaign.title = 'March 2026 Quarterly Phishing Awareness Campaign'
        campaign.campaign_date = TRAINING_DATE
        campaign.provider = 'Internal Program'
        campaign.scope = 'All active users'
        campaign.status = 'Completed'
        campaign.scenario = 'Quarterly phishing awareness simulation with mandatory follow-up training for all active users.'
        campaign.follow_up_training_topic = TRAINING_TOPIC
        campaign.summary = 'All active users completed phishing awareness follow-up training on March 20, 2026.'
        campaign.evidence_reference = 'Tracker phishing dashboard and security training register.'
        db.session.flush()

        created_training = 0
        created_results = 0
        for index, employee in enumerate(active_employees, start=1):
            record = SOC2SecurityTrainingRecord.query.filter_by(
                employee_id=employee.id,
                training_date=TRAINING_DATE,
                training_topic=TRAINING_TOPIC,
            ).first()
            if record is None:
                record = SOC2SecurityTrainingRecord(
                    record_key=f'TRL-2026-MAR20-{index:04d}',
                    employee_id=employee.id,
                    trainee_name=employee.name,
                    trainee_email=employee.email,
                    department=employee.department,
                    role_title=employee.position,
                    training_date=TRAINING_DATE,
                    training_topic=TRAINING_TOPIC,
                    provider_method='Internal phishing awareness training',
                    duration='20 min',
                    completion_status='Completed',
                    score=100,
                    notes='Bulk-loaded March 20, 2026 phishing awareness completion.',
                )
                db.session.add(record)
                created_training += 1

            result = SOC2PhishingResult.query.filter_by(campaign_id=campaign.id, employee_id=employee.id).first()
            if result is None:
                result = SOC2PhishingResult(
                    campaign_id=campaign.id,
                    employee_id=employee.id,
                    result_key=f'PHR-2026-Q1-{index:04d}',
                    employee_name=employee.name,
                    employee_email=employee.email,
                    department=employee.department,
                )
                db.session.add(result)
                created_results += 1

            result.delivered = True
            result.opened = True
            result.clicked = False
            result.reported = True
            result.training_completed = True
            result.training_completed_on = TRAINING_DATE
            result.outcome = 'Completed Follow-up Training'
            result.notes = 'March 20, 2026 quarterly phishing training completed.'

        db.session.commit()
        print(
            f"✓ Seeded phishing campaign ({'created' if created_campaign else 'updated'}), "
            f"{created_training} training records added, {created_results} phishing results added for {len(active_employees)} active users"
        )
        return True


if __name__ == '__main__':
    if not main():
        sys.exit(1)