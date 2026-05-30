import json
import os
import re
import secrets
import subprocess
import threading
import time as _time
from datetime import date, datetime, timedelta, timezone

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
    SupportTicket, TicketActivity, TicketNote, TicketTag, TicketWatcher, TicketLink,
    User, now_mst, allowed_file,
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
                               techs=[], f_status='', f_priority='', f_source='',
                               f_category='', f_assignee='', f_type='',
                               base_user_mode=True, now=datetime.utcnow())
    # ── Read filter params ────────────────────────────────────────────────────
    f_status   = request.args.get('status',   '').strip()
    f_priority = request.args.get('priority', '').strip()
    f_source   = request.args.get('source',   '').strip()
    f_category = request.args.get('category', '').strip()
    f_assignee = request.args.get('assignee', '').strip()
    f_type     = request.args.get('type',     '').strip()
    # show_alerts=1 explicitly includes alert/system auto-tickets in the queue
    show_alerts = request.args.get('show_alerts', '0').strip() == '1'

    # ── Stat cards: human tickets only (exclude alert/system noise) ───────────
    _human_src = SupportTicket.source.notin_(['alert', 'system'])
    human_open       = SupportTicket.query.filter(_human_src, SupportTicket.status == 'Open').count()
    human_inprog     = SupportTicket.query.filter(_human_src, SupportTicket.status == 'In Progress').count()
    human_closed     = SupportTicket.query.filter(_human_src, SupportTicket.status == 'Closed').count()
    human_urgent     = SupportTicket.query.filter(
        _human_src,
        SupportTicket.status.notin_(['Closed', 'Merged']),
        SupportTicket.priority == 'Urgent'
    ).count()
    human_unassigned = SupportTicket.query.filter(
        _human_src,
        SupportTicket.status.notin_(['Closed', 'Merged']),
        SupportTicket.assigned_to_user_id == None
    ).count()
    # Alert/system ticket count shown as secondary info
    alert_open_count = SupportTicket.query.filter(
        SupportTicket.source.in_(['alert', 'system']),
        SupportTicket.status.notin_(['Closed', 'Merged'])
    ).count()
    total_closed = SupportTicket.query.filter_by(status='Closed').count()
    today_date = datetime.utcnow().date()

    # ── Build filtered query ──────────────────────────────────────────────────
    q = SupportTicket.query
    # Default: hide alert/system auto-tickets unless show_alerts is set or
    # the user has explicitly filtered by source/type
    if not show_alerts and not f_source and f_type not in ('auto',):
        q = q.filter(SupportTicket.source.notin_(['alert', 'system']))
    if f_status:
        q = q.filter(SupportTicket.status == f_status)
    if f_priority:
        q = q.filter(SupportTicket.priority == f_priority)
    if f_source:
        q = q.filter(SupportTicket.source == f_source)
    if f_category:
        q = q.filter(SupportTicket.category == f_category)
    if f_assignee == 'unassigned':
        q = q.filter(SupportTicket.assigned_to_user_id == None)
    elif f_assignee:
        try:
            q = q.filter(SupportTicket.assigned_to_user_id == int(f_assignee))
        except ValueError:
            pass
    # Type filter — shortcuts over category + source
    if f_type == 'user':
        q = q.filter(SupportTicket.source.in_(['web', 'tray']))
    elif f_type == 'hardware':
        q = q.filter(SupportTicket.category == 'Hardware')
    elif f_type == 'software':
        q = q.filter(SupportTicket.category == 'Software')
    elif f_type == 'network':
        q = q.filter(SupportTicket.category == 'Network')
    elif f_type == 'auto':
        q = q.filter(SupportTicket.source.in_(['alert', 'system']))
        show_alerts = True  # mark so banner doesn't show

    tickets = q.order_by(SupportTicket.created_at.desc()).all()
    techs = User.query.filter(User.role.in_(['admin', 'manager', 'viewer', 'eagle_eyes'])) \
                      .order_by(User.full_name).all()

    # ── Chart: tickets created per day for last 30 days ───────────────────────
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

    # Technician workload (reuse techs list)
    user_map = {u.id: u for u in techs}
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

    # Closed today (from filtered set)
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

    return render_template('tickets.html', tickets=tickets,
                           total_closed=total_closed,
                           total_open=human_open + human_inprog,
                           open_count=human_open,
                           inprog_count=human_inprog,
                           urgent_count=human_urgent,
                           unassigned_count=human_unassigned,
                           alert_open_count=alert_open_count,
                           show_alerts=show_alerts,
                           chart_labels=chart_labels, chart_data=chart_data, tech_loads=tech_loads,
                           avg_hours=avg_hours,
                           closed_today=closed_today, resolution_by_priority=resolution_by_priority,
                           techs=techs,
                           f_status=f_status, f_priority=f_priority, f_source=f_source,
                           f_category=f_category, f_assignee=f_assignee, f_type=f_type,
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

        # Notify admins of new ticket via email and notification bell
        try:
            _base = request.host_url.rstrip('/')
            priority_colors = {'Urgent': '#dc3545', 'High': '#fd7e14', 'Normal': '#0d6efd', 'Low': '#6c757d'}
            color = priority_colors.get(priority, '#0d6efd')
            submitter = current_user.full_name or current_user.username
            notification_html = f"""
            <p><strong>A new support ticket has been submitted:</strong></p>
            <table style="width:100%;border-collapse:collapse;font-size:14px;">
                <tr><td style="padding:6px;font-weight:bold;width:140px;">Ticket #:</td><td style="padding:6px;">#{ticket.id}</td></tr>
                <tr><td style="padding:6px;font-weight:bold;">Subject:</td><td style="padding:6px;">{ticket.subject}</td></tr>
                <tr><td style="padding:6px;font-weight:bold;">Priority:</td><td style="padding:6px;"><span style="color:{color};font-weight:bold;">{priority}</span></td></tr>
                <tr><td style="padding:6px;font-weight:bold;">Submitted by:</td><td style="padding:6px;">{submitter}</td></tr>
                {'<tr><td style="padding:6px;font-weight:bold;">Asset:</td><td style="padding:6px;">' + asset.asset_tag + ' — ' + asset.name + '</td></tr>' if asset else ''}
            </table>
            <p style="margin-top:14px;">
                <a href="{_base}/tickets/{ticket.id}"
                   style="background:{color};color:#fff;padding:8px 16px;text-decoration:none;border-radius:4px;">
                    View Ticket #{ticket.id}
                </a>
            </p>
            """
            # If ticket_notify_email is configured, send only there.
            # Otherwise send to all admin-role users.
            from pg_db import pg_connect as _pgc
            _tcon = _pgc()
            _taddr = (_tcon.execute("SELECT value FROM setting WHERE key=%s", ('ticket_notify_email',)).fetchone() or {}).get('value', '').strip()
            _tcon.close()
            if _taddr:
                _recips = [a.strip() for a in _taddr.split(',') if a.strip()]
                if _recips:
                    send_email(f'[New Ticket #{ticket.id}] {ticket.subject}', _recips, notification_html, notification_html)
            else:
                send_admin_notification(f'[New Ticket #{ticket.id}] {ticket.subject}', notification_html)
        except Exception as _e:
            logger.warning(f'Failed to send new-ticket notification: {_e}')

        # Notify assigned technician directly if High or Urgent
        if priority in ('High', 'Urgent') and ticket.assigned_to_user_id:
            try:
                tech = User.query.get(ticket.assigned_to_user_id)
                if tech and tech.email:
                    send_email(
                        f'[{priority} Ticket #{ticket.id}] {ticket.subject}',
                        [tech.email],
                        f'A {priority} priority ticket has been assigned to you.\n\n'
                        f'Ticket #{ticket.id}: {ticket.subject}\n'
                        f'Submitted by: {submitter}\n'
                        f'URL: {_base}/tickets/{ticket.id}',
                        notification_html
                    )
            except Exception as _e:
                logger.warning(f'Failed to send technician alert email: {_e}')

        # Insert notification bell entry
        try:
            from pg_db import pg_connect
            _con = pg_connect()
            _con.execute(
                """INSERT INTO notification_bell (title, body, icon, color, link, read_flag, created_at)
                   VALUES (?, ?, 'bi-ticket-perforated', ?, ?, false, datetime('now'))""",
                (f'New Ticket #{ticket.id}', ticket.subject, 'info',
                 f'/tickets/{ticket.id}')
            )
            _con.commit()
            _con.close()
        except Exception as _e:
            logger.warning(f'Failed to insert ticket notification bell: {_e}')

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
    # Base users and eagle_eyes cannot see internal (tech-only) notes
    _notes_q = ticket.notes.order_by(TicketNote.created_at).all()
    if current_user.role in ('base_user', 'eagle_eyes'):
        _notes_q = [n for n in _notes_q if not n.is_internal]
    notes = [{'type': 'note', 'obj': n, 'ts': n.created_at} for n in _notes_q]
    acts = [{'type': 'activity', 'obj': a, 'ts': a.created_at} for a in ticket.activity.order_by(TicketActivity.created_at).all()]
    timeline = sorted(notes + acts, key=lambda x: x['ts'] or datetime.utcnow())
    all_tags = TicketTag.query.order_by(TicketTag.name).all()
    watcher_ids = {w.user_id for w in ticket.watchers.all()}
    all_users = User.query.filter(User.role.in_(['admin', 'manager', 'viewer', 'eagle_eyes'])) \
                          .order_by(db.func.coalesce(User.full_name, User.username)).all()
    linked = ticket.links.all()
    return render_template('view_ticket.html', ticket=ticket, techs=techs, timeline=timeline,
                           today=date.today(), all_tags=all_tags, watcher_ids=watcher_ids,
                           all_users=all_users, linked=linked)


@bp.route('/tickets/<int:ticket_id>/delete', methods=['POST'])
@login_required
@admin_required
@license_required
def delete_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    subject = ticket.subject
    TicketNote.query.filter_by(ticket_id=ticket.id).delete()
    TicketActivity.query.filter_by(ticket_id=ticket.id).delete()
    db.session.delete(ticket)
    db.session.commit()
    flash(f'Ticket "{subject}" has been permanently deleted.', 'warning')
    return redirect(url_for('tickets.tickets'))


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
        # Email technician if ticket is High or Urgent
        if ticket.priority in ('High', 'Urgent') and u and u.email:
            try:
                _base = request.host_url.rstrip('/')
                priority_colors = {'Urgent': '#dc3545', 'High': '#fd7e14'}
                col = priority_colors.get(ticket.priority, '#0d6efd')
                send_email(
                    f'[{ticket.priority} Ticket #{ticket.id} Assigned] {ticket.subject}',
                    [u.email],
                    f'Ticket #{ticket.id} ({ticket.priority}) has been assigned to you.\n'
                    f'URL: {_base}/tickets/{ticket.id}',
                    f'<p>Ticket <strong>#{ticket.id}</strong> '
                    f'(<span style="color:{col};font-weight:bold">{ticket.priority}</span>) '
                    f'has been assigned to you: <em>{ticket.subject}</em></p>'
                    f'<p><a href="{_base}/tickets/{ticket.id}" '
                    f'style="background:{col};color:#fff;padding:7px 14px;text-decoration:none;border-radius:4px;">'
                    f'View Ticket #{ticket.id}</a></p>'
                )
            except Exception as _e:
                logger.warning(f'Failed to send assignment alert email: {_e}')
    db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                  action='assigned', detail=detail))
    db.session.commit()
    # Bell notification for all assignments
    try:
        from pg_db import pg_connect
        _con = pg_connect()
        _con.execute(
            """INSERT INTO notification_bell (title, body, icon, color, link, read_flag, created_at)
               VALUES (?, ?, 'bi-ticket-perforated', 'info', ?, false, datetime('now'))""",
            (f'Ticket #{ticket.id} Assigned', f'{detail}: {ticket.subject}', f'/tickets/{ticket.id}')
        )
        _con.commit()
        _con.close()
    except Exception as _e:
        logger.warning(f'Failed to insert assignment bell: {_e}')
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
        # Alert the assigned technician if escalated to High or Urgent
        if priority in ('High', 'Urgent') and old not in ('High', 'Urgent') and ticket.assigned_to_user_id:
            try:
                _base = request.host_url.rstrip('/')
                tech = User.query.get(ticket.assigned_to_user_id)
                if tech and tech.email:
                    col = '#dc3545' if priority == 'Urgent' else '#fd7e14'
                    send_email(
                        f'[Ticket #{ticket.id} Escalated to {priority}] {ticket.subject}',
                        [tech.email],
                        f'Ticket #{ticket.id} has been escalated to {priority}.\n'
                        f'URL: {_base}/tickets/{ticket.id}',
                        f'<p>Ticket <strong>#{ticket.id}</strong> has been escalated to '
                        f'<span style="color:{col};font-weight:bold">{priority}</span>: '
                        f'<em>{ticket.subject}</em></p>'
                        f'<p><a href="{_base}/tickets/{ticket.id}" '
                        f'style="background:{col};color:#fff;padding:7px 14px;text-decoration:none;border-radius:4px;">'
                        f'View Ticket #{ticket.id}</a></p>'
                    )
            except Exception as _e:
                logger.warning(f'Failed to send escalation email: {_e}')
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


