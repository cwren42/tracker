"""Web SSH-terminal routes for the RMM blueprint.

Split out of the oversized blueprints/rmm.py. These routes register on the same
`rmm` blueprint (imported from blueprints.rmm), so URLs and endpoint names are
unchanged (rmm.terminal, rmm.api_terminal_connect, ...).
"""
import uuid

from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from blueprints.rmm import bp
from models import Asset
from ssh_terminal_manager import get_ssh_manager


@bp.route('/terminal/<int:asset_id>')
@login_required
def terminal(asset_id):
    """Web-based SSH terminal for an asset"""
    asset = Asset.query.get_or_404(asset_id)
    return render_template('terminal.html', asset=asset)


@bp.route('/api/terminal/connect', methods=['POST'])
@login_required
def api_terminal_connect():
    """Connect to an asset via SSH"""
    data = request.get_json()

    asset_id = data.get('asset_id')
    username = data.get('username', 'root')
    password = data.get('password')

    asset = Asset.query.get_or_404(asset_id)

    # Generate session ID
    session_id = f"{current_user.id}:{asset_id}:{str(uuid.uuid4())[:8]}"

    # Create SSH session
    ssh_manager = get_ssh_manager()
    session = ssh_manager.create_session(
        session_id=session_id,
        hostname=asset.ip_address_1,
        username=username,
        password=password,
        port=22
    )

    if session:
        return jsonify({
            'success': True,
            'session_id': session_id
        })
    else:
        return jsonify({
            'success': False,
            'error': 'Failed to connect'
        }), 500


@bp.route('/api/terminal/input', methods=['POST'])
@login_required
def api_terminal_input():
    """Send input to SSH session"""
    data = request.get_json()

    session_id = data.get('session_id')
    input_data = data.get('data', '')

    ssh_manager = get_ssh_manager()
    session = ssh_manager.get_session(session_id)

    if not session:
        return jsonify({'success': False, 'error': 'Session not found'}), 404

    if session.send_input(input_data):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Failed to send input'}), 500


@bp.route('/api/terminal/output', methods=['POST'])
@login_required
def api_terminal_output():
    """Get output from SSH session"""
    data = request.get_json()

    session_id = data.get('session_id')
    since_index = data.get('since_index', 0)

    ssh_manager = get_ssh_manager()
    session = ssh_manager.get_session(session_id)

    if not session:
        return jsonify({'success': False, 'error': 'Session not found'}), 404

    output = session.get_output(since_index)

    return jsonify({
        'success': True,
        'output': output,
        'index': since_index + len(output)
    })


@bp.route('/api/terminal/disconnect', methods=['POST'])
@login_required
def api_terminal_disconnect():
    """Disconnect SSH session"""
    data = request.get_json()

    session_id = data.get('session_id')

    ssh_manager = get_ssh_manager()
    if ssh_manager.close_session(session_id):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'error': 'Session not found'}), 404
