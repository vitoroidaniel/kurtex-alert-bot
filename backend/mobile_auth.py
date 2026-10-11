"""Kurtex Mobile: Telegram browser sign-in with PKCE and revocable bearer tokens.

The existing dashboard remains the only data API. This module adds a mobile
login bridge, not a second user database or a second backend.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlencode
from flask import g, jsonify, redirect, request, session
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

CALLBACK = 'kurtex://auth'
TOKEN_DAYS = 30


def _challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode('ascii').rstrip('=')


def register_mobile_auth(app, data_dir, allowed_pages):
    path = Path(data_dir) / 'mobile_auth.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    signer = URLSafeTimedSerializer(app.secret_key, salt='kurtex-mobile-auth-code-v1')

    def db():
        con = sqlite3.connect(str(path), timeout=10)
        con.execute('PRAGMA busy_timeout=10000')
        con.execute('CREATE TABLE IF NOT EXISTS mobile_tokens (hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires INTEGER NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS mobile_codes (jti TEXT PRIMARY KEY, expires INTEGER NOT NULL)')
        return con

    with db() as con:
        con.execute('CREATE INDEX IF NOT EXISTS mobile_tokens_expiry ON mobile_tokens(expires)')

    def complete_login(user_id, pending):
        # Called ONLY after Telegram's existing HMAC verification and membership check.
        state = pending.get('state', '')
        challenge = pending.get('challenge', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', state) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', challenge):
            return redirect('/login?error=mobile')
        jti = secrets.token_urlsafe(24)
        code = signer.dumps({'uid': int(user_id), 'state': state, 'challenge': challenge, 'jti': jti})
        with db() as con:
            con.execute('DELETE FROM mobile_codes WHERE expires < ?', (int(time.time()),))
            con.execute('INSERT INTO mobile_codes (jti,expires) VALUES (?,?)', (jti, int(time.time()) + 180))
        return redirect(CALLBACK + '?' + urlencode({'code': code, 'state': state}))

    @app.before_request
    def mobile_authenticate():
        if not request.path.startswith('/api/'):
            return
        authorization = request.headers.get('Authorization', '')
        if not authorization.startswith('Bearer '):
            return
        token = authorization[7:].strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{40,128}', token):
            return jsonify({'error': 'Invalid mobile token'}), 401
        digest = hashlib.sha256(token.encode()).hexdigest()
        with db() as con:
            row = con.execute('SELECT user_id FROM mobile_tokens WHERE hash=? AND expires>?', (digest, int(time.time()))).fetchone()
        if row is None:
            return jsonify({'error': 'Session expired. Sign in again.'}), 401
        from backend.storage.user_store import get_user
        stored = get_user(int(row[0]))
        if not stored:
            with db() as con:
                con.execute('DELETE FROM mobile_tokens WHERE hash=?', (digest,))
            return jsonify({'error': 'Account no longer authorized'}), 403
        # Permissions are always re-evaluated by the existing dashboard routes.
        session['user'] = {
            'id': int(row[0]), 'first_name': stored.get('first_name') or stored.get('name') or '',
            'username': stored.get('username') or '', 'role': stored.get('role', 'agent'),
            'photo_url': stored.get('photo_url') or '',
        }
        # Apply navigation permissions at the API boundary for native clients.
        page = None
        path = request.path
        if path.startswith('/api/workspace/') or path in ('/api/cases', '/api/case'):
            page = 'cases'
        elif path in ('/api/fleet', '/api/unit', '/api/issue_search'):
            page = 'fleet'
        elif path.startswith('/api/ai/chats') or path in ('/api/ai/chat', '/api/ai/status', '/api/ai/knowledge/options'):
            page = 'ai_assistant'
        elif path in ('/api/part_search', '/api/part_image'):
            page = 'parts_manual'
        elif path.startswith('/api/agents') or path == '/api/agent':
            page = 'agents'
        elif path.startswith('/api/developer/'):
            page = 'developer'
        if page and page not in allowed_pages():
            return jsonify({'error': 'This feature is restricted for your account.'}), 403
        g.mobile_authenticated = True
        g.mobile_token_hash = digest

    @app.route('/mobile/login')
    def mobile_login_start():
        challenge = request.args.get('challenge', '')
        state = request.args.get('state', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', challenge) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', state):
            return 'Invalid mobile login request.', 400
        session['mobile_oauth'] = {'challenge': challenge, 'state': state, 'ts': time.time()}
        return redirect('/login')

    @app.route('/api/mobile/token', methods=['POST'])
    def mobile_token_exchange():
        data = request.get_json(silent=True) or {}
        verifier = str(data.get('verifier') or '')
        code = str(data.get('code') or '')
        state = str(data.get('state') or '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', verifier) or len(code) > 1500:
            return jsonify({'error': 'Invalid exchange request'}), 400
        try:
            payload = signer.loads(code, max_age=180)
        except (BadSignature, SignatureExpired):
            return jsonify({'error': 'Login link expired. Try again.'}), 401
        if not hmac.compare_digest(payload.get('challenge', ''), _challenge(verifier)) or not hmac.compare_digest(payload.get('state', ''), state):
            return jsonify({'error': 'Login verification failed.'}), 403
        from backend.storage.user_store import get_user
        uid = int(payload.get('uid', 0))
        stored = get_user(uid)
        if not stored:
            return jsonify({'error': 'Account no longer authorized.'}), 403
        jti = str(payload.get('jti', ''))
        token = secrets.token_urlsafe(48)
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = int(time.time())
        with db() as con:
            # Atomic one-time exchange: second use cannot mint a new token.
            deleted = con.execute('DELETE FROM mobile_codes WHERE jti=? AND expires>=?', (jti, now)).rowcount
            if deleted != 1:
                return jsonify({'error': 'Login link already used or expired.'}), 401
            con.execute('DELETE FROM mobile_tokens WHERE expires < ?', (now,))
            con.execute('INSERT INTO mobile_tokens (hash,user_id,expires) VALUES (?,?,?)', (digest, uid, now + TOKEN_DAYS * 86400))
        return jsonify({'access_token': token, 'expires_in': TOKEN_DAYS * 86400, 'token_type': 'Bearer'})

    @app.route('/api/mobile/session')
    def mobile_session():
        if not getattr(g, 'mobile_authenticated', False):
            return jsonify({'error': 'Mobile login required.'}), 401
        return jsonify({'user': session['user'], 'allowed_pages': allowed_pages()})

    @app.route('/api/mobile/logout', methods=['POST'])
    def mobile_logout():
        digest = getattr(g, 'mobile_token_hash', None)
        if digest:
            with db() as con:
                con.execute('DELETE FROM mobile_tokens WHERE hash=?', (digest,))
        session.clear()
        return jsonify({'ok': True})

    return complete_login