@bp.route('/tickets/<int:ticket_id>/note', methods=['POST'])
@login_required
@license_required
def add_ticket_note(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    content = request.form.get('content', '').strip()
    is_internal = request.form.get('is_internal') == '1'
    is_reply = request.form.get('is_reply') == '1'
    reply_to_addr = request.form.get('reply_to', '').strip() if is_reply else None

    if not content:
        return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))

    if is_reply:
        is_internal = False  # replies are never internal
        # Send email to the reporter
        if reply_to_addr:
            try:
                _base = request.host_url.rstrip('/')
                tech_name = current_user.full_name or current_user.username
                html_body = f"""
                <div style="font-family:sans-serif;max-width:600px;">
                  <p>Hi {ticket.reporter_name or 'there'},</p>
                  <p>You have a reply from <strong>{tech_name}</strong> regarding your support ticket
                     <strong>#{ticket.id}: {ticket.subject}</strong>:</p>
                  <blockquote style="border-left:4px solid #0d6efd;padding:10px 16px;
                                     background:#f0f4ff;margin:16px 0;border-radius:4px;">
                    <p style="margin:0;white-space:pre-wrap;">{content}</p>
                  </blockquote>
                  <p style="margin-top:20px;">
                    <a href="{_base}/tickets/{ticket.id}"
                       style="background:#0d6efd;color:#fff;padding:8px 16px;
                              text-decoration:none;border-radius:4px;">
                      View Ticket #{ticket.id}
                    </a>
                  </p>
                  <p style="color:#6c757d;font-size:12px;margin-top:24px;">
                    Ticket #{ticket.id} · IT Support
                  </p>
                </div>"""
                send_email(
                    subject=f'Re: [Ticket #{ticket.id}] {ticket.subject}',
                    recipients=[reply_to_addr],
                    text_body=content,
                    html_body=html_body,
                )
            except Exception as _e:
                logger.warning(f'Reply email failed for ticket {ticket.id}: {_e}')
                flash(f'Note saved but email delivery failed: {_e}', 'warning')

    note = TicketNote(ticket_id=ticket.id, user_id=current_user.id,
                      content=content, is_internal=is_internal,
                      is_reply=is_reply,
                      reply_to=reply_to_addr if is_reply else None)
    db.session.add(note)
    detail = (f'Reply sent to {reply_to_addr}' if is_reply
              else ('Internal note (tech only)' if is_internal else 'Note added'))
    db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                  action='note_added', detail=detail))
    db.session.commit()

    if not is_internal:
        _notify_watchers(ticket,
                         f'{"Reply" if is_reply else "Note"} on Ticket #{ticket.id}',
                         f'<p>{"A reply was sent" if is_reply else "A note was added"} on '
                         f'<strong>#{ticket.id}: {ticket.subject}</strong>:</p>'
                         f'<blockquote style="border-left:3px solid #ccc;padding-left:10px;">'
                         f'{content}</blockquote>')
    flash('Reply sent.' if is_reply else 'Note added.', 'success')
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


