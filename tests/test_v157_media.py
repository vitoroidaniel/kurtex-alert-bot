"""v157 Telegram media removal and preview fallback contracts (offline)."""
import sys, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.storage import case_store

class TelegramMediaStorageTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=case_store.CASES_FILE
  case_store.CASES_FILE=Path(self.tmp.name)/'cases.json'
  case_store._save(case_store.CASES_FILE,[{'id':'c1','unit_number':'R193','status':'reported','agent_id':42,
   'media':[['photo','file-a'],['video','file-b'],['document','file-c']], 'workspace_attachments':[]}])
  self.actor={'id':42,'role':'agent','name':'Agent'}
 def tearDown(self):
  case_store.CASES_FILE=self.old;self.tmp.cleanup()
 def test_delete_middle_keeps_order_and_audits(self):
  removed=case_store.workspace_telegram_media_remove(self.actor,'c1',1)
  self.assertEqual(removed,['video','file-b'])
  cases=case_store._load(case_store.CASES_FILE,strict=True)
  self.assertEqual(cases[0]['media'],[['photo','file-a'],['document','file-c']])
  self.assertEqual(cases[0]['workspace_history'][-1]['action'],'telegram_attachment_removed')
 def test_access_and_missing_index(self):
  with self.assertRaises(PermissionError):case_store.workspace_telegram_media_remove({'id':99,'role':'agent','name':'Other'},'c1',0)
  with self.assertRaises(LookupError):case_store.workspace_telegram_media_remove(self.actor,'c1',8)
  self.assertEqual(len(case_store._load(case_store.CASES_FILE,strict=True)[0]['media']),3)
 def test_server_contract(self):
  source=(ROOT/'api/server.py').read_text()
  for token in ('telegram-media/<int:index>', 'workspace_telegram_media_remove', 'telegram_file_too_large',
                'telegram_file_unavailable', 'telegram_media_cache', 'sha256(file_id.encode())'):
   self.assertIn(token,source)

if __name__=='__main__':unittest.main()
