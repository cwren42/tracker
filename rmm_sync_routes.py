"""
RMM Agent Data Sync Routes

These endpoints receive system info and telemetry from Linux agents
and update asset records with hardware details.
"""

from flask import request, jsonify
from sqlalchemy import text
from datetime import datetime
import json

# Add to app.py after existing RMM routes (around line 3800)

@app.route('/api/rmm/system-info', methods=['POST'])
def receive_system_info():
    """Receive system info from Linux agent and update asset record"""
    try:
        data = request.get_json()
        
        if not data or 'agent_id' not in data:
            return jsonify({'ok': False, 'error': 'Missing agent_id'}), 400
        
        agent_id = data['agent_id']
        hostname = data.get('hostname', 'Unknown')
        
        # Find asset by agent_id (stored in a field or by hostname)
        # For now, match by name/hostname
        asset = Asset.query.filter(
            (Asset.name.ilike(f'%{hostname}%')) | 
            (Asset.serial_number == agent_id)
        ).first()
        
        if not asset:
            # Create new asset from agent data
            asset = Asset(
                asset_tag=f'LINUX-{agent_id[:8].upper()}',
                name=hostname,
                category='Server' if 'server' in hostname.lower() else 'Workstation',
                device_type='Virtual Machine' if data.get('virtualization', {}).get('is_virtual') else 'Linux Workstation',
                status='In Use'
            )
            db.session.add(asset)
        
        # Update asset with system info
        os_info = data.get('os', {})
        cpu_info = data.get('cpu', {})
        mem_info = data.get('memory', {})
        virt_info = data.get('virtualization', {})
        
        asset.operating_system = os_info.get('pretty_name', 'Linux')
        asset.os_version = os_info.get('version', 'Unknown')
        asset.hardware_cpu = cpu_info.get('model', 'Unknown').strip()
        asset.hardware_ram_gb = mem_info.get('total_gb')
        asset.last_seen = datetime.utcnow()
        asset.online_state = 'Online'
        
        # Set manufacturer/model from virtualization or detect
        if virt_info.get('is_virtual'):
            asset.manufacturer = virt_info.get('type', 'Unknown').upper()
            asset.model = 'Virtual Machine'
            if not asset.device_type or asset.device_type == '-':
                asset.device_type = 'Virtual Machine'
        
        # Store agent_id in a field (using serial_number for now)
        if not asset.serial_number:
            asset.serial_number = agent_id
        
        db.session.commit()
        
        return jsonify({
            'ok': True,
            'asset_id': asset.id,
            'message': f'Updated asset {asset.name}'
        })
        
    except Exception as e:
        logger.error(f"Error receiving system info: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/rmm/telemetry', methods=['POST'])
def receive_telemetry():
    """Receive telemetry from Linux agent"""
    try:
        data = request.get_json()
        
        if not data or 'agent_id' not in data:
            return jsonify({'ok': False, 'error': 'Missing agent_id'}), 400
        
        agent_id = data['agent_id']
        
        # Store in rmm_telemetry table if exists, or update asset last_seen
        asset = Asset.query.filter_by(serial_number=agent_id).first()
        if asset:
            asset.last_seen = datetime.utcnow()
            asset.online_state = 'Online'
            
            # Update hardware metrics
            if 'memory_percent' in data:
                # Store latest telemetry in database (if table exists)
                pass
            
            db.session.commit()
        
        return jsonify({'ok': True})
        
    except Exception as e:
        logger.error(f"Error receiving telemetry: {e}")
        return jsonify({'ok': False, 'error': str(e)}), 500