@bp.route('/tickets/<int:ticket_id>/due_date', methods=['POST'])
@login_required
@manager_required
@license_required
def set_ticket_due_date(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    date_str = request.form.get('due_date', '').strip()
    if date_str:
        try:
            ticket.due_date = date.fromisoformat(date_str)
        except ValueError:
            flash('Invalid date format.', 'danger')
            return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))
    else:
        ticket.due_date = None
    db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                  action='due_date_set',
                                  detail=f'Due date set to {ticket.due_date}' if ticket.due_date else 'Due date cleared'))
    db.session.commit()
    flash('Due date updated.', 'success')
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


@bp.route('/tickets/bulk', methods=['POST'])
@login_required
@manager_required
@license_required
def bulk_action():
    action = request.form.get('bulk_action', '').strip()
    raw_ids = request.form.getlist('ticket_ids')
    if not raw_ids or not action:
        flash('No tickets selected.', 'warning')
        return redirect(url_for('tickets.tickets'))
    try:
        ids = [int(i) for i in raw_ids]
    except ValueError:
        abort(400)
    bulk_tickets = SupportTicket.query.filter(SupportTicket.id.in_(ids)).all()
    count = len(bulk_tickets)
    if action == 'close':
        for t in bulk_tickets:
            if t.status not in ('Closed', 'Merged'):
                t.status = 'Closed'
                t.closed_at = datetime.utcnow()
                t.closed_by_user_id = current_user.id
                db.session.add(TicketActivity(ticket_id=t.id, user_id=current_user.id,
                                              action='closed', detail='Bulk closed'))
    elif action == 'assign':
        assignee_raw = request.form.get('bulk_assignee_id', '0')
        assignee_id = int(assignee_raw) if assignee_raw.isdigit() and assignee_raw != '0' else None
        for t in bulk_tickets:
            t.assigned_to_user_id = assignee_id
            label = User.query.get(assignee_id).display_name if assignee_id else 'Unassigned'
            db.session.add(TicketActivity(ticket_id=t.id, user_id=current_user.id,
                                          action='assigned', detail=f'Bulk assigned to {label}'))
    elif action == 'priority':
        priority = request.form.get('bulk_priority', 'Normal')
        if priority not in ('Low', 'Normal', 'High', 'Urgent'):
            abort(400)
        for t in bulk_tickets:
            old = t.priority
            t.priority = priority
            db.session.add(TicketActivity(ticket_id=t.id, user_id=current_user.id,
                                          action='priority_changed',
                                          detail=f'Bulk: {old} → {priority}'))
    elif action == 'status':
        status = request.form.get('bulk_status', 'Open')
        if status not in ('Open', 'In Progress', 'Closed'):
            abort(400)
        for t in bulk_tickets:
            if t.status in ('Merged',):
                continue
            t.status = status
            if status == 'Closed' and t.closed_at is None:
                t.closed_at = datetime.utcnow()
                t.closed_by_user_id = current_user.id
            db.session.add(TicketActivity(ticket_id=t.id, user_id=current_user.id,
                                          action='status_changed',
                                          detail=f'Bulk: status → {status}'))
    elif action == 'delete':
        if current_user.role not in ('admin', 'superadmin'):
            abort(403)
        for t in bulk_tickets:
            # Remove reverse TicketLink rows pointing at this ticket
            TicketLink.query.filter_by(linked_ticket_id=t.id).delete()
            # Nullify merged_into references pointing at this ticket
            SupportTicket.query.filter_by(merged_into_id=t.id).update({'merged_into_id': None})
            db.session.delete(t)
        db.session.commit()
        flash(f'{count} ticket(s) permanently deleted.', 'danger')
        return redirect(url_for('tickets.tickets'))
    else:
        abort(400)
    db.session.commit()
    flash(f'{count} ticket(s) updated.', 'success')
    return redirect(request.referrer or url_for('tickets.tickets'))


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
    _notify_watchers(ticket, f'Ticket #{ticket.id} Closed',
                     f'<p>Ticket <strong>#{ticket.id}: {ticket.subject}</strong> has been closed.</p>')
    flash(f'Ticket #{ticket.id} closed.', 'success')
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


