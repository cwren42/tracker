import json
import os
import re
import secrets
import subprocess
import threading
import time as _time
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, abort, current_app, flash, g, jsonify,
                   redirect, render_template, request, send_file, session,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import func, or_, text

from extensions import db, limiter
from models import (
    AuditTrail, Asset, AssetHistory, Control, CustomReport, DashboardWidget,
    Employee, License, LicenseAssignment, LicenseInfo, MaintenanceWindow,
    MonitoringAlert, MonitoringCheck, MonitoringProfile, Policy, PolicySection,
    ProxmoxBackupJob, ProxmoxZfsPool, RemoteSession, Risk, Setting,
    SupportTicket, TicketActivity, TicketNote, User, now_mst, allowed_file,
    SystemDescription, AzureIntegrationConfig, ControlRiskMapping,
)
from soc2_models import SOC2Control, EvidenceSnapshot
import logging
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
)
logger = logging.getLogger(__name__)
from api_system import require_api_key


bp = Blueprint('tickets', __name__)


# ==================== SUPPORT TICKETS ====================

# ─── Ticket SLA Escalation Background Thread ─────────────────────────────────



@bp.route('/tickets')
@login_required
@ticket_access_required
@license_required
def tickets():
    from collections import defaultdict
    from types import SimpleNamespace
    # Base users, eagle_eyes, and viewers only see their own submitted tickets
    if current_user.role in ('base_user', 'eagle_eyes', 'viewer'):
        tickets = SupportTicket.query.filter_by(
            created_by_user_id=current_user.id
        ).order_by(SupportTicket.created_at.desc()).all()
        return render_template('tickets.html', tickets=tickets,
                               total_closed=sum(1 for t in tickets if t.status == 'Closed'),
                               total_open=sum(1 for t in tickets if t.status in ('Open', 'In Progress')),
                               open_count=sum(1 for t in tickets if t.status == 'Open'),
                               inprog_count=sum(1 for t in tickets if t.status == 'In Progress'),
                               urgent_count=0, unassigned_count=0, closed_today=0,
                               chart_labels=[], chart_data=[], tech_loads=[],
                               avg_hours=None, resolution_by_priority=[],
                               base_user_mode=True, now=datetime.utcnow())
    tickets = SupportTicket.query.order_by(SupportTicket.created_at.desc()).all()
    total_closed = SupportTicket.query.filter_by(status='Closed').count()
    total_open = SupportTicket.query.filter(SupportTicket.status.in_(['Open', 'In Progress'])).count()

    # Chart: tickets created per day for last 30 days
    today = datetime.utcnow().date()
    chart_labels = [
        (today - timedelta(days=29 - i)).strftime('%Y-%m-%d') for i in range(30)
    ]
    label_set = set(chart_labels)
    counts_by_date = defaultdict(int)
    for t in tickets:
        if t.created_at:
            d = t.created_at.date() if hasattr(t.created_at, 'date') else t.created_at
            key = d.strftime('%Y-%m-%d')
            if key in label_set:
                counts_by_date[key] += 1
    chart_data = [counts_by_date[d] for d in chart_labels]

    # Technician workload
    users = User.query.filter(User.role.in_(['admin', 'manager', 'viewer', 'eagle_eyes'])).order_by(User.full_name).all()
    user_map = {u.id: u for u in users}
    tech_stats = defaultdict(lambda: {'open': 0, 'in_progress': 0, 'closed': 0, 'res_hours': []})
    for t in tickets:
        if t.assigned_to_user_id and t.assigned_to_user_id in user_map:
            s = tech_stats[t.assigned_to_user_id]
            if t.status == 'Open':
                s['open'] += 1
            elif t.status == 'In Progress':
                s['in_progress'] += 1
            elif t.status in ('Closed', 'Merged'):
                s['closed'] += 1
                if t.created_at and t.updated_at:
                    hours = (t.updated_at - t.created_at).total_seconds() / 3600
                    if hours >= 0:
                        s['res_hours'].append(hours)
    tech_loads = []
    for uid, s in tech_stats.items():
        if uid not in user_map:
            continue
        avg_h = round(sum(s['res_hours']) / len(s['res_hours']), 1) if s['res_hours'] else None
        tech_loads.append(SimpleNamespace(
            user=user_map[uid],
            open=s['open'],
            in_progress=s['in_progress'],
            closed=s['closed'],
            avg_hours=avg_h,
        ))
    tech_loads.sort(key=lambda x: -(x.open + x.in_progress))

    # Summary counts for the stat cards
    open_count       = sum(1 for t in tickets if t.status == 'Open')
    inprog_count     = sum(1 for t in tickets if t.status == 'In Progress')
    urgent_count     = sum(1 for t in tickets if t.status not in ('Closed', 'Merged') and t.priority == 'Urgent')
    unassigned_count = sum(1 for t in tickets if t.status not in ('Closed', 'Merged') and not t.assigned_to_user_id)

    # Closed today
    today_date   = datetime.utcnow().date()
    closed_today = sum(1 for t in tickets if t.status in ('Closed', 'Merged') and t.updated_at
                       and (t.updated_at.date() if hasattr(t.updated_at, 'date') else t.updated_at) == today_date)

    # Overall average resolution hours (all closed tickets)
    all_res_hours = []
    res_by_priority = defaultdict(lambda: {'count': 0, 'hours': []})
    for t in tickets:
        if t.status in ('Closed', 'Merged') and t.created_at and t.updated_at:
            h = (t.updated_at - t.created_at).total_seconds() / 3600
            if h >= 0:
                all_res_hours.append(h)
                res_by_priority[t.priority or 'Normal']['count'] += 1
                res_by_priority[t.priority or 'Normal']['hours'].append(h)
    avg_hours = round(sum(all_res_hours) / len(all_res_hours), 1) if all_res_hours else None

    priority_order = ['Urgent', 'High', 'Normal', 'Low']
    resolution_by_priority = [
        SimpleNamespace(
            priority=p,
            count=res_by_priority[p]['count'],
            avg_hours=round(sum(res_by_priority[p]['hours']) / len(res_by_priority[p]['hours']), 1)
                      if res_by_priority[p]['hours'] else 0
        )
        for p in priority_order if p in res_by_priority
    ]

    return render_template('tickets.html', tickets=tickets, total_closed=total_closed, total_open=total_open,
                           chart_labels=chart_labels, chart_data=chart_data, tech_loads=tech_loads,
                           avg_hours=avg_hours, open_count=open_count, inprog_count=inprog_count,
                           urgent_count=urgent_count, unassigned_count=unassigned_count,
                           closed_today=closed_today, resolution_by_priority=resolution_by_priority,
                           now=datetime.utcnow())


