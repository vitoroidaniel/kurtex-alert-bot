"""Bounded local video decoding plus Cloudflare speech transcription."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from threading import BoundedSemaphore
import urllib.request

VIDEO_SLOTS=BoundedSemaphore(2)
MAX_VIDEO_BYTES=80*1024*1024

def transcribe_audio(raw):
    account=os.getenv('CLOUDFLARE_ACCOUNT_ID','').strip()
    token=os.getenv('CLOUDFLARE_API_TOKEN','').strip()
    if not account or not token:raise RuntimeError('Speech transcription is not configured')
    model=os.getenv('KURTEX_SPEECH_MODEL','@cf/openai/whisper-large-v3-turbo')
    url=f'https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}'
    body={'audio':base64.b64encode(raw).decode('ascii'),'task':'transcribe','vad_filter':True}
    request=urllib.request.Request(url,data=json.dumps(body).encode(),headers={
        'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(request,timeout=45) as response:payload=json.load(response)
    if not payload.get('success',True):raise RuntimeError('Speech provider rejected the request')
    result=payload.get('result') or {}
    return str(result.get('text') or '').strip()[:16000]

def process_video(raw, suffix, transcriber=None):
    if not raw or len(raw)>MAX_VIDEO_BYTES:raise ValueError('Video must be under 80 MB')
    if not VIDEO_SLOTS.acquire(blocking=False):raise ValueError('Video processing is busy. Try again shortly.')
    try:
        import imageio_ffmpeg
        ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory(prefix='kurtex-video-') as folder:
            folder=Path(folder);source=folder/('input'+suffix);source.write_bytes(raw)
            common=[ffmpeg,'-nostdin','-hide_banner','-threads','1','-protocol_whitelist','file,pipe','-format_whitelist','mov,matroska,webm']
            probe=subprocess.run(common+['-i',str(source)],capture_output=True,timeout=15)
            metadata=probe.stderr.decode('utf-8','replace')
            match=re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)',metadata)
            if not match or 'Video:' not in metadata:raise ValueError('Video cannot be decoded. Try MP4/H.264.')
            hours,minutes,seconds=map(float,match.groups());duration=hours*3600+minutes*60+seconds
            if not 0<duration<=120:raise ValueError('Use a video up to 2 minutes.')
            frames=[]
            # Eight views across the full clip. Never claim continuous-video coverage.
            for i in range(8):
                timestamp=round(duration*(i+.5)/8,3);target=folder/f'frame-{i}.jpg'
                command=common+['-ss',str(timestamp),'-i',str(source),'-an','-frames:v','1','-vf',
                    'scale=1280:1280:force_original_aspect_ratio=decrease','-q:v','3','-y',str(target)]
                result=subprocess.run(command,capture_output=True,timeout=12)
                if result.returncode or not target.exists():raise ValueError('Unable to sample this video. Try MP4/H.264.')
                frames.append({'kind':'image','mime':'image/jpeg','name':f'Video frame {timestamp:.2f}s',
                    'timestamp':timestamp,'data':'data:image/jpeg;base64,'+base64.b64encode(target.read_bytes()).decode()})
            audio_status='no_audio';transcript='';warning='';audio_bytes=None
            if 'Audio:' in metadata:
                wav=folder/'speech.wav'
                result=subprocess.run(common+['-i',str(source),'-vn','-t','120','-ac','1','-ar','16000','-y',str(wav)],capture_output=True,timeout=25)
                if result.returncode or not wav.exists():
                    audio_status='failed';warning='Audio extraction failed. Add the driver explanation as text.'
                else:
                    audio_bytes=wav.read_bytes()
                    try:
                        transcript=(transcriber or transcribe_audio)(audio_bytes)
                        audio_status='transcribed' if transcript else 'no_speech'
                    except Exception:
                        audio_status='failed';warning='Speech transcription failed. Retry transcription or type the driver explanation.'
            return {'kind':'video','mime':{'.mov':'video/quicktime','.webm':'video/webm'}.get(suffix,'video/mp4'),
                'duration':duration,'frames':frames,'_audio_bytes':audio_bytes,'transcript':transcript,'audio_status':audio_status,'warning':warning}
    except (subprocess.TimeoutExpired,FileNotFoundError) as exc:
        raise ValueError('Video processing timed out or decoder is unavailable. Try a shorter MP4.') from exc
    finally:VIDEO_SLOTS.release()
