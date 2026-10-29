import unittest, tempfile,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'collector'))
from collector import Collector,quota_value
class MetricsTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.c=Collector(Path(self.tmp.name)/'db.sqlite',self.tmp.name);self.s={'thread':'t','provider':'openai'}
 def tearDown(self):self.c.db.close();self.tmp.cleanup()
 def event(self,t,p,time='2026-10-29T01:00:00Z'):
  self.c.process(self.s,{'type':t,'payload':p,'timestamp':time})
 def test_canonical_usage_does_not_duplicate_compatibility_event(self):
  self.event('event_msg',{'type':'task_started','turn_id':'turn'})
  self.event('token_usage_record',{'response_id':'response','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':10,'reasoning_output_tokens':7}})
  self.event('event_msg',{'type':'token_count','info':{'last_token_usage':{'input_tokens':100,'output_tokens':10},'total_token_usage':{'input_tokens':100,'output_tokens':10}}})
  self.assertEqual(self.c.db.execute('select count(*) from records').fetchone()[0],1)
  self.assertEqual(self.s['input_tokens'],100);self.assertEqual(self.s['request_count'],1)
 def test_native_ttft_remains_turn_only(self):
  self.event('event_msg',{'type':'task_started','turn_id':'turn'})
  self.event('token_usage_record',{'response_id':'response','usage':{'input_tokens':100,'output_tokens':10}})
  self.event('event_msg',{'type':'task_complete','time_to_first_token_ms':3183,'duration_ms':10000})
  self.c.turn_record(self.s,1793235600)
  rs=[json.loads(r[0]) for r in self.c.db.execute('select data from records')]
  self.assertIsNone(next(r for r in rs if r['kind']=='request')['ttft_ms'])
  self.assertEqual(next(r for r in rs if r['kind']=='turn')['ttft_ms'],3183)
 def test_completed_turns_survive_the_next_turn(self):
  self.event('event_msg',{'type':'task_started','turn_id':'first'})
  self.event('event_msg',{'type':'task_complete','time_to_first_token_ms':100,'duration_ms':500})
  self.event('event_msg',{'type':'task_started','turn_id':'second'},'2026-10-29T01:01:00Z')
  self.event('event_msg',{'type':'task_complete','time_to_first_token_ms':200,'duration_ms':600},'2026-10-29T01:01:01Z')
  self.assertEqual(self.c.db.execute('select count(*) from records').fetchone()[0],2)
 def test_quota_zero_is_a_valid_observation(self):
  q=quota_value({'primary':{'used_percent':0,'window_minutes':300,'resets_at':1}},2)
  self.assertEqual(q['windows'][0]['remaining_percent'],100)
if __name__=='__main__':unittest.main()