@bp.route('/tickets/new', methods=['GET', 'POST'])
@login_required
@ticket_access_required
@license_required
def new_ticket():
    if request.method == 'POST':
        subject = (request.form.get('subject') or '').strip()
        description = (request.form.get('description') or '').strip()
        if not subject or not description:
            flash('Subject and description are required.', 'danger')
            return redirect(url_for('tickets.new_ticket'))

        priority = (request.form.get('priority') or 'Normal').strip()
        if priority not in ['Low', 'Normal', 'High', 'Urgent']:
            priority = 'Normal'

        reporter_name = (request.form.get('reporter_name') or '').strip() or None
        reporter_email = (request.form.get('reporter_email') or '').strip() or None

        asset_id = request.form.get('asset_id')
        asset = Asset.query.get(int(asset_id)) if asset_id else None

        ticket = SupportTicket(
            status='Open',
            priority=priority,
            source='web',
            subject=subject,
            description=description,
            reporter_name=reporter_name,
            reporter_email=reporter_email,
            asset_id=asset.id if asset else None,
            asset_tag=asset.asset_tag if asset else None,
            hostname=asset.name if asset else None,
            created_by_user_id=current_user.id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.session.add(ticket)
        db.session.commit()

        if asset:
            history = AssetHistory(
                asset_id=asset.id,
                action='Ticket Created',
                description=f'Support ticket #{ticket.id} created: {ticket.subject}',
                user_id=current_user.id
            )
            db.session.add(history)
            db.session.commit()

        flash(f'Ticket #{ticket.id} created.', 'success')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))

    assets = Asset.query.order_by(Asset.asset_tag.asc()).all()
    return render_template('add_ticket.html', assets=assets)


@bp.route('/tickets/<int:ticket_id>')
@login_required
@ticket_access_required
@license_required
def view_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    # Base users can only view tickets they submitted
    if current_user.role == 'base_user' and ticket.created_by_user_id != current_user.id:
        flash('You can only view your own tickets.', 'danger')
        return redirect(url_for('tickets.tickets'))
    techs = User.query.filter(User.role.in_(['admin', 'manager', 'viewer'])).order_by(db.func.coalesce(User.full_name, User.username)).all()
    # Build timeline: merge notes + activity sorted by created_at
    notes = [{'type': 'note', 'obj': n, 'ts': n.created_at} for n in ticket.notes.order_by(TicketNote.created_at).all()]
    acts = [{'type': 'activity', 'obj': a, 'ts': a.created_at} for a in ticket.activity.order_by(TicketActivity.created_at).all()]
    timeline = sorted(notes + acts, key=lambda x: x['ts'] or datetime.utcnow())
    return render_template('view_ticket.html', ticket=ticket, techs=techs, timeline=timeline)


