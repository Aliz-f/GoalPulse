"""Exercise procurement worker -> saved evaluation -> authenticated API offline.

All facts are synthetic recorded inputs. No peer implementation, real broker,
database or provider call is used. This does not establish a live café saving.
"""

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from procurement_recorded import NOW, recorded_runtime
from goal_evaluation.api import create_app
from goal_evaluation.auth import ServiceAuthorizer
from test_ge_api import SECRET, SCOPE, bearer
from fastapi.testclient import TestClient


def run_case(*, original_cafe=False, confirmed=False):
    s = recorded_runtime(original_cafe=original_cafe, confirmed=confirmed)
    assert s.runtime.ready()
    assert s.runtime.worker.run_once()
    assert s.runtime.worker.run_once()  # Replay cannot create another outcome.
    s.channel.basic_get.return_value = None, None, None
    s.runtime.worker.run_once()  # Drain any remaining committed outbox intent.
    assert len(s.store.data['evaluations']) == 1
    result = next(iter(s.store.data['evaluations'].values()))
    auth = ServiceAuthorizer(secret=SECRET, issuer='fixture-issuer', audience='fixture-audience',
                              required_scope=SCOPE, clock=lambda: NOW)
    app = create_app(reader=s.runtime.reader, authorizer=auth, ready=s.runtime.ready)
    with TestClient(app) as client:
        response = client.get('/internal/v1/evaluations/' + result['evaluation_id'],
            headers={'Authorization': bearer(), 'X-Correlation-ID': s.data['event']['correlation_id']})
        assert response.status_code == 200 and response.json() == result
    s.connect.assert_not_called()
    emitted = [call.kwargs['routing_key'] for call in s.channel.basic_publish.call_args_list]
    return dict(normalization_confirmed=confirmed, saved_evaluation_count=1,
                api_status=200, emitted_routes=sorted(emitted), evaluation=result)


def smoke() -> dict:
    def forbidden(*args, **kwargs):
        raise AssertionError('External network forbidden in recorded procurement smoke')
    with patch('socket.socket.connect', forbidden), patch('socket.create_connection', forbidden):
        supported = run_case(confirmed=True)
        unconfirmed = run_case()
        original = run_case(original_cafe=True, confirmed=True)
    assert supported['evaluation']['is_opportunity_verified']
    assert unconfirmed['evaluation']['status'] == original['evaluation']['status'] == 'unknown'
    assert unconfirmed['emitted_routes'] == original['emitted_routes'] == ['goal.evaluated.v1']
    assert 'progress' not in original['evaluation']
    return dict(mode='recorded_test', live_deployment=False, external_network='blocked',
                normalized_single_unit=supported, unconfirmed_policy=unconfirmed,
                original_cafe_request=original)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New file for synthetic JSON evidence')
    args = parser.parse_args()
    content = json.dumps(smoke(), indent=2) + '\n'
    if args.output:
        with args.output.open('x') as output:
            output.write(content)
        print('PASS recorded procurement worker/API smoke; synthetic evidence written')
    else:
        print(content, end='')
