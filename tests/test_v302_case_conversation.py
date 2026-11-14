"""Collaborative case conversations: permission, history, reply, pin and files."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.storage import case_store, db


class CaseConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.patch_dir = patch.object(case_store, '_storage_dir', return_value=self.data_dir)
        self.patch_dir.start()
        self.addCleanup(self.patch_dir.stop)
        self.daniel = {'id':101, 'name':'Daniel','role':'agent', 'username':'danikav'}
        self.andrei = {'id':102, 'name':'Andrei','role':'agent', 'username':'andrei'}
        self.manager = {'id':103, 'name':'Manager','role':'super_admin'}
        with db.write_tx(self.data_dir) as conn:
            for person in (self.daniel,self.andrei,self.manager):
                conn.execute('INSERT INTO users(id,name,username,role) VALUES (?,?,?,?)',
                             (person['id'],person['name'],person.get('username',''),person['role']))
        data = {'id':'case-101','request_id':'req-101','driver':'Driver A','unit_number':'R481',
                'issue':'Air leak','vehicle_type':'trailer','priority':'normal'}
        self.case=case_store.workspace_write(self.daniel, data)

    def act(self, actor, action, **payload):
        c = case_store.get_case('case-101')
        return case_store.workspace_write(actor,{'action':action,'revision':case_store.workspace_revision(c), **payload},'case-101')

    def test_reply_emoji_mentions_and_notification(self):
        c = self.act(self.daniel,'note',text='Hi @andrei 🔧')
        root=c['workspace_notes'][0]
        self.assertEqual(root['author_id'],'101')
        self.assertEqual(root['mentions'],[{'id':'102','name':'Andrei'}])
        self.assertEqual(root['text'],'Hi @andrei 🔧')
        with db.read_conn(self.data_dir) as conn:
            notifications=conn.execute('SELECT user_key,data_json FROM notifications').fetchall()
        self.assertEqual(len(notifications),1)
        self.assertEqual(notifications[0]['user_key'],'102')
        reply=self.act(self.andrei,'note',text='Confirmed ✅',reply_to_id=root['id'])['workspace_notes'][-1]
        self.assertEqual(reply['reply_to_id'],root['id'])

    def test_author_only_edit_delete_with_audit_and_tombstone(self):
        c=self.act(self.daniel,'note',text='Original message')
        nid=c['workspace_notes'][0]['id']
        with self.assertRaises(PermissionError):self.act(self.andrei,'note_edit',note_id=nid,text='Hacked')
        with self.assertRaises(PermissionError):self.act(self.andrei,'note_delete',note_id=nid)
        edited=self.act(self.daniel,'note_edit',note_id=nid,text='Edited')
        self.assertEqual(edited['workspace_notes'][0]['text'],'Edited')
        self.assertTrue(edited['workspace_notes'][0].get('edited_at'))
        deleted=self.act(self.daniel,'note_delete',note_id=nid)
        self.assertEqual(len(deleted['workspace_notes']),1)
        self.assertEqual(deleted['workspace_notes'][0]['text'],'')
        self.assertTrue(deleted['workspace_notes'][0]['deleted_at'])
        acts=[h['action'] for h in deleted['workspace_history']]
        self.assertEqual(acts.count('note'),1)
        self.assertIn('note_edit',acts);self.assertIn('note_delete',acts)
        self.assertEqual(deleted['workspace_history'][-1]['author'],'Daniel')

    def test_pin_requires_owner_or_manager(self):
        c=self.act(self.daniel,'note',text='Important')
        nid=c['workspace_notes'][0]['id']
        with self.assertRaises(PermissionError):self.act(self.andrei,'note_pin',note_id=nid,pinned=True)
        pinned=self.act(self.daniel,'note_pin',note_id=nid,pinned=True)
        self.assertTrue(pinned['workspace_notes'][0]['pinned'])
        self.assertEqual(pinned['workspace_history'][-1]['action'],'note_pin')
        self.assertFalse(self.act(self.manager,'note_pin',note_id=nid,pinned=False)['workspace_notes'][0]['pinned'])

    def test_file_links_must_exist_on_same_case(self):
        with self.assertRaises(ValueError):self.act(self.daniel,'note',text='See file',file_ids=['0'*32])
        file={'id':'a'*32,'name':'repair.jpg','extension':'.jpg','uploaded_by_id':'101'}
        case_store.workspace_attachment_write(self.andrei,'case-101',attachment=file)
        note=self.act(self.daniel,'note',text='',file_ids=['a'*32])['workspace_notes'][0]
        self.assertEqual(note['file_ids'],['a'*32])
        with self.assertRaises(PermissionError):case_store.workspace_attachment_write(self.andrei,'case-101',remove_id='a'*32)
        case_store.workspace_attachment_write(self.daniel,'case-101',remove_id='a'*32)

    def test_conflicts_do_not_overwrite_other_agent(self):
        before=case_store.get_case('case-101')
        revision=case_store.workspace_revision(before)
        self.act(self.andrei,'note',text='Latest')
        with self.assertRaises(ValueError):
            case_store.workspace_write(self.daniel,{'action':'note','revision':revision,'text':'stale','file_ids':[]},'case-101')
        self.assertEqual(len(case_store.get_case('case-101')['workspace_notes']),1)

if __name__ == '__main__':unittest.main()