@bp.route('/tickets/<int:ticket_id>/reopen', methods=['POST'])
@login_required
@manager_required
@license_required
def reopen_ticket(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    if ticket.status != 'Open':
        old = ticket.status
        ticket.status = 'Open'
        ticket.closed_at = None
        ticket.closed_by_user_id = None
        db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                      action='status_changed', detail=f'{old} → Open'))
        db.session.commit()
    flash(f'Ticket #{ticket.id} reopened.', 'success')
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


# ─── Watcher notification helper ─────────────────────────────────────────────

def _notify_watchers(ticket, subject_prefix, message_html):
    """Email all watchers of a ticket (excluding the current acting user)."""
    try:
        _base = 'http://localhost'  # fallback if outside request context
        try:
            _base = request.host_url.rstrip('/')
        except Exception:
            pass
        ticket_url = f'{_base}/tickets/{ticket.id}'
        full_html = (f'{message_html}<p><a href="{ticket_url}">View Ticket #{ticket.id}</a></p>')
        for w in ticket.watchers.all():
            u = w.user
            if not u or not u.email:
                continue
            if u.id == current_user.id:
                continue
            try:
                send_email(f'{subject_prefix} [Ticket #{ticket.id}]', [u.email],
                           f'{ticket.subject}\n\n{ticket_url}', full_html)
            except Exception as _e:
                logger.debug(f'Watcher email failed for user {u.id}: {_e}')
    except Exception as _e:
        logger.warning(f'_notify_watchers error: {_e}')


