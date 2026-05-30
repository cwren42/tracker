from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from extensions import db
from models import Employee, SOC2PolicyAcknowledgement, _log_audit
from utils import admin_required


bp = Blueprint('policy_acknowledgements', __name__)


STATUS_OPTIONS = ['Acknowledged', 'Pending', 'Superseded', 'Revoked']
TYPE_OPTIONS = ['Security Policy', 'Code of Conduct', 'Acceptable Use', 'Remote Work', 'Physical Access', 'Contractor Security']


def _parse_date(value):
    value = (value or '').strip()
    return datetime.strptime(value, '%Y-%m-%d').date() if value else None


def _apply_form(record, form):
    employee_value = (form.get('employee_id') or '').strip()
    employee = db.session.get(Employee, int(employee_value)) if employee_value else None

    record.employee_id = employee.id if employee else None
    record.person_name = (form.get('person_name') or '').strip() or (employee.name if employee else '')
    record.person_email = (form.get('person_email') or '').strip() or (employee.email if employee else None)
    record.department = (form.get('department') or '').strip() or (employee.department if employee else None)
    record.acknowledgement_type = (form.get('acknowledgement_type') or 'Security Policy').strip()
    record.policy_name = (form.get('policy_name') or '').strip()
    record.policy_version = (form.get('policy_version') or '').strip() or None
    record.acknowledged_on = _parse_date(form.get('acknowledged_on'))
    record.status = (form.get('status') or 'Acknowledged').strip()
    record.evidence_reference = (form.get('evidence_reference') or '').strip() or None
    record.notes = (form.get('notes') or '').strip() or None


@bp.route('/soc2/policy-acknowledgements')
@login_required
@admin_required
def acknowledgements_dashboard():
    records = SOC2PolicyAcknowledgement.query.order_by(
        SOC2PolicyAcknowledgement.acknowledged_on.desc(),
        SOC2PolicyAcknowledgement.id.desc(),
    ).all()
    employees = Employee.query.order_by(Employee.name.asc()).all()
    return render_template(
        'soc2_policy_acknowledgements_dashboard.html',
        records=records,
        employees=employees,
        status_options=STATUS_OPTIONS,
        type_options=TYPE_OPTIONS,
        summary={
            'records': len(records),
            'acknowledged': sum(1 for record in records if record.status == 'Acknowledged'),
            'pending': sum(1 for record in records if record.status == 'Pending'),
        },
    )


@bp.route('/soc2/policy-acknowledgements/new', methods=['POST'])
@login_required
@admin_required
def create_acknowledgement():
    next_number = SOC2PolicyAcknowledgement.query.count() + 1
    record = SOC2PolicyAcknowledgement(acknowledgement_key=f'ACK-{datetime.utcnow().strftime("%Y")}-{next_number:04d}')
    _apply_form(record, request.form)
    if not record.person_name or not record.policy_name or not record.acknowledged_on:
        flash('Person, policy name, and acknowledgement date are required.', 'danger')
        return redirect(url_for('policy_acknowledgements.acknowledgements_dashboard'))

    db.session.add(record)
    db.session.flush()
    _log_audit('soc2_policy_acknowledgement', record.id, 'create', {'acknowledgement_key': record.acknowledgement_key, 'policy_name': record.policy_name})
    db.session.commit()
    flash('Policy acknowledgement recorded.', 'success')
    return redirect(url_for('policy_acknowledgements.acknowledgement_detail', acknowledgement_id=record.id))


@bp.route('/soc2/policy-acknowledgements/<int:acknowledgement_id>')
@login_required
@admin_required
def acknowledgement_detail(acknowledgement_id):
    record = SOC2PolicyAcknowledgement.query.get_or_404(acknowledgement_id)
    employees = Employee.query.order_by(Employee.name.asc()).all()
    return render_template(
        'soc2_policy_acknowledgement_detail.html',
        record=record,
        employees=employees,
        status_options=STATUS_OPTIONS,
        type_options=TYPE_OPTIONS,
    )


@bp.route('/soc2/policy-acknowledgements/<int:acknowledgement_id>/update', methods=['POST'])
@login_required
@admin_required
def update_acknowledgement(acknowledgement_id):
    record = SOC2PolicyAcknowledgement.query.get_or_404(acknowledgement_id)
    _apply_form(record, request.form)
    if not record.person_name or not record.policy_name or not record.acknowledged_on:
        flash('Person, policy name, and acknowledgement date are required.', 'danger')
        return redirect(url_for('policy_acknowledgements.acknowledgement_detail', acknowledgement_id=record.id))

    _log_audit('soc2_policy_acknowledgement', record.id, 'update', {'acknowledgement_key': record.acknowledgement_key, 'policy_name': record.policy_name})
    db.session.commit()
    flash('Policy acknowledgement updated.', 'success')
    return redirect(url_for('policy_acknowledgements.acknowledgement_detail', acknowledgement_id=record.id))