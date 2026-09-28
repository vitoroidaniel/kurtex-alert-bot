"""Auditable learning candidates. Only reviewed summaries enter shared retrieval."""
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from backend.ai.maintenance_ai import tokens, knowledge_excerpt

class LearningStore:
    def __init__(self,path):
        self.path=path
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS lessons (
              id TEXT PRIMARY KEY, source_key TEXT UNIQUE, source_type TEXT,
              title TEXT, evidence TEXT, fingerprint TEXT, status TEXT,
              content TEXT DEFAULT '', tags TEXT DEFAULT '[]', reviewer TEXT,
              created_at TEXT, updated_at TEXT)''')
            db.execute('CREATE INDEX IF NOT EXISTS lessons_status ON lessons(status,updated_at)')
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row;return db
    def capture(self,source_key,source_type,title,evidence):
        evidence=str(evidence)[:40000];digest=hashlib.sha256(evidence.encode()).hexdigest()
        now=datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            old=db.execute('SELECT id,fingerprint FROM lessons WHERE source_key=?',(source_key,)).fetchone()
            if old and old['fingerprint']==digest:return old['id']
            ident=old['id'] if old else uuid.uuid4().hex
            if old:
                db.execute("UPDATE lessons SET title=?,evidence=?,fingerprint=?,status='pending',content='',tags='[]',reviewer=NULL,updated_at=? WHERE id=?",(title[:160],evidence,digest,now,ident))
            else:
                db.execute('INSERT INTO lessons (id,source_key,source_type,title,evidence,fingerprint,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',(ident,source_key,source_type,title[:160],evidence,digest,'pending',now,now))
            return ident
    def capture_chat(self,owner,chat):
        messages=chat.get('messages') or []
        for index,message in enumerate(messages):
            if message.get('role')!='assistant':continue
            previous=messages[index-1] if index else {}
            key=message.get('id') or message.get('at') or str(index)
            evidence='Agent report (unverified):\n'+str(previous.get('content',''))+'\n\nAI suggestion (NOT confirmed):\n'+str(message.get('content',''))
            self.capture(f'chat:{owner}:{chat["id"]}:{key}','chat',chat.get('title') or 'Maintenance conversation',evidence)
    def capture_cases(self,cases):
        count=0
        for case in cases:
            ident=case.get('id') or case.get('full_id')
            if not ident:continue
            fields={key:case.get(key) for key in ('vehicle_type','unit_number','issue_text','description','notes','status','resolution','solution','close_notes','closing_notes','resolution_notes','closed_at') if case.get(key)}
            self.capture('case:'+str(ident),'case','Case '+str(ident),json.dumps(fields,ensure_ascii=False));count+=1
        return count
    def list(self,status='pending',offset=0):
        with self.connect() as db:
            total=db.execute('SELECT count(*) FROM lessons WHERE status=?',(status,)).fetchone()[0]
            items=[dict(row) for row in db.execute('SELECT * FROM lessons WHERE status=? ORDER BY updated_at DESC LIMIT 20 OFFSET ?',(status,offset))]
            counts={row[0]:row[1] for row in db.execute('SELECT status,count(*) FROM lessons GROUP BY status')}
        return {'items':items,'total':total,'counts':counts,'offset':offset}
    def review(self,ident,status,content,title,tags,reviewer):
        if status not in ('approved','rejected','pending'):raise ValueError('Invalid review action')
        if status=='approved' and len(content.strip())<30:raise ValueError('Add a verified maintenance lesson with supporting evidence before approving.')
        now=datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            cursor=db.execute('UPDATE lessons SET status=?,content=?,title=?,tags=?,reviewer=?,updated_at=? WHERE id=?',(status,content[:12000],title[:160],json.dumps(tags[:12]),reviewer,now,ident))
            return cursor.rowcount>0
    def matches(self,query,limit=5):
        wanted=tokens(query)
        if not wanted:return []
        with self.connect() as db:
            rows=db.execute("SELECT id,title,content,tags,source_type,updated_at FROM lessons WHERE status='approved'").fetchall()
        ranked=[]
        for row in rows:
            item=dict(row);item['tags']=json.loads(item['tags']);score=len(wanted & tokens(item['title']+' '+item['content']+' '+' '.join(item['tags'])))
            if score:
                item['content']=knowledge_excerpt(item['content'],query,2200);ranked.append((score,item))
        return [item for _,item in sorted(ranked,key=lambda pair:-pair[0])[:limit]]
