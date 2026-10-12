"""Offline regression checks for Kurtex AI v147. No Cloudflare calls or Telegram bot startup."""
import unittest
import subprocess
import tempfile
from pathlib import Path
from backend.ai.ai_intent import detect_language, classify_task, response_policy, retrieval_query
from backend.ai.ai_video import process_video, MAX_VIDEO_BYTES

class TestTaskFocus(unittest.TestCase):
    def test_multilingual_routing(self):
        examples={
            'Что видно на видео?':'ru',
            'Ce vezi in acest video?':'ro',
            'Cod eroare SPN 111 FMI 4':'ro',
            '¿Qué ves en este vídeo?':'es',
            'Analiza el video por favor':'es',
            'What do you see in this video?':'en',
        }
        for text,lang in examples.items():
            with self.subTest(text=text):self.assertEqual(detect_language(text),lang)
    def test_no_unrelated_generic_sections(self):
        policy=response_policy('похожие случаи с утечкой воздуха')
        self.assertEqual(policy['task'],'history')
        self.assertEqual(policy['language'],'ru')
        self.assertIn('matching cases',policy['instructions'])
    def test_visual_distinct_from_driver_speech(self):
        policy=response_policy('Ce vezi in video?',has_video=True,transcript='The driver speaks Spanish')
        self.assertEqual(policy['task'],'video_inspection')
        self.assertEqual(policy['language'],'ro')
        self.assertIn('sampled video frames',policy['instructions'])
        self.assertIn('driver said',policy['instructions'])
    def test_retrieval_expansion(self):
        self.assertIn('air leak',retrieval_query('Утечка воздуха'))
        self.assertIn('brake',retrieval_query('Problema cu frâna'))
        self.assertIn('coolant leak',retrieval_query('Fuga de refrigerante'))
    def test_speech_does_not_change_english_question(self):
        self.assertEqual(detect_language('Analyze this video',transcript='Водитель говорит'), 'en')

class TestVideoSampling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import imageio_ffmpeg
            ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, FileNotFoundError):
            raise unittest.SkipTest('Video decoder is unavailable')
        cls.folder=tempfile.TemporaryDirectory(prefix='kurtex-ai-video-test-')
        cls.video=Path(cls.folder.name)/'video.mp4'
        cls.audio_video=Path(cls.folder.name)/'audio.mp4'
        base=[ffmpeg,'-nostdin','-y','-f','lavfi','-i','testsrc2=size=240x135:rate=12']
        args=['-t','2','-c:v','libx264','-pix_fmt','yuv420p']
        for cmd in (base+args+[str(cls.video)],base+['-f','lavfi','-i','sine=frequency=500:sample_rate=16000']+args+['-c:a','aac','-shortest',str(cls.audio_video)]):
            result=subprocess.run(cmd,capture_output=True,timeout=30)
            if result.returncode:raise unittest.SkipTest('Could not generate test video')
    @classmethod
    def tearDownClass(cls):
        if hasattr(cls,'folder'):cls.folder.cleanup()
    def test_oversize(self):
        with self.assertRaises(ValueError):process_video(b'x'*(MAX_VIDEO_BYTES+1),'.mp4')
    def test_invalid(self):
        with self.assertRaises(ValueError):process_video(b'not a video','.mp4')
    def test_video_audio_and_language_metadata(self):
        item=process_video(self.audio_video.read_bytes(),'.mp4',transcriber=lambda wav:{'text':'Воздух уходит из трубки','language':'ru'})
        self.assertEqual(item['audio_status'],'transcribed')
        self.assertEqual(item['audio_language'],'ru')
        self.assertIn('Воздух',item['transcript'])
        self.assertEqual(item['frames_sampled'],8)
    def test_eight_frames_real_ffmpeg(self):
        item=process_video(self.video.read_bytes(),'.mp4')
        self.assertEqual(item['visual_status'],'frames_ready')
        self.assertEqual(item['frames_sampled'],8)
        self.assertEqual(item['audio_status'],'no_audio')
        self.assertEqual(len(item['frame_timestamps']),8)
        self.assertEqual(len(item['frames']),8)
        self.assertTrue(all(x['data'].startswith('data:image/jpeg;base64,') for x in item['frames']))

if __name__=='__main__':unittest.main()