@bp.route('/tickets/<int:ticket_id>/status', methods=['POST'])
@login_required
@license_required
def set_ticket_status(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    new_status = request.form.get('status', '').strip()
    if new_status in ('Open', 'In Progress', 'Closed'):
        old = ticket.status
        ticket.status = new_status
        if new_status == 'Closed':
            ticket.closed_at = datetime.utcnow()
            ticket.closed_by_user_id = current_user.id
        db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                      action='status_changed', detail=f'{old} → {new_status}'))
        db.session.commit()
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


@bp.route('/tickets/<int:ticket_id>/edit', methods=['POST'])
@login_required
@manager_required
@license_required
def edit_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    ticket.subject = request.form.get('subject', ticket.subject).strip()
    ticket.description = request.form.get('description', ticket.description).strip()
    db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                  action='edited', detail='Subject/description updated'))
    db.session.commit()
    flash('Ticket updated.', 'success')
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


@bp.route('/tickets/<int:ticket_id>/assign', methods=['POST'])
@login_required
@manager_required
@license_required
def assign_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    assignee_id = request.form.get('assignee_id', '0')
    if assignee_id == '0':
        ticket.assigned_to_user_id = None
        detail = 'Unassigned'
    else:
        ticket.assigned_to_user_id = int(assignee_id)
        u = User.query.get(ticket.assigned_to_user_id)
        detail = f'Assigned to {u.display_name if u else assignee_id}'
    db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                  action='assigned', detail=detail))
    db.session.commit()
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


@bp.route('/tickets/<int:ticket_id>/category', methods=['POST'])
@login_required
@license_required
def set_ticket_category(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    cat = request.form.get('category', 'General').strip()
    ticket.category = cat
    db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                  action='category_changed', detail=f'Category set to {cat}'))
    db.session.commit()
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


@bp.route('/tickets/<int:ticket_id>/priority', methods=['POST'])
@login_required
@license_required
def set_ticket_priority(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    priority = request.form.get('priority', 'Normal').strip()
    if priority in ('Low', 'Normal', 'High', 'Urgent'):
        old = ticket.priority
        ticket.priority = priority
        db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                      action='priority_changed', detail=f'{old} → {priority}'))
        db.session.commit()
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


@bp.route('/tickets/<int:ticket_id>/note', methods=['POST'])
@login_required
@license_required
def add_ticket_note(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    content = request.form.get('content', '').strip()
    if content:
        note = TicketNote(ticket_id=ticket.id, user_id=current_user.id, content=content)
        db.session.add(note)
        db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                      action='note_added', detail='Internal note added'))
        db.session.commit()
        flash('Note added.', 'success')
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


@bp.route('/tickets/<int:ticket_id>/merge', methods=['POST'])
@login_required
@manager_required
@license_required
def merge_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    try:
        target_id = int(request.form.get('merge_into_id', 0))
    except (ValueError, TypeError):
        flash('Invalid target ticket ID.', 'danger')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))
    target = SupportTicket.query.get(target_id)
    if not target or target.id == ticket.id:
        flash('Target ticket not found.', 'danger')
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))
    ticket.status = 'Merged'
    ticket.merged_into_id = target.id
    db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                  action='merged', detail=f'Merged into #{target.id}'))
    db.session.add(TicketActivity(ticket_id=target.id, user_id=current_user.id,
                                  action='merged', detail=f'#{ticket.id} merged into this ticket'))
    db.session.commit()
    flash(f'Ticket #{ticket.id} merged into #{target.id}.', 'success')
    return redirect(url_for('tickets.view_ticket', ticket_id=target.id))


@bp.route('/tickets/<int:ticket_id>/close', methods=['POST'])
@login_required
@manager_required
@license_required
def close_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    if ticket.status != 'Closed':
        ticket.status = 'Closed'
        ticket.closed_at = datetime.utcnow()
        ticket.closed_by_user_id = current_user.id
        # Generate CSAT token and send survey email if reporter email is known
        if ticket.reporter_email and not ticket.csat_token:
            ticket.csat_token = secrets.token_urlsafe(32)
            db.session.commit()
            try:
                base = request.host_url.rstrip('/')
                good_url = f"{base}/csat/{ticket.csat_token}/1"
                bad_url  = f"{base}/csat/{ticket.csat_token}/0"
                send_email(
                    subject=f'How did we do? Ticket #{ticket.id} — {ticket.subject[:60]}',
                    recipients=[ticket.reporter_email],
                    text_body=(
                        f'Hi {ticket.reporter_name or "there"},\n\n'
                        f'Your support ticket #{ticket.id} has been resolved.\n\n'
                        f'Quick question — how satisfied were you with our service?\n'
                        f'👍 Great:  {good_url}\n'
                        f'👎 Needs improvement:  {bad_url}\n\n'
                        f'Thank you for your feedback!\n— IT Support'
                    )
                )
            except Exception as _csat_err:
                logger.warning(f'CSAT email failed for ticket {ticket.id}: {_csat_err}')
        else:
            db.session.commit()
    flash(f'Ticket #{ticket.id} closed.', 'success')
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


