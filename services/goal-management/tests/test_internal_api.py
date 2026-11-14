import asyncio, base64, hashlib, hmac, json, os, sys, uuid
from pathlib import Path
import httpx

ROOT=Path(__file__).parents[3]
sys.path.insert(0,str(ROOT/'services/goal-management/src'))
from goal_management.api import create_app

GOAL=uuid.UUID('ae000000-0000-4000-8000-000000000002')
SECRET='service_secret_for_goal_management_tests_32_chars'

class FakeReads:
    def __init__(self):
        self.spec=json.loads((ROOT/'contracts/examples/goal-spec.product.json').read_text())
    def get_version(self, goal_id, version): return self.spec if goal_id==GOAL and version==1 else None
    def get_runtime(self, goal_id):
        return {'goal_id':str(GOAL),'owner_user_id':'ae000000-0000-4000-8000-000000000001','current_version':1,'lifecycle':'paused'} if goal_id==GOAL else None
    def list_active(self,cursor,limit):
        return ([{'goal_id':str(GOAL),'current_version':1}],None)

def bearer(scope='gm.read'):
    enc=lambda x:base64.urlsafe_b64encode(json.dumps(x,separators=(',',':')).encode()).rstrip(b'=').decode()
    h,p=enc({'alg':'HS256','typ':'JWT'}),enc({'iss':'goalpulse-internal-dev','aud':'goalpulse-internal','sub':str(uuid.uuid4()),'token_kind':'service','scope':scope,'exp':4102444800})
    s=base64.urlsafe_b64encode(hmac.new(SECRET.encode(),f'{h}.{p}'.encode(),hashlib.sha256).digest()).rstrip(b'=').decode()
    return 'Bearer '+h+'.'+p+'.'+s

def request(path,headers=None):
    async def run():
        transport=httpx.ASGITransport(app=create_app(FakeReads()))
        async with httpx.AsyncClient(transport=transport,base_url='http://gm') as c: return await c.get(path,headers=headers)
    return asyncio.run(run())

def setup_module():
    os.environ.update(SERVICE_JWT_SECRET=SECRET,SERVICE_JWT_ISSUER='goalpulse-internal-dev',SERVICE_JWT_AUDIENCE='goalpulse-internal')

def test_dm_ge_consumer_reads_and_active_page_follow_contract():
    headers={'Authorization':bearer()}
    spec=request(f'/internal/v1/goals/{GOAL}/versions/1',headers); assert spec.status_code==200 and spec.json()['goal_id']==str(GOAL)
    runtime=request(f'/internal/v1/goals/{GOAL}/runtime',headers); assert runtime.status_code==200 and runtime.json()['lifecycle']=='paused'
    active=request('/internal/v1/goals/active?limit=1',headers); assert active.status_code==200 and set(active.json())=={'items','next_cursor'} and 'owner_user_id' not in active.text

def test_unauthorized_service_and_unknown_resources_are_denied_or_masked():
    assert request(f'/internal/v1/goals/{GOAL}/runtime').status_code==403
    assert request(f'/internal/v1/goals/{GOAL}/runtime',{'Authorization':bearer('gm.write')}).status_code==403
    assert request('/internal/v1/goals/ae000000-0000-4000-8000-000000000099/runtime',{'Authorization':bearer()}).status_code==404