# ─── Tags ─────────────────────────────────────────────────────────────────────

@bp.route('/tickets/tags', methods=['GET', 'POST'])
@login_required
@manager_required
@license_required
def manage_tags():
    """Create / delete global ticket tags."""
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'create':
            name = request.form.get('name', '').strip()[:50]
            color = request.form.get('color', '#6c757d').strip()
            if name and not TicketTag.query.filter_by(name=name).first():
                db.session.add(TicketTag(name=name, color=color))
                db.session.commit()
                flash(f'Tag "{name}" created.', 'success')
            elif not name:
                flash('Tag name is required.', 'danger')
            else:
                flash('A tag with that name already exists.', 'warning')
        elif action == 'delete':
            tag_id = request.form.get('tag_id')
            tag = TicketTag.query.get_or_404(int(tag_id))
            db.session.delete(tag)
            db.session.commit()
            flash(f'Tag "{tag.name}" deleted.', 'success')
        return redirect(url_for('tickets.manage_tags'))
    tags = TicketTag.query.order_by(TicketTag.name).all()
    return render_template('manage_tags.html', tags=tags)


@bp.route('/tickets/<int:ticket_id>/tags', methods=['POST'])
@login_required
@manager_required
@license_required
def update_ticket_tags(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    selected_ids = set(int(i) for i in request.form.getlist('tag_ids') if i.isdigit())
    new_tags = TicketTag.query.filter(TicketTag.id.in_(selected_ids)).all() if selected_ids else []
    ticket.tags = new_tags
    db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                  action='tags_updated',
                                  detail='Tags: ' + ', '.join(t.name for t in new_tags) if new_tags else 'Tags cleared'))
    db.session.commit()
    flash('Tags updated.', 'success')
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


# ─── Watchers ─────────────────────────────────────────────────────────────────

@bp.route('/tickets/<int:ticket_id>/watchers', methods=['POST'])
@login_required
@manager_required
@license_required
def update_ticket_watchers(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    action = request.form.get('action', 'add')
    user_id_raw = request.form.get('user_id', '0')
    if not user_id_raw.isdigit():
        abort(400)
    user_id = int(user_id_raw)
    if action == 'add' and user_id:
        existing = TicketWatcher.query.filter_by(ticket_id=ticket.id, user_id=user_id).first()
        if not existing:
            db.session.add(TicketWatcher(ticket_id=ticket.id, user_id=user_id))
            u = User.query.get(user_id)
            db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                          action='watcher_added',
                                          detail=f'{u.display_name if u else user_id} added as watcher'))
            db.session.commit()
            flash('Watcher added.', 'success')
    elif action == 'remove' and user_id:
        TicketWatcher.query.filter_by(ticket_id=ticket.id, user_id=user_id).delete()
        u = User.query.get(user_id)
        db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                      action='watcher_removed',
                                      detail=f'{u.display_name if u else user_id} removed as watcher'))
        db.session.commit()
        flash('Watcher removed.', 'success')
    return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))


# ─── Linked tickets ───────────────────────────────────────────────────────────

