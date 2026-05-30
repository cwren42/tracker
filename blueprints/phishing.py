from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from extensions import db
from models import Employee, SOC2PhishingCampaign, SOC2PhishingResult, _log_audit
from soc2_artifact_service import get_phishing_campaign_snapshot
from utils import admin_required


bp = Blueprint('phishing', __name__)


CAMPAIGN_STATUS_OPTIONS = ['Planned', 'In Progress', 'Completed', 'Closed']
OUTCOME_OPTIONS = ['Completed Follow-up Training', 'Reported Simulation', 'No Click', 'Clicked Link', 'Pending Follow-up']


def _parse_date(value):
    value = (value or '').strip()
    return datetime.strptime(value, '%Y-%m-%d').date() if value else None


@bp.route('/soc2/phishing')
@login_required
@admin_required
def phishing_dashboard():
    snapshot = get_phishing_campaign_snapshot()
    return render_template(
        'soc2_phishing_dashboard.html',
        campaigns=snapshot['campaigns'],
        summary=snapshot,
        campaign_status_options=CAMPAIGN_STATUS_OPTIONS,
    )


@bp.route('/soc2/phishing/new', methods=['POST'])
@login_required
@admin_required
def create_phishing_campaign():
    title = (request.form.get('title') or '').strip()
    campaign_date = _parse_date(request.form.get('campaign_date'))
    if not title or not campaign_date:
        flash('Campaign title and date are required.', 'danger')
        return redirect(url_for('phishing.phishing_dashboard'))

    next_number = SOC2PhishingCampaign.query.count() + 1
    campaign = SOC2PhishingCampaign(
        campaign_key=f'PHISH-{datetime.utcnow().strftime("%Y")}-{next_number:03d}',
        title=title,
        campaign_date=campaign_date,
        provider=(request.form.get('provider') or '').strip() or None,
        scope=(request.form.get('scope') or '').strip() or None,
        status=(request.form.get('status') or 'Planned').strip(),
        scenario=(request.form.get('scenario') or '').strip() or None,
        follow_up_training_topic=(request.form.get('follow_up_training_topic') or '').strip() or None,
        summary=(request.form.get('summary') or '').strip() or None,
        evidence_reference=(request.form.get('evidence_reference') or '').strip() or None,
    )
    db.session.add(campaign)
    db.session.flush()
    _log_audit('soc2_phishing_campaign', campaign.id, 'create', {'campaign_key': campaign.campaign_key, 'title': campaign.title})
    db.session.commit()
    flash('Phishing campaign created.', 'success')
    return redirect(url_for('phishing.phishing_campaign_detail', campaign_id=campaign.id))


@bp.route('/soc2/phishing/<int:campaign_id>')
@login_required
@admin_required
def phishing_campaign_detail(campaign_id):
    campaign = SOC2PhishingCampaign.query.get_or_404(campaign_id)
    employees = Employee.query.order_by(Employee.name.asc()).all()
    return render_template(
        'soc2_phishing_detail.html',
        campaign=campaign,
        employees=employees,
        outcome_options=OUTCOME_OPTIONS,
    )


@bp.route('/soc2/phishing/<int:campaign_id>/results/new', methods=['POST'])
@login_required
@admin_required
def create_phishing_result(campaign_id):
    campaign = SOC2PhishingCampaign.query.get_or_404(campaign_id)
    employee_value = (request.form.get('employee_id') or '').strip()
    employee = db.session.get(Employee, int(employee_value)) if employee_value else None
    employee_name = (request.form.get('employee_name') or '').strip() or (employee.name if employee else '')
    if not employee_name:
        flash('Employee name is required.', 'danger')
        return redirect(url_for('phishing.phishing_campaign_detail', campaign_id=campaign.id))

    next_number = SOC2PhishingResult.query.count() + 1
    result = SOC2PhishingResult(
        campaign_id=campaign.id,
        employee_id=employee.id if employee else None,
        result_key=f'PHR-{datetime.utcnow().strftime("%Y")}-{next_number:04d}',
        employee_name=employee_name,
        employee_email=(request.form.get('employee_email') or '').strip() or (employee.email if employee else None),
        department=(request.form.get('department') or '').strip() or (employee.department if employee else None),
        delivered=request.form.get('delivered') == 'on',
        opened=request.form.get('opened') == 'on',
        clicked=request.form.get('clicked') == 'on',
        reported=request.form.get('reported') == 'on',
        training_completed=request.form.get('training_completed') == 'on',
        training_completed_on=_parse_date(request.form.get('training_completed_on')),
        outcome=(request.form.get('outcome') or 'Pending Follow-up').strip(),
        notes=(request.form.get('notes') or '').strip() or None,
    )
    db.session.add(result)
    db.session.flush()
    _log_audit('soc2_phishing_result', result.id, 'create', {'campaign_id': campaign.id, 'employee_name': result.employee_name})
    db.session.commit()
    flash('Phishing result added.', 'success')
    return redirect(url_for('phishing.phishing_campaign_detail', campaign_id=campaign.id))