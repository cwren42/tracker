from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, func

from extensions import db
from models import (
    SOC2InternalAudit,
    SOC2InternalAuditFinding,
    SOC2ReadinessItem,
    _log_audit,
)
from utils import admin_required


bp = Blueprint('internal_audit', __name__)


AUDIT_STATUS_OPTIONS = ['Planned', 'In Progress', 'Completed', 'Cancelled']
FINDING_STATUS_OPTIONS = ['Open', 'In Progress', 'Closed']
FINDING_SEVERITY_OPTIONS = ['Critical', 'Major', 'Minor', 'Observation']


def _parse_date(value):
    value = (value or '').strip()
    return datetime.strptime(value, '%Y-%m-%d').date() if value else None


@bp.route('/soc2/internal-audits')
@login_required
@admin_required
def internal_audit_dashboard():
    audits = SOC2InternalAudit.query.order_by(
        SOC2InternalAudit.planned_date.desc().nullslast(),
        SOC2InternalAudit.created_at.desc(),
    ).all()
    findings_summary = db.session.query(
        func.count(SOC2InternalAuditFinding.id),
        func.sum(case((SOC2InternalAuditFinding.status != 'Closed', 1), else_=0)),
    ).one()
    linked_readiness_items = SOC2InternalAuditFinding.query.filter(
        SOC2InternalAuditFinding.readiness_item_id.isnot(None)
    ).count()
    readiness_items = SOC2ReadinessItem.query.filter_by(is_active=True).order_by(SOC2ReadinessItem.title.asc()).all()

    return render_template(
        'soc2_internal_audit_dashboard.html',
        audits=audits,
        readiness_items=readiness_items,
        audit_status_options=AUDIT_STATUS_OPTIONS,
        finding_count=findings_summary[0] or 0,
        open_finding_count=findings_summary[1] or 0,
        linked_readiness_items=linked_readiness_items,
    )


@bp.route('/soc2/internal-audits/new', methods=['POST'])
@login_required
@admin_required
def create_internal_audit():
    title = (request.form.get('title') or '').strip()
    if not title:
        flash('Audit title is required.', 'danger')
        return redirect(url_for('internal_audit.internal_audit_dashboard'))

    audit_count = SOC2InternalAudit.query.count() + 1
    audit = SOC2InternalAudit(
        audit_key=f'IA-{datetime.utcnow().strftime("%Y")}-{audit_count:03d}',
        title=title,
        scope=(request.form.get('scope') or '').strip() or None,
        status=(request.form.get('status') or 'Planned').strip(),
        owner=(request.form.get('owner') or '').strip() or None,
        audit_period_start=_parse_date(request.form.get('audit_period_start')),
        audit_period_end=_parse_date(request.form.get('audit_period_end')),
        planned_date=_parse_date(request.form.get('planned_date')),
        performed_date=_parse_date(request.form.get('performed_date')),
        summary=(request.form.get('summary') or '').strip() or None,
        evidence_reference=(request.form.get('evidence_reference') or '').strip() or None,
    )
    db.session.add(audit)
    db.session.flush()
    _log_audit('soc2_internal_audit', audit.id, 'create', {'audit_key': audit.audit_key, 'title': audit.title})
    db.session.commit()
    flash('Internal audit created.', 'success')
    return redirect(url_for('internal_audit.internal_audit_detail', audit_id=audit.id))


@bp.route('/soc2/internal-audits/<int:audit_id>')
@login_required
@admin_required
def internal_audit_detail(audit_id):
    audit = SOC2InternalAudit.query.get_or_404(audit_id)
    readiness_items = SOC2ReadinessItem.query.filter_by(is_active=True).order_by(SOC2ReadinessItem.title.asc()).all()
    return render_template(
        'soc2_internal_audit_detail.html',
        audit=audit,
        readiness_items=readiness_items,
        finding_status_options=FINDING_STATUS_OPTIONS,
        finding_severity_options=FINDING_SEVERITY_OPTIONS,
    )


@bp.route('/soc2/internal-audits/<int:audit_id>/findings/new', methods=['POST'])
@login_required
@admin_required
def create_internal_audit_finding(audit_id):
    audit = SOC2InternalAudit.query.get_or_404(audit_id)
    title = (request.form.get('title') or '').strip()
    if not title:
        flash('Finding title is required.', 'danger')
        return redirect(url_for('internal_audit.internal_audit_detail', audit_id=audit.id))

    finding_count = SOC2InternalAuditFinding.query.count() + 1
    readiness_item_id = (request.form.get('readiness_item_id') or '').strip()
    finding = SOC2InternalAuditFinding(
        audit_id=audit.id,
        readiness_item_id=int(readiness_item_id) if readiness_item_id else None,
        finding_key=f'F-{datetime.utcnow().strftime("%Y")}-{finding_count:04d}',
        title=title,
        severity=(request.form.get('severity') or 'Minor').strip(),
        status=(request.form.get('status') or 'Open').strip(),
        criteria_reference=(request.form.get('criteria_reference') or '').strip() or None,
        owner=(request.form.get('owner') or '').strip() or None,
        due_date=_parse_date(request.form.get('due_date')),
        description=(request.form.get('description') or '').strip() or None,
        recommendation=(request.form.get('recommendation') or '').strip() or None,
        evidence_reference=(request.form.get('evidence_reference') or '').strip() or None,
    )
    db.session.add(finding)

    if finding.readiness_item_id:
        readiness_item = SOC2ReadinessItem.query.get(finding.readiness_item_id)
        if readiness_item and readiness_item.status in {'Closed', 'In Place'}:
            readiness_item.status = 'Partially In Place'
        if readiness_item and not readiness_item.next_step:
            readiness_item.next_step = 'Review linked internal audit finding and address remediation.'

    _log_audit('soc2_internal_audit_finding', audit.id, 'create', {
        'audit_id': audit.id,
        'title': title,
        'linked_readiness_item_id': finding.readiness_item_id,
    })
    db.session.commit()
    flash('Audit finding added.', 'success')
    return redirect(url_for('internal_audit.internal_audit_detail', audit_id=audit.id))