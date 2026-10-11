"""Short-lived one-use PKCE bridge for independently hosted Kurtex web frontends.

Telegram authentication happens ONLY at the existing bot service. A one-time code
is returned to the approved frontend, exchanged server-to-server for a Flask
session cookie, and consumed atomically. No BOT_TOKEN is given to a frontend.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlencode
from flask import jsonify, redirect, request, session
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

CLIENTS = {"desktop": "DESKTOP_PUBLIC_URL", "mobile": "MOBILE_PUBLIC_URL"}

def _challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode('ascii').rstrip('=')

def register_web_sso(app, data_dir, allowed_pages):
    signer = URLSafeTimedSerializer(app.secret_key, salt='kurtex-web-sso-v1')
    path = Path(data_dir) / 'web_sso_codes.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE IF NOT EXISTS codes (digest TEXT PRIMARY KEY, expires INTEGER NOT NULL)')

    def client_url(name):
        if name not in CLIENTS: return None
        value = os.getenv(CLIENTS[name], '').strip().rstrip('/')
        if not value.startswith('https://') or '?' in value or '#' in value or '@' in value.split('://', 1)[1].split('/')[0]:
            return None
        # Only a bare HTTPS origin, no path/query/fragment.
        from urllib.parse import urlparse
        parsed=urlparse(value)
        if parsed.path not in ('','/') or not parsed.netloc: return None
        return value

    @app.route('/auth/web/start')
    def web_start():
        client = request.args.get('client', '')
        state = request.args.get('state', '')
        challenge = request.args.get('challenge', '')
        if not client_url(client): return 'Web frontend not configured', 503
        if not re.fullmatch(r'[A-Za-z0-9_-]{24,128}', state) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', challenge):
            return 'Invalid sign-in request', 400
        session['web_sso_pending'] = {'client': client, 'state': state, 'challenge': challenge, 'created': time.time()}
        return redirect('/login')

    def complete(user_id):
        pending = session.pop('web_sso_pending', None)
        if not pending: return None
        origin = client_url(pending.get('client'))
        if not origin or time.time()-float(pending.get('created', 0)) > 600:
            return redirect('/login?error=expired')
        jti = secrets.token_urlsafe(24)
        payload = {'uid': int(user_id), 'client': pending['client'], 'state': pending['state'],
                   'challenge': pending['challenge'], 'jti': jti}
        code = signer.dumps(payload)
        digest = hashlib.sha256(jti.encode()).hexdigest()
        with sqlite3.connect(path, timeout=10) as con:
            con.execute('DELETE FROM codes WHERE expires < ?', (int(time.time()),))
            con.execute('INSERT INTO codes (digest, expires) VALUES (?,?)', (digest, int(time.time())+120))
        response = redirect(origin+'/auth/complete?'+urlencode({'code':code,'state':pending['state']}))
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.route('/api/web/exchange', methods=['POST'])
    def exchange():
        body=request.get_json(silent=True) or {}
        code=str(body.get('code',''))
        verifier=str(body.get('verifier',''))
        state=str(body.get('state',''))
        client=str(body.get('client',''))
        if not client_url(client) or len(code)>2000 or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}',verifier):
            return jsonify(error='Invalid sign-in exchange'),400
        try: payload=signer.loads(code,max_age=120)
        except (BadSignature, SignatureExpired):return jsonify(error='Expired or invalid sign-in code'),401
        if (payload.get('client')!=client or not hmac.compare_digest(str(payload.get('state','')),state)
                or not hmac.compare_digest(str(payload.get('challenge','')),_challenge(verifier))):
            return jsonify(error='Sign-in verification failed'),403
        digest=hashlib.sha256(str(payload.get('jti','')).encode()).hexdigest()
        # Atomic one-use consumption. Expired/replayed codes are rejected.
        with sqlite3.connect(path,timeout=10) as con:
            cur=con.execute('DELETE FROM codes WHERE digest=? AND expires>=?', (digest,int(time.time())))
            if cur.rowcount!=1: return jsonify(error='Sign-in code already used or expired'),401
        from backend.storage.user_store import get_user
        stored=get_user(int(payload['uid']))
        if not stored: return jsonify(error='Account no longer authorized'),403
        user={'id':int(payload['uid']),'first_name':stored.get('name') or stored.get('first_name') or '',
              'username':stored.get('username') or '', 'photo_url':'','role':stored.get('role') or 'agent'}
        # The existing backend remains the authority for sessions and permissions.
        values={'user':user,'workspace_csrf':secrets.token_urlsafe(32)}
        serializer=app.session_interface.get_signing_serializer(app)
        if not serializer: return jsonify(error='Backend session signing unavailable'),503
        return jsonify({'session_cookie':serializer.dumps(values)})

    @app.route('/api/frontend/bootstrap')
    def frontend_bootstrap():
        user=session.get('user')
        if not user: return jsonify(error='unauthorized'),401
        from backend.storage.user_store import get_user
        stored=get_user(int(user.get('id',0)))
        if not stored: return jsonify(error='Account no longer authorized'),403
        # Refresh role from backend, not a stale browser cookie.
        role=stored.get('role') or 'agent'
        user={**user,'role':role}
        session['user']=user
        if not session.get('workspace_csrf'):
            session['workspace_csrf']=secrets.token_urlsafe(32)
        manager=role in ('manager','admin','developer','super_admin')
        return jsonify({'user':user,'allowed_pages':list(allowed_pages()),'is_manager':manager,
                        'is_developer':role=='developer','workspace_csrf':session['workspace_csrf']})

    return complete
