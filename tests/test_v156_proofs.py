"""Offline regression tests for case proof metadata and authorization."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.storage import case_store

class ProofStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=case_store.CASES_FILE
        case_store.CASES_FILE=Path(self.tmp.name)/'cases.json'
        case_store._save(case_store.CASES_FILE,[{'id':'case-proof-1','unit_number':'R193','issue_text':'Reefer temperature',
            'status':'reported','agent_id':42,'workspace_attachments':[{'id':'old-file','name':'old.png','extension':'.png'}]}])
        self.actor={'id':42,'role':'agent','name':'Alex'}
    def tearDown(self):
        case_store.CASES_FILE=self.old
        self.tmp.cleanup()
    def test_proofs_separate_from_legacy_attachments_and_audited(self):
        item={'id':'proof-a','name':'invoice.pdf','extension':'.pdf','category':'proof','proof_type':'invoice',
              'description':'Reefer repair','created_at':'2026-10-12T12:00:00Z','uploaded_by':'Alex','size':2048}
        case_store.workspace_attachment_write(self.actor,'case-proof-1',attachment=item)
        case,stored=case_store.workspace_attachment_lookup('case-proof-1','proof-a')
        self.assertEqual(stored['proof_type'],'invoice')
        self.assertEqual(stored['description'],'Reefer repair')
        self.assertEqual(len([a for a in case['workspace_attachments'] if a.get('category')=='proof']),1)
        self.assertEqual(len([a for a in case['workspace_attachments'] if a.get('category')!='proof']),1)
        self.assertEqual(case['workspace_history'][-1]['action'],'proof_added')
        case_store.workspace_attachment_write(self.actor,'case-proof-1',remove_id='proof-a')
        case,_=case_store.workspace_attachment_lookup('case-proof-1','old-file')
        self.assertEqual(case['workspace_history'][-1]['action'],'proof_removed')
        self.assertEqual([a['id'] for a in case['workspace_attachments']],['old-file'])
    def test_other_agent_cannot_upload_or_delete(self):
        outsider={'id':99,'role':'agent','name':'Other'}
        with self.assertRaises(PermissionError):
            case_store.workspace_attachment_write(outsider,'case-proof-1',attachment={'id':'nope','category':'proof'})
        with self.assertRaises(PermissionError):
            case_store.workspace_attachment_write(outsider,'case-proof-1',remove_id='old-file')
    def test_api_validation_contract(self):
        source=(ROOT/'api/server.py').read_text()
        for expected in ('"category"','"proof_type"','"repair_proof"','"invoice"','"receipt"','"description"',
                         'if category not in ("attachment", "proof")', 'if len(content)>15*1024*1024'):
            self.assertIn(expected,source)

if __name__=='__main__':unittest.main()
