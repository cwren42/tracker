import json
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime, timedelta, timezone
try:
    import msal
except ImportError:
    msal = None
import requests
from werkzeug.security import generate_password_hash, check_password_hash

from flask import (Blueprint, abort, current_app, flash, g, jsonify,
                   redirect, render_template, request, send_file, session,
                   url_for)
from flask_login import current_user, login_required, login_user, logout_user
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
from utils import (
    admin_required, manager_required, eagle_eyes_required,
    ticket_access_required, license_required,
    send_email, send_admin_notification, send_asset_assignment_email,
    send_warranty_expiry_alert, send_lifecycle_alert,
)


bp = Blueprint('auth', __name__)


# ── Restored: /csat/<token>/<int:score> ──



@bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute; 30 per hour")
def login():
    """Login page - supports local and Azure AD authentication"""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))
    
    if request.method == 'POST':
        # Local login disabled — use Microsoft 365 SSO
        flash('Local login is disabled. Please sign in with Microsoft.', 'warning')
        return redirect(url_for('auth.login'))
    
    # Check if Azure AD is configured for SSO button
    azure_config = AzureIntegrationConfig.query.filter_by(enabled=True, app_name='tracker').first()
    azure_enabled = azure_config is not None
    
    return render_template('login.html', azure_enabled=azure_enabled)


@bp.route('/login/microsoft')
def login_microsoft():
    """Initiate Microsoft/Azure AD OAuth2 login flow"""
    # Get Azure configuration
    azure_config = AzureIntegrationConfig.query.filter_by(enabled=True, app_name='tracker').first()
    
    if not azure_config:
        flash('Azure AD authentication is not configured.', 'danger')
        return redirect(url_for('auth.login'))
    
    # Create MSAL confidential client
    msal_app = msal.ConfidentialClientApplication(
        azure_config.client_id,
        authority=f"https://login.microsoftonline.com/{azure_config.tenant_id}",
        client_credential=azure_config.client_secret
    )
    
    # Generate auth URL with PKCE
    session['state'] = str(uuid.uuid4())
    
    auth_url = msal_app.get_authorization_request_url(
        scopes=["User.Read"],
        state=session['state'],
        redirect_uri=url_for('auth.login_microsoft_callback', _external=True, _scheme='https')
    )
    
    return redirect(auth_url)


@bp.route('/login/microsoft/callback')
def login_microsoft_callback():
    """Handle Microsoft/Azure AD OAuth2 callback"""
    # Verify state to prevent CSRF
    if request.args.get('state') != session.get('state'):
        flash('Authentication failed: Invalid state parameter.', 'danger')
        return redirect(url_for('auth.login'))
    
    # Check for errors
    if 'error' in request.args:
        flash(f'Authentication failed: {request.args.get("error_description", "Unknown error")}', 'danger')
        return redirect(url_for('auth.login'))
    
    # Get authorization code
    code = request.args.get('code')
    if not code:
        flash('Authentication failed: No authorization code received.', 'danger')
        return redirect(url_for('auth.login'))
    
    # Get Azure configuration
    azure_config = AzureIntegrationConfig.query.filter_by(enabled=True, app_name='tracker').first()
    
    if not azure_config:
        flash('Azure AD authentication is not configured.', 'danger')
        return redirect(url_for('auth.login'))
    
    # Exchange code for token
    msal_app = msal.ConfidentialClientApplication(
        azure_config.client_id,
        authority=f"https://login.microsoftonline.com/{azure_config.tenant_id}",
        client_credential=azure_config.client_secret
    )
    
    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=["User.Read"],
        redirect_uri=url_for('auth.login_microsoft_callback', _external=True, _scheme='https')
    )
    
    if "error" in result:
        flash(f'Authentication failed: {result.get("error_description", "Unknown error")}', 'danger')
        return redirect(url_for('auth.login'))
    
    # Get user info from Microsoft Graph
    access_token = result['access_token']
    graph_response = requests.get(
        'https://graph.microsoft.com/v1.0/me',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    
    if graph_response.status_code != 200:
        flash('Failed to retrieve user information from Microsoft.', 'danger')
        return redirect(url_for('auth.login'))
    
    user_info = graph_response.json()
    
    # Extract user details
    email = user_info.get('mail') or user_info.get('userPrincipalName')
    display_name = user_info.get('displayName', '')
    azure_id = user_info.get('id')
    
    # Check if user exists in database
    user = User.query.filter_by(email=email).first()
    
    if not user:
        # Auto-create user account from Azure AD
        username = email.split('@')[0]  # Use email prefix as username
        
        # Check if username already exists
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            username = f"{username}_{azure_id[:8]}"  # Make unique
        
        user = User(
            username=username,
            email=email,
            full_name=display_name,
            password_hash=generate_password_hash(str(uuid.uuid4())),
            role='viewer',  # Default role for new users
            azure_id=azure_id,
            created_at=datetime.utcnow()
        )
        db.session.add(user)
        db.session.commit()
        
        flash(f'Welcome to the Asset Tracker! Your account has been created.', 'success')
    else:
        # Update azure_id if not set
        if not user.azure_id:
            user.azure_id = azure_id
            db.session.commit()
        
        flash(f'Welcome back, {display_name}!', 'success')
    
    # Log user in
    session.permanent = True
    login_user(user)
    user.last_login = datetime.utcnow()
    db.session.commit()
    
    return redirect(url_for('dashboard.index'))


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('auth.login'))


