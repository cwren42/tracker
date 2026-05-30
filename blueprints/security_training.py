from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from extensions import db
from models import Employee, SOC2SecurityTrainingRecord, _log_audit
from soc2_artifact_service import QUALIFYING_TRAINING_STATUSES, get_training_completion_snapshot
from utils import admin_required


bp = Blueprint('security_training', __name__)


STATUS_OPTIONS = ['Completed', 'Passed', 'Attended', 'Scheduled', 'In Progress', 'Overdue', 'Waived']


def _parse_date(value):
    value = (value or '').strip()
    return datetime.strptime(value, '%Y-%m-%d').date() if value else None


def _apply_training_form(record, form):
    employee_value = (form.get('employee_id') or '').strip()
    employee = db.session.get(Employee, int(employee_value)) if employee_value else None

    record.employee_id = employee.id if employee else None
    record.trainee_name = (form.get('trainee_name') or '').strip() or (employee.name if employee else '')
    record.trainee_email = (form.get('trainee_email') or '').strip() or (employee.email if employee else None)
    record.department = (form.get('department') or '').strip() or (employee.department if employee else None)
    record.role_title = (form.get('role_title') or '').strip() or (employee.position if employee else None)
    record.training_date = _parse_date(form.get('training_date'))
    record.training_topic = (form.get('training_topic') or '').strip()
    record.provider_method = (form.get('provider_method') or '').strip() or None
    record.duration = (form.get('duration') or '').strip() or None
    record.completion_status = (form.get('completion_status') or 'Completed').strip()
    score_value = (form.get('score') or '').strip()
    record.score = int(score_value) if score_value else None
    record.notes = (form.get('notes') or '').strip() or None


@bp.route('/soc2/security-training')
@login_required
@admin_required
def security_training_dashboard():
    records = SOC2SecurityTrainingRecord.query.order_by(
        SOC2SecurityTrainingRecord.training_date.desc(),
        SOC2SecurityTrainingRecord.id.desc(),
    ).all()
    employees = Employee.query.order_by(Employee.name.asc()).all()
    snapshot = get_training_completion_snapshot()
    return render_template(
        'soc2_security_training_dashboard.html',
        records=records,
        employees=employees,
        status_options=STATUS_OPTIONS,
        qualifying_statuses=QUALIFYING_TRAINING_STATUSES,
        summary={
            'active_employees': len(snapshot['active_employees']),
            'completed_current_cycle': snapshot['completed_current_cycle'],
            'overdue_employees': len(snapshot['overdue_employees']),
            'records': len(records),
            'cutoff_date': snapshot['cutoff_date'],
            'cadence_label': 'quarter',
        },
        overdue_employees=snapshot['overdue_employees'][:12],
    )


@bp.route('/soc2/security-training/new', methods=['POST'])
@login_required
@admin_required
def create_security_training_record():
    next_number = SOC2SecurityTrainingRecord.query.count() + 1
    record = SOC2SecurityTrainingRecord(record_key=f'TRL-{datetime.utcnow().strftime("%Y")}-{next_number:04d}')
    _apply_training_form(record, request.form)

    if not record.trainee_name or not record.training_date or not record.training_topic:
        flash('Trainee name, training date, and training topic are required.', 'danger')
        return redirect(url_for('security_training.security_training_dashboard'))

    db.session.add(record)
    db.session.flush()
    _log_audit('soc2_security_training_record', record.id, 'create', {'record_key': record.record_key, 'training_topic': record.training_topic})
    db.session.commit()
    flash('Security training record created.', 'success')
    return redirect(url_for('security_training.security_training_record_detail', record_id=record.id))


@bp.route('/soc2/security-training/<int:record_id>')
@login_required
@admin_required
def security_training_record_detail(record_id):
    record = SOC2SecurityTrainingRecord.query.get_or_404(record_id)
    employees = Employee.query.order_by(Employee.name.asc()).all()
    return render_template(
        'soc2_security_training_detail.html',
        record=record,
        employees=employees,
        status_options=STATUS_OPTIONS,
    )


@bp.route('/soc2/security-training/<int:record_id>/update', methods=['POST'])
@login_required
@admin_required
def update_security_training_record(record_id):
    record = SOC2SecurityTrainingRecord.query.get_or_404(record_id)
    _apply_training_form(record, request.form)

    if not record.trainee_name or not record.training_date or not record.training_topic:
        flash('Trainee name, training date, and training topic are required.', 'danger')
        return redirect(url_for('security_training.security_training_record_detail', record_id=record.id))

    _log_audit('soc2_security_training_record', record.id, 'update', {'record_key': record.record_key, 'training_topic': record.training_topic})
    db.session.commit()
    flash('Security training record updated.', 'success')
    return redirect(url_for('security_training.security_training_record_detail', record_id=record.id))