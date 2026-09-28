import base64
import io
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
TEMP=tempfile.TemporaryDirectory()
os.environ['DATA_DIR']=TEMP.name
os.environ['DASHBOARD_SECRET']='local-test-only'
Path(TEMP.name,'cases.json').write_text('[]')
import dashboard as d
from backend.ai.ai_chat_store import ChatStore
from backend.ai.maintenance_ai import ranked_cases
from PIL import Image

class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.client=d.app.test_client()
        with self.client.session_transaction() as session:session['user']={'id':self.id(),'role':'developer'}
    def chat(self):return self.client.post('/api/ai/chats',json={}).json['id']
    def image(self,chat,name):
        b=io.BytesIO();Image.new('RGB',(32,32),'gray').save(b,'PNG');b.seek(0)
        r=self.client.post('/api/ai/chats/'+chat+'/files',data={'file':(b,name)})
        self.assertEqual(r.status_code,201);return r.json['id']
    def test_all_selected_images_reach_both_passes(self):
        chat=self.chat();ids=[self.image(chat,'one.png'),self.image(chat,'two.png')]
        self.image(chat,'unselected.png')
        with patch.object(d,'_cf_ai',side_effect=['Visible rim fracture. Search terms: cracked rim','Inspect rim']) as model,patch.object(d,'_ai_web_search') as web:
            r=self.client.post('/api/ai/chat',json={'chat_id':chat,'message':'What is wrong?','attachment_ids':ids,'web_search':False})
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.json['images_analyzed'],2)
        self.assertEqual([len(c.kwargs['images']) for c in model.call_args_list],[2,2])
        web.assert_not_called()
    def test_invalid_image_rejected(self):
        r=self.client.post('/api/ai/chats/'+self.chat()+'/files',data={'file':(io.BytesIO(b'not an image'),'bad.png')})
        self.assertEqual(r.status_code,400)
    def test_missing_attachment_and_unknown_chat(self):
        self.assertEqual(self.client.post('/api/ai/chat',json={'chat_id':self.chat(),'message':'rim','attachment_ids':['bad']}).status_code,400)
        self.assertEqual(self.client.post('/api/ai/chat',json={'chat_id':'bad','message':'rim'}).status_code,404)
    def test_other_user_cannot_read_chat(self):
        chat=self.chat();other=d.app.test_client()
        with other.session_transaction() as session:session['user']={'id':'other'}
        self.assertEqual(other.get('/api/ai/chats/'+chat).status_code,404)
    def test_retrieval_no_random_fallback(self):
        cases=[{'id':'1','issue_text':'routine engine oil change'},{'id':'2','issue_text':'broken rim flange'}]
        self.assertEqual([c['id'] for c in ranked_cases('cracked rim',cases)],['2'])
        self.assertEqual(ranked_cases('compressor overheating',cases),[])
    def test_migration_media_and_no_resurrection(self):
        with tempfile.TemporaryDirectory() as root:
            legacy={'a':[{'id':'x','attachments':[{'kind':'image','mime':'image/png','data':'data:image/png;base64,'+base64.b64encode(b'abc').decode()}]}]}
            Path(root,'ai_chats.json').write_text(json.dumps(legacy))
            store=ChatStore(root);item=store.read('a')[0]['attachments'][0]
            self.assertNotIn('data',item);self.assertTrue(store.hydrate(item)['data'].endswith('YWJj'))
            store.save('a',[]);self.assertEqual(ChatStore(root).read('a'),[])
    def test_ai_failure_does_not_save_partial_turn(self):
        chat=self.chat()
        with patch.object(d,'_cf_ai',side_effect=RuntimeError('offline')):
            self.assertEqual(self.client.post('/api/ai/chat',json={'chat_id':chat,'message':'rim broken'}).status_code,503)
        self.assertEqual(self.client.get('/api/ai/chats/'+chat).json['messages'],[])
    def test_same_user_busy(self):
        with self.client.session_transaction() as session:key=str(session['user']['id'])
        import hashlib
        lock=d._ai_user_locks[int(hashlib.sha256(key.encode()).hexdigest(),16)%64]
        lock.acquire()
        try:self.assertEqual(self.client.post('/api/ai/chat',json={'message':'rim'}).status_code,409)
        finally:lock.release()
    def test_retry_does_not_duplicate_saved_answer(self):
        chat=self.chat();body={'chat_id':chat,'message':'rim fractured','request_id':'retry-123','web_search':False}
        with patch.object(d,'_cf_ai',return_value='Inspect the rim') as model:
            first=self.client.post('/api/ai/chat',json=body)
            retry=self.client.post('/api/ai/chat',json=body)
        self.assertEqual(first.status_code,200);self.assertTrue(retry.json['replayed'])
        self.assertEqual(model.call_count,1)
        self.assertEqual(len(self.client.get('/api/ai/chats/'+chat).json['messages']),2)
    def test_case_cache_invalidates_and_copies(self):
        from backend.storage import case_store as store
        path=Path(TEMP.name,'cache-test.json');path.write_text('[{"id":"a"}]')
        result=store._load(path);result[0]['id']='bad'
        self.assertEqual(store._load(path)[0]['id'],'a')
        store._save(path,[{'id':'b'}]);self.assertEqual(store._load(path)[0]['id'],'b')
if __name__=='__main__':unittest.main()
