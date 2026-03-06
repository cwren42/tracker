"""API key system for Tracker.

This mirrors the Incident portal API key implementation so keys stored in the
shared database can be used across apps.

Permissions are stored as a JSON array of strings in `api_keys.permissions`.
"""

import json
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from functools import wraps

from flask import request, jsonify

DB_PATH = '/var/www/tracker/assets.db'


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def generate_api_key():
    return 'cirque_' + secrets.token_urlsafe(32)


def create_api_key(user_id, key_name, permissions, rate_limit=100, expires_days=365):
    conn = get_db()
    cur = conn.cursor()

    api_key = generate_api_key()
    expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat() if expires_days else None

    cur.execute(
        '''
        INSERT INTO api_keys (key_name, api_key, user_id, permissions, rate_limit, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (key_name, api_key, user_id, json.dumps(permissions), rate_limit, expires_at)
    )

    conn.commit()
    key_id = cur.lastrowid
    conn.close()

    return {'id': key_id, 'api_key': api_key}


def validate_api_key(api_key):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        '''
        SELECT id, user_id, permissions, rate_limit, expires_at, enabled
        FROM api_keys
        WHERE api_key = ?
        ''',
        (api_key,)
    )
    key_info = cur.fetchone()

    if not key_info:
        conn.close()
        return {'valid': False, 'error': 'Invalid API key'}

    if not key_info['enabled']:
        conn.close()
        return {'valid': False, 'error': 'API key disabled'}

    if key_info['expires_at']:
        expires_at = datetime.fromisoformat(key_info['expires_at'])
        if datetime.now() > expires_at:
            conn.close()
            return {'valid': False, 'error': 'API key expired'}

    one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
    cur.execute(
        '''
        SELECT COUNT(*) as request_count
        FROM api_request_log
        WHERE api_key_id = ? AND created_at > ?
        ''',
        (key_info['id'], one_hour_ago)
    )

    count = cur.fetchone()['request_count']
    if count >= key_info['rate_limit']:
        conn.close()
        return {'valid': False, 'error': 'Rate limit exceeded'}

    cur.execute('UPDATE api_keys SET last_used = ? WHERE id = ?', (datetime.now().isoformat(), key_info['id']))
    conn.commit()
    conn.close()

    return {
        'valid': True,
        'key_id': key_info['id'],
        'user_id': key_info['user_id'],
        'permissions': json.loads(key_info['permissions']) if key_info['permissions'] else [],
    }


def log_api_request(api_key_id, endpoint, method, response_code, request_time_ms):
    conn = get_db()
    cur = conn.cursor()

    ip_address = request.remote_addr if request else None
    user_agent = request.headers.get('User-Agent') if request else None

    cur.execute(
        '''
        INSERT INTO api_request_log
        (api_key_id, endpoint, method, ip_address, user_agent, response_code, request_time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (api_key_id, endpoint, method, ip_address, user_agent, response_code, request_time_ms)
    )

    conn.commit()
    conn.close()


def require_api_key(required_permission=None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            start_time = time.time()

            auth_header = request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                return jsonify({'error': 'Missing or invalid Authorization header'}), 401

            api_key = auth_header.replace('Bearer ', '')
            validation = validate_api_key(api_key)

            if not validation['valid']:
                log_api_request(None, request.path, request.method, 401, (time.time() - start_time) * 1000)
                return jsonify({'error': validation['error']}), 401

            if required_permission and required_permission not in validation['permissions']:
                log_api_request(validation['key_id'], request.path, request.method, 403, (time.time() - start_time) * 1000)
                return jsonify({'error': 'Insufficient permissions'}), 403

            request.api_key_id = validation['key_id']
            request.api_user_id = validation['user_id']

            response = f(*args, **kwargs)

            response_code = response[1] if isinstance(response, tuple) else 200
            log_api_request(validation['key_id'], request.path, request.method, response_code, (time.time() - start_time) * 1000)

            return response

        return decorated_function

    return decorator
