"""Parser regressions: local-date boundaries, replay dedup, and growing files."""
import json
import tempfile
import unittest
from pathlib import Path
from import_sessions import bounds,parse

class ImportTests(unittest.TestCase):
 def fixture(self):
  return [
   {'timestamp':'2026-09-08T12:00:00Z','type':'session_meta','payload':{'id':'thread','timestamp':'2026-09-08T12:00:00Z'}},
   {'timestamp':'2026-09-12T23:00:00Z','type':'turn_context','payload':{'turn_id':'turn','model':'gpt-6-astra','effort':'xhigh'}},
   {'timestamp':'2026-09-13T03:30:00Z','type':'token_usage_record','payload':{'thread_id':'thread','turn_id':'turn','response_id':'resp1','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':10,'reasoning_output_tokens':4,'total_tokens':110}}},
   {'timestamp':'2026-09-13T03:30:00Z','type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'total_tokens':999999}},'rate_limits':{'primary':{'used_percent':99,'window_minutes':10080,'resets_at':1790000000}}}}
  ]
 def test_local_midnight_and_dedup(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'test.jsonl';rows=self.fixture();rows.append(rows[2]);p.write_text(''.join(json.dumps(x)+'\n' for x in rows))
   result=parse(p,0,{},*bounds('2026-09-12','2026-09-12'),'host')
   self.assertEqual(len(result[2]),1)
   self.assertEqual(result[2][0][8:10],('gpt-6-astra','xhigh'))
   self.assertEqual(result[2][0][11:17],(100,80,None,10,4,110))
   self.assertEqual(len(result[3]),1)
 def test_partial_line_checkpoint(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'test.jsonl';rows=self.fixture();complete=''.join(json.dumps(x)+'\n' for x in rows[:2]);last=json.dumps(rows[2])
   p.write_bytes((complete+last[:20]).encode());a=parse(p,0,{},*bounds('2026-09-12','2026-09-12'),'host')
   self.assertEqual(a[0],len(complete.encode()));self.assertEqual(len(a[2]),0)
   p.write_bytes((complete+last+'\n').encode());b=parse(p,a[0],a[1],*bounds('2026-09-12','2026-09-12'),'host')
   self.assertEqual(len(b[2]),1)
 def test_malformed_complete_line_stops(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'test.jsonl';p.write_text('{"type":"token_usage_record",BROKEN}\n')
   with self.assertRaises(ValueError):parse(p,0,{},*bounds('2026-09-12','2026-09-12'),'host')

 def test_speed_setting_and_epoch_lifecycle(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'test.jsonl';rows=self.fixture()
   rows.insert(1,{'timestamp':'2026-09-12T22:59:00Z','type':'event_msg','payload':{'type':'thread_settings_applied','thread_settings':{'service_tier':'default'}}})
   rows.append({'timestamp':'2026-09-13T03:31:00Z','type':'event_msg','payload':{'type':'task_complete','turn_id':'turn','started_at':1789260000,'completed_at':1789270200,'duration_ms':10200000}})
   p.write_bytes(''.join(json.dumps(x)+'\n' for x in rows).encode())
   result=parse(p,0,{},*bounds('2026-09-12','2026-09-12'),'host')
   self.assertEqual(result[2][0][10],'default')
   self.assertIsNotNone(result[4][0][7])
