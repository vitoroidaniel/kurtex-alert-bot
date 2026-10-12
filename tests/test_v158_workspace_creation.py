"""New website report fields, Telegram parity, and backward compatibility."""
import tempfile,unittest,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.storage import case_store
class WebsiteReportCreationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  self.old=case_store.CASES_FILE
  case_store.CASES_FILE=Path(self.tmp.name)/'cases.json'
  case_store._save(case_store.CASES_FILE,[])
  self.actor={'id':42,'role':'agent','name':'Agent'}
 def tearDown(self):
  case_store.CASES_FILE=self.old
  self.tmp.cleanup()
 def create(self,**overrides):
  payload={'id':'case-'+str(overrides.get('request_id','a')),'unit_number':'R193','driver':'Valentina','issue':'Temp not going down',
    'vehicle_type':'reefer','priority':'medium','request_id':'a','group':'','load_type':'Broker Load',
    'pickup':'Chicago 10 AM','delivery':'Des Moines','location':'Iowa','setpoint':'-10 C',
    'current_temp':'5 C','temp_recorder':'Yes','part_number':'P-123','comments':'Monitor',
    'target_agent':{'id':42,'name':'Agent','username':'agent'}}
  payload.update(overrides)
  return case_store.workspace_write(self.actor,payload)
 def test_loaded_reefer_keeps_optional_fields(self):
  c=self.create()
  for field,value in [('load_type','Broker Load'),('pickup','Chicago 10 AM'),('delivery','Des Moines'),('setpoint','-10 C'),('current_temp','5 C'),('temp_recorder','Yes'),('part_number','P-123')]:
   self.assertEqual(c[field],value)
  self.assertEqual(c['group_name'],'')
  self.assertEqual(c['report_data']['load'],'Broker Load')
 def test_empty_clears_when_edited(self):
  c=self.create()
  changed=case_store.workspace_write(self.actor,{'action':'edit','revision':case_store.workspace_revision(c),'fields':{'load_type':'Empty'}},c['id'])
  self.assertEqual(changed['load_type'],'Empty')
  for field in ('pickup','delivery','setpoint','current_temp','temp_recorder'):
   self.assertEqual(changed[field],'')
   self.assertEqual(changed['report_data'][field],'')
 def test_change_equipment_clears_reefer_temps(self):
  c=self.create()
  changed=case_store.workspace_write(self.actor,{'action':'edit','revision':case_store.workspace_revision(c),'fields':{'vehicle_type':'truck'}},c['id'])
  self.assertEqual(changed['setpoint'],'')
  self.assertEqual(changed['current_temp'],'')
  self.assertEqual(changed['temp_recorder'],'')
 def test_api_contract(self):
  s=(ROOT/'api/server.py').read_text()
  for field in ('"part_number"','"load_type"','"setpoint"','"temp_recorder"','"pickup"','"delivery"'):
   self.assertIn(field,s)
  self.assertIn('if payload["load_type"] == "Empty":',s)
  self.assertIn('"", "JBS Load", "Broker Load", "OpenDeck", "Empty"',s)
if __name__=='__main__':unittest.main()
