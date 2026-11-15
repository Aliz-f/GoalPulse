import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'src'))
from goal_management.events import consume_evaluated
class Store:
 def __init__(self): self.events=[]; self.results=['projected','duplicate','stale','inactive']
 def process_evaluated(self,**event): self.events.append(event); return self.results.pop(0)
def test_duplicate_and_stale_evaluations_are_safe():
 e=json.loads((Path(__file__).parents[3]/'contracts/examples/event.goal-evaluated.json').read_text()); s=Store()
 assert consume_evaluated(s,e)=='projected'; assert consume_evaluated(s,e)=='duplicate'
 e['event_id']='ae000000-0000-4000-8000-000000000012'; e['payload']['goal_version']=2
 assert consume_evaluated(s,e)=='stale'
 e['event_id']='ae000000-0000-4000-8000-000000000013'; e['payload']['goal_version']=1
 assert consume_evaluated(s,e)=='inactive'; assert len(s.events)==4

def test_invalid_evaluation_event_is_rejected_before_inbox_write():
 e=json.loads((Path(__file__).parents[3]/'contracts/examples/event.goal-evaluated.json').read_text()); s=Store()
 e['schema_version']=2
 with pytest.raises(ValueError, match='invalid domain event'): consume_evaluated(s,e)
 assert not s.events

def test_mismatched_aggregate_and_goal_are_rejected_before_inbox_write():
 e=json.loads((Path(__file__).parents[3]/'contracts/examples/event.goal-evaluated.json').read_text()); s=Store()
 e['aggregate_id']='ae000000-0000-4000-8000-000000000099'
 with pytest.raises(ValueError, match='aggregate'): consume_evaluated(s,e)
 assert not s.events
