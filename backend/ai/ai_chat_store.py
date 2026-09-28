"""Per-user SQLite chat storage with one-time legacy import and external media."""
import base64
import copy
import hashlib
import json
import sqlite3
from pathlib import Path

class ChatStore:
    def __init__(self, root):
        self.root=Path(root)
        self.media=self.root/'ai_media'
        self.media.mkdir(parents=True,exist_ok=True)
        self.db=self.root/'ai_chats.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, chats TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS metadata (id TEXT PRIMARY KEY)')
            if not db.execute("SELECT 1 FROM metadata WHERE id='legacy_import'").fetchone():
                legacy=self.root/'ai_chats.json'
                data=json.loads(legacy.read_text()) if legacy.exists() else {}
                if not isinstance(data,dict):raise ValueError('Invalid legacy chat store; refusing to overwrite')
                for key,chats in data.items():
                    db.execute('INSERT OR IGNORE INTO users VALUES (?,?)',(key,json.dumps(self.externalize(chats))))
                db.execute("INSERT INTO metadata VALUES ('legacy_import')")
    def connect(self):
        return sqlite3.connect(self.db,timeout=10)
    def put_blob(self,raw):
        digest=hashlib.sha256(raw).hexdigest()
        path=self.media/digest
        if not path.exists():path.write_bytes(raw)
        return digest
    def blob_path(self,digest):
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('Invalid media reference')
        return self.media/digest
    def externalize(self,chats):
        chats=copy.deepcopy(chats)
        for chat in chats:
            for item in chat.get('attachments',[]):
                for media in [item]+item.get('frames',[]):
                    value=media.pop('data',None) or media.pop('raw_b64',None)
                    if value:media['blob']=self.put_blob(base64.b64decode(value.split(',',1)[-1]))
        return chats
    def all_chats(self):
        with self.connect() as db:
            for key,content in db.execute('SELECT id,chats FROM users'):
                yield key,json.loads(content)
    def read(self,key):
        with self.connect() as db:
            row=db.execute('SELECT chats FROM users WHERE id=?',(key,)).fetchone()
        return json.loads(row[0]) if row else []
    def save(self,key,chats):
        content=json.dumps(self.externalize(chats[-100:]),ensure_ascii=False,separators=(',',':'))
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO users VALUES (?,?)',(key,content))
    def hydrate(self,item):
        item=dict(item)
        if item.get('kind')=='video':
            item['frames']=[self.hydrate(frame) for frame in item.get('frames',[])]
            return item
        if item.get('blob'):
            digest=item['blob']
            if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('Invalid media reference')
            raw=base64.b64encode((self.media/digest).read_bytes()).decode('ascii')
            if item.get('kind')=='image':item['data']='data:'+item['mime']+';base64,'+raw
            else:item['raw_b64']=raw
        return item
