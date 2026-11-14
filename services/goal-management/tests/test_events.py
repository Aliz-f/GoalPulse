import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from goal_management.events import consume_evaluated
class Store:
 def __init__(self): self.seen=set(); self.updated=[]; self.version=1
 def inbox_seen(self,x): return x in self.seen
 def mark_inbox(self,x,t): self.seen.add(x)
 def current_version(self,x): return self.version
 def update_projection(self,*x): self.updated.append(x)
def test_duplicate_and_stale_evaluations_are_safe():
 e=json.loads((Path(__file__).parents[3]/'contracts/examples/event.goal-evaluated.json').read_text()); s=Store()
 assert consume_evaluated(s,e)=='projected'; assert consume_evaluated(s,e)=='duplicate'; assert len(s.updated)==1
 e['event_id']='ae000000-0000-4000-8000-000000000012'; e['payload']['goal_version']=2
 assert consume_evaluated(s,e)=='stale'; assert len(s.updated)==1
