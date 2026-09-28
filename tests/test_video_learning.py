import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import imageio_ffmpeg
from test_maintenance import d
from backend.ai.ai_video import process_video
from backend.ai.ai_learning import LearningStore
from backend.ai.ai_research import technical_query,source_type,search_research

class VideoLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.path=Path(cls.temp.name,'sample.mp4')
        cmd=[imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=gray:s=160x120:r=12','-f','lavfi','-i','sine=frequency=440:sample_rate=16000','-t','2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-y',str(cls.path)]
        subprocess.run(cmd,check=True,capture_output=True,timeout=20)
        cls.raw=cls.path.read_bytes()
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        self.client=d.app.test_client()
        with self.client.session_transaction() as session:session['user']={'id':self.id(),'role':'developer'}
    def test_real_video_decode_and_audio(self):
        result=process_video(self.raw,'.mp4',transcriber=lambda wav:'Driver reports a cracked rim and vibration.')
        self.assertEqual(len(result['frames']),8);self.assertEqual(result['audio_status'],'transcribed')
        self.assertTrue(result['_audio_bytes'].startswith(b'RIFF'))
        self.assertTrue(all(frame['timestamp']<result['duration'] for frame in result['frames']))
    def test_speech_failure_keeps_visual_evidence(self):
        def offline(raw):raise RuntimeError('offline')
        result=process_video(self.raw,'.mp4',transcriber=offline)
        self.assertEqual(result['audio_status'],'failed');self.assertEqual(len(result['frames']),8);self.assertTrue(result['warning'])
    def test_invalid_video(self):
        with self.assertRaises(ValueError):process_video(b'invalid','.mp4')
    def test_video_attachment_transcript_and_diagnosis(self):
        chat=self.client.post('/api/ai/chats',json={}).json['id']
        with patch('ai_video.transcribe_audio',return_value='Driver reports vibration from the wheel.'):
            upload=self.client.post('/api/ai/chats/'+chat+'/files',data={'file':(io.BytesIO(self.raw),'wheel.mp4')})
        self.assertEqual(upload.status_code,201,upload.json);ident=upload.json['id']
        detail=self.client.get('/api/ai/chats/'+chat).json['attachments'][0]
        self.assertNotIn('frames',detail);self.assertNotIn('blob',detail)
        transcript='/api/ai/chats/'+chat+'/files/'+ident+'/transcript'
        self.assertEqual(self.client.patch(transcript,json={'transcript':'Cracked rim confirmed visible to driver'}).status_code,200)
        with patch.object(d,'_cf_ai',side_effect=['Visible rim. Search terms: cracked rim','Inspect wheel']) as model:
            answer=self.client.post('/api/ai/chat',json={'chat_id':chat,'message':'What is wrong?','attachment_ids':[ident],'web_search':False})
        self.assertEqual(answer.status_code,200,answer.json);self.assertEqual(answer.json['images_analyzed'],8)
        context=model.call_args_list[-1].args[0][1]['content']
        self.assertIn('Cracked rim confirmed visible to driver',context);self.assertIn('agent_corrected',context)
        response=self.client.get('/api/ai/chats/'+chat+'/files/'+ident)
        self.assertEqual(response.data,self.raw);response.close()
    def test_review_gate_and_revocation(self):
        with tempfile.TemporaryDirectory() as folder:
            store=LearningStore(Path(folder,'learning.sqlite3'))
            ident=store.capture('case:1','case','Rim fracture','Mechanic reported a fractured rim')
            self.assertEqual(store.matches('rim fracture'),[])
            store.review(ident,'approved','Rim fracture was confirmed by technician inspection. Replace damaged wheel per OEM procedure.','Rim fracture',['rim'],'reviewer')
            self.assertEqual(len(store.matches('rim fracture')),1)
            store.review(ident,'rejected','','Rim fracture',[],'reviewer');self.assertEqual(store.matches('rim fracture'),[])
    def test_changed_source_invalidates_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            store=LearningStore(Path(folder,'learning.sqlite3'));ident=store.capture('case:1','case','wheel','original')
            store.review(ident,'approved','Verified wheel findings from technician inspection','wheel',[],'reviewer')
            self.assertEqual(store.capture('case:1','case','wheel','changed evidence'),ident)
            self.assertEqual(store.matches('wheel'),[]);self.assertEqual(store.list()['total'],1)
    def test_feedback_owner_and_review_role(self):
        chat=self.client.post('/api/ai/chats',json={}).json['id']
        with patch.object(d,'_cf_ai',return_value='Inspect rim'):
            answer=self.client.post('/api/ai/chat',json={'chat_id':chat,'message':'broken rim','web_search':False}).json
        result=self.client.post('/api/ai/chats/'+chat+'/feedback',json={'message_id':answer['message_id'],'correction':'Technician confirmed fracture at the rim flange.'})
        self.assertEqual(result.status_code,201)
        with self.client.session_transaction() as session:session['user']={**session['user'],'role':'agent'}
        self.assertEqual(self.client.get('/api/ai/learning').status_code,403)
        self.assertEqual(self.client.patch('/api/ai/learning/'+result.json['id'],json={'status':'approved'}).status_code,403)
    def test_research_query_and_source_classification(self):
        query=technical_query('Driver John john@example.com phone 5551234567 reports Cummins SPN 123 FMI 4 low fuel pressure')
        self.assertNotIn('John',query);self.assertNotIn('example',query);self.assertNotIn('5551234567',query);self.assertIn('SPN 123 FMI 4',query)
        self.assertEqual(source_type('https://www.cummins.com/support'),'manufacturer / official')
        self.assertIn('community',source_type('https://www.reddit.com/r/truckers'))
        with patch.dict('os.environ',{'SERPER_API_KEY':''}):self.assertEqual(search_research('rim crack')['status'],'not_configured')
if __name__=='__main__':unittest.main()
