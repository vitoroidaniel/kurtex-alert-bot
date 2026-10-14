"""WebSocket security, cross-process detection and fallback contracts."""
import unittest
from pathlib import Path
from itsdangerous import URLSafeTimedSerializer, BadSignature

ROOT=Path(__file__).resolve().parents[1]
class RealtimeContractTests(unittest.TestCase):
    def test_ticket_cannot_be_forged_or_reused_with_different_secret(self):
        issuer=URLSafeTimedSerializer('test-secret-A',salt='kurtex-workspace-ws-v1')
        other=URLSafeTimedSerializer('test-secret-B',salt='kurtex-workspace-ws-v1')
        token=issuer.dumps({'uid':'123','origin':'https://desktop.example.com'})
        self.assertEqual(issuer.loads(token,max_age=45)['uid'],'123')
        with self.assertRaises(BadSignature):other.loads(token,max_age=45)
    def test_websocket_is_read_only_and_checks_membership(self):
        source=(ROOT/'api/server.py').read_text()
        for snippet in ('@sock.route("/ws/workspace")', 'max_age=45', 'request.headers.get("Origin")',
                        'authorized_actor()', '"cases" not in _load_role_permissions()',
                        '_workspace_board_version(actor)', 'ws.receive(timeout=0.75)',
                        '"type": "changed"', '"type": "heartbeat"'):
            self.assertIn(snippet,source)
        self.assertNotIn('ws.receive()\n    workspace_write',source)
    def test_gateway_uses_ticket_not_cross_origin_cookie(self):
        for folder in ('desktop','mobile'):
            source=(ROOT.parent/folder/'web.py').read_text()
            self.assertIn("@app.route('/realtime/connect', methods=['POST'])",source)
            self.assertIn("'/api/workspace/realtime-ticket'",source)
            self.assertIn("'/ws/workspace'",source)
        self.assertIn('flask-sock==0.7.0',(ROOT/'requirements.txt').read_text())
        self.assertIn('gunicorn>=22,<24',(ROOT/'requirements.txt').read_text())
        self.assertIn('\"--threads\", \"32\"',(ROOT/'api/server.py').read_text())
if __name__=='__main__':unittest.main()