@bp.route('/tickets/<int:ticket_id>/reopen', methods=['POST'])
@login_required
@manager_required
@license_required
def reopen_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    if ticket.status != 'Open':
        ticket.status = 'Open'
        ticket.closed_at = None
        ticket.closed_by_user_id = None
        db.session.commit()
    flash(f'Ticket #{ticket.id} reopened.', 'success')
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


@bp.route('/api/support-tickets', methods=['POST'])
@require_api_key('create_tickets')
def api_create_support_ticket():
    payload = request.get_json(silent=True) or {}
    subject = (payload.get('subject') or '').strip()
    description = (payload.get('description') or '').strip()

    if not subject or not description:
        return jsonify({'error': 'subject and description are required'}), 400

    priority = (payload.get('priority') or 'Normal').strip()
    if priority not in ['Low', 'Normal', 'High', 'Urgent']:
        priority = 'Normal'

    source = (payload.get('source') or 'api').strip().lower()
    if source not in ['api', 'tray']:
        source = 'api'

    reporter_name = (payload.get('reporter_name') or '').strip() or None
    reporter_email = (payload.get('reporter_email') or '').strip() or None
    hostname = (payload.get('hostname') or '').strip() or None
    asset_tag = (payload.get('asset_tag') or '').strip() or None
    asset_id = payload.get('asset_id')

    asset = None
    if asset_id:
        try:
            asset = Asset.query.get(int(asset_id))
        except Exception:
            asset = None
    if asset is None and asset_tag:
        asset = Asset.query.filter_by(asset_tag=asset_tag).first()

    if asset:
        asset_id = asset.id
        asset_tag = asset.asset_tag
        if not hostname:
            hostname = asset.name

    created_by_user_id = getattr(request, 'api_user_id', None)
    ticket = SupportTicket(
        status='Open',
        priority=priority,
        source=source,
        subject=subject,
        description=description,
        reporter_name=reporter_name,
        reporter_email=reporter_email,
        hostname=hostname,
        asset_id=asset_id,
        asset_tag=asset_tag,
        created_by_user_id=created_by_user_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.session.add(ticket)
    db.session.commit()

    if asset and created_by_user_id:
        history = AssetHistory(
            asset_id=asset.id,
            action='Ticket Created',
            description=f'Support ticket #{ticket.id} created via API: {ticket.subject}',
            user_id=created_by_user_id
        )
        db.session.add(history)
        db.session.commit()

    return jsonify({
        'success': True,
        'ticket_id': ticket.id,
        'ticket_url': url_for('tickets.view_ticket', ticket_id=ticket.id, _external=True),
        'status': ticket.status,
    })


def _ticket_sla_check():
    """Escalate tickets that have breached their SLA. Runs hourly."""
    SLA_HOURS = {'Low': 120, 'Normal': 72, 'High': 24, 'Urgent': 4}
    while True:
        try:
            _time.sleep(3600)  # wait 1 hour between checks
            with app.app_context():
                open_tickets = SupportTicket.query.filter(
                    SupportTicket.status.in_(['Open', 'In Progress'])
                ).all()
                now = datetime.utcnow()
                escalated = []
                for t in open_tickets:
                    hours = SLA_HOURS.get(t.priority, 72)
                    age_hours = (now - t.created_at).total_seconds() / 3600
                    if age_hours > hours:
                        # Escalate priority one level
                        escalation_map = {'Low': 'Normal', 'Normal': 'High', 'High': 'Urgent'}
                        if t.priority in escalation_map:
                            old_priority = t.priority
                            t.priority = escalation_map[t.priority]
                            # Add activity note
                            note = TicketNote(
                                ticket_id=t.id, user_id=1,
                                content=f'[SLA] Auto-escalated from {old_priority} to {t.priority} '
                                        f'({age_hours:.0f}h open, SLA: {hours}h)')
                            db.session.add(note)
                            escalated.append(t.id)
                if escalated:
                    db.session.commit()
                    logger.info(f'SLA escalation: raised priority on tickets {escalated}')
        except Exception as _sla_err:
            logger.warning(f'SLA check error: {_sla_err}')