@bp.route('/users')
@login_required
@admin_required
@license_required
def users():
    """List all users"""
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('users.html', users=users)


@bp.route('/users/add', methods=['GET', 'POST'])
@login_required
@admin_required
@license_required
def add_user():
    """Add a new user"""
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role', 'viewer')
        
        # Check if username or email already exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'danger')
            return redirect(url_for('auth.add_user'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists', 'danger')
            return redirect(url_for('auth.add_user'))
        
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        full_name = f"{first_name} {last_name}".strip() or None

        password_confirm = request.form.get('password_confirm', '')
        if password != password_confirm:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('auth.add_user'))

        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            role=role,
            is_admin=(role == 'admin'),
            full_name=full_name,
        )
        
        db.session.add(user)
        db.session.commit()
        
        flash(f'User {user.display_name} created successfully!', 'success')
        return redirect(url_for('auth.users'))
    
    return render_template('add_user.html')


@bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
@license_required
def edit_user(user_id):
    """Edit user details"""
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        user.username = request.form.get('username')
        user.email = request.form.get('email')
        user.role = request.form.get('role')
        user.is_admin = (user.role == 'admin')

        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        user.full_name = f"{first_name} {last_name}".strip() or None
        
        # Update password if provided
        new_password = request.form.get('password')
        if new_password:
            if len(new_password) < 6:
                flash('Password must be at least 6 characters.', 'danger')
                return redirect(url_for('auth.edit_user', user_id=user_id))
            user.password_hash = generate_password_hash(new_password)
        
        db.session.commit()
        flash(f'User {user.display_name} updated successfully!', 'success')
        return redirect(url_for('auth.users'))

    # Split full_name into first/last for pre-population
    name_parts = (user.full_name or '').split(' ', 1)
    user._first_name = name_parts[0] if name_parts else ''
    user._last_name = name_parts[1] if len(name_parts) > 1 else ''
    return render_template('edit_user.html', user=user)


@bp.route('/users/<int:user_id>/view-as', methods=['POST'])
@login_required
@admin_required
def view_as_user(user_id):
    """Allow an admin to impersonate another user to preview their experience."""
    if user_id == current_user.id:
        flash('You cannot view as yourself.', 'warning')
        return redirect(url_for('auth.users'))
    target = User.query.get_or_404(user_id)
    session['impersonate_user_id'] = user_id
    session['impersonate_real_admin_id'] = current_user.id
    flash(f'Now viewing as {target.display_name} ({target.role}). Use the banner at the top to stop.', 'warning')
    return redirect(url_for('dashboard.index'))


@bp.route('/users/stop-view-as', methods=['POST'])
@login_required
def stop_view_as():
    """End impersonation session and return to real admin account."""
    session.pop('impersonate_user_id', None)
    session.pop('impersonate_real_admin_id', None)
    flash('Returned to your admin account.', 'success')
    return redirect(url_for('auth.users'))


@bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
@license_required
def delete_user(user_id):
    """Delete a user"""
    user = User.query.get_or_404(user_id)
    
    # Prevent deleting yourself
    if user.id == current_user.id:
        flash('You cannot delete your own account', 'danger')
        return redirect(url_for('auth.users'))
    
    username = user.username
    db.session.delete(user)
    db.session.commit()
    
    flash(f'User {username} deleted successfully', 'success')
    return redirect(url_for('auth.users'))


@bp.route('/users/<int:user_id>/reset-password', methods=['POST'])
@login_required
@admin_required
@license_required
def reset_user_password(user_id):
    """Admin sets a new temporary password for a user"""
    user = User.query.get_or_404(user_id)
    new_password = request.form.get('new_password', '')
    if len(new_password) < 6:
        flash('Password must be at least 6 characters.', 'danger')
        return redirect(url_for('auth.edit_user', user_id=user_id))
    user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    flash(f'Password for {user.display_name} has been reset.', 'success')
    return redirect(url_for('auth.users'))


@bp.route('/csat/<token>/<int:score>')
def csat_response(token, score):
    """Public endpoint — reporter clicks 👍 or 👎 in the close email."""
    ticket = SupportTicket.query.filter_by(csat_token=token).first_or_404()
    if ticket.csat_score is None:
        ticket.csat_score = 1 if score >= 1 else 0
        ticket.csat_comment = request.args.get('comment', '').strip() or None
        db.session.commit()
        label = 'positive' if ticket.csat_score == 1 else 'negative'
        return f"""<!doctype html><html><head><meta charset=utf-8>
        <title>Feedback received</title>
        <style>body{{font-family:Arial,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f8f9fa;}}
        .box{{text-align:center;padding:40px;background:#fff;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.1);max-width:400px;}}</style></head>
        <body><div class="box">
        <div style="font-size:64px">{'👍' if ticket.csat_score==1 else '👎'}</div>
        <h2>Thanks for your feedback!</h2>
        <p>Your {label} response has been recorded for ticket <strong>#{ticket.id}</strong>.</p>
        </div></body></html>"""
    return """<!doctype html><html><head><meta charset=utf-8><title>Already rated</title></head>
    <body style="font-family:Arial,sans-serif;text-align:center;padding:60px">
    <h2>Already recorded</h2><p>This ticket has already been rated. Thank you!</p></body></html>"""