@bp.route('/tickets/<int:ticket_id>/links', methods=['POST'])
@login_required
@manager_required
@license_required
def update_ticket_links(ticket_id):
    ticket = SupportTicket.query.get_or_404(ticket_id)
    action = request.form.get('action', 'add')
    if action == 'add':
        raw = request.form.get('linked_ticket_id', '').strip()
        if not raw.isdigit():
            flash('Enter a valid ticket number.', 'danger')
            return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))
        linked_id = int(raw)
        if linked_id == ticket.id:
            flash("A ticket can't be linked to itself.", 'warning')
            return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))
        target = SupportTicket.query.get(linked_id)
        if not target:
            flash(f'Ticket #{linked_id} not found.', 'danger')
            return redirect(url_for('tickets.view_ticket', ticket_id=ticket.id))
        # Check both directions
        existing = TicketLink.query.filter(
            db.or_(
                db.and_(TicketLink.ticket_id == ticket.id, TicketLink.linked_ticket_id == linked_id),
                db.and_(TicketLink.ticket_id == linked_id, TicketLink.linked_ticket_id == ticket.id),
            )
        ).first()
        if not existing:
            db.session.add(TicketLink(ticket_id=ticket.id, linked_ticket_id=linked_id))
            # Mirror link so both sides show it
            db.session.add(TicketLink(ticket_id=linked_id, linked_ticket_id=ticket.id))
            db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                          action='linked', detail=f'Linked to #{linked_id}'))
            db.session.commit()
            flash(f'Linked to Ticket #{linked_id}.', 'success')
        else:
            flash('Tickets are already linked.', 'info')
    elif action == 'remove':
        link_id = request.form.get('link_id', '')
        if link_id.isdigit():
            lnk = TicketLink.query.get(int(link_id))
            if lnk and lnk.ticket_id == ticket.id:
                mirror = TicketLink.query.filter_by(
                    ticket_id=lnk.linked_ticket_id, linked_ticket_id=ticket.id).first()
                if mirror:
                    db.session.delete(mirror)
                db.session.delete(lnk)
                db.session.add(TicketActivity(ticket_id=ticket.id, user_id=current_user.id,
                                              action='unlinked',
                                              detail=f'Removed link to #{lnk.linked_ticket_id}'))
                db.session.commit()
                flash('Link removed.', 'success')
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

    # Bell notification for API-created tickets
    try:
        from pg_db import pg_connect
        _con = pg_connect()
        _con.execute(
            """INSERT INTO notification_bell (title, body, icon, color, link, read_flag, created_at)
               VALUES (?, ?, 'bi-ticket-perforated', 'info', ?, false, datetime('now'))""",
            (f'New Ticket #{ticket.id}', ticket.subject, f'/tickets/{ticket.id}')
        )
        _con.commit()
        _con.close()
    except Exception as _e:
        logger.warning(f'Failed to insert API ticket bell: {_e}')

    return jsonify({
        'success': True,
        'ticket_id': ticket.id,
        'ticket_url': url_for('tickets.view_ticket', ticket_id=ticket.id, _external=True),
        'status': ticket.status,
    })


def _ticket_sla_check(flask_app):
    """Escalate tickets that have breached their SLA. Runs hourly.

    Started in every gunicorn worker, so each pass is guarded by a cross-process
    file lock — only the worker that wins the lock does the work. Without this,
    all workers escalate concurrently, bumping a ticket several levels at once
    and writing duplicate SLA notes.
    """
    from sync_scheduler import _file_lock
    sla_lock_path = os.environ.get('TRACKER_SLA_LOCK_PATH', '/tmp/tracker_sla_check.lock')
    while True:
        try:
            _time.sleep(3600)  # wait 1 hour between checks
            with _file_lock(sla_lock_path) as got_lock:
                if got_lock:
                    _do_sla_pass(flask_app)
        except Exception as _sla_err:
            logger.warning(f'SLA check error: {_sla_err}')


def _do_sla_pass(flask_app):
    """Run a single SLA escalation pass. Caller holds the single-instance lock."""
    SLA_HOURS = {'Low': 120, 'Normal': 72, 'High': 24, 'Urgent': 4}
    with flask_app.app_context():
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