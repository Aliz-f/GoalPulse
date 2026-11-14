"""Run the actual package worker/API with explicit in-memory recorded peers.

No service port is opened. This is test evidence, not a live deployment. Use an
environment with this service's development requirements installed.
"""

import argparse
import copy
import json
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from recorded_support import NOW, recorded_runtime
from agent_integration.api import create_app
from agent_integration.service import Principal
from fastapi.testclient import TestClient


def smoke() -> dict:
    s = recorded_runtime()
    def forbidden(*args, **kwargs):
        raise AssertionError('External network/merchant effects forbidden in recorded smoke')
    with patch('socket.socket.connect', forbidden), patch('socket.create_connection', forbidden):
        assert s.runtime.ready()
        assert s.runtime.worker.run_once()
        assert s.runtime.worker.run_once()
        assert len(s.store.packages) == 1
        assert s.channel.basic_ack.call_count == 2
        package = next(iter(s.store.packages.values()))[1]
        original = copy.deepcopy(package)
        owner = {'subject': s.data['goal']['owner_user_id']}
        app = create_app(reader=s.runtime.reader,
            authenticate=lambda: Principal(owner['subject']), ready=s.runtime.ready)
        with TestClient(app, headers={
                'X-Correlation-ID': s.data['event']['correlation_id']}) as client:
            path = '/v1/action-packages/' + package['package_id']
            response = client.get(path)
            assert response.status_code == 200
            assert response.json() == original
            owner['subject'] = str(uuid4())
            assert client.get(path).status_code == 404
            owner['subject'] = s.data['goal']['owner_user_id']
            s.clock.return_value = NOW + timedelta(minutes=30)
            assert client.get(path).status_code == 410
        assert next(iter(s.store.packages.values()))[1] == original
        s.connect.assert_not_called()
    return dict(mode='recorded_test', live_deployment=False, package_count=1,
                acknowledged_deliveries=2, owner_get=200, other_owner_get=404,
                expired_get=410, external_network='blocked', package=original)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Optional path for synthetic JSON evidence')
    args = parser.parse_args()
    result = json.dumps(smoke(), indent=2) + '\n'
    if args.output:
        # Explicit output must be new; never overwrite an existing artifact.
        with args.output.open('x') as output:
            output.write(result)
        print('PASS recorded package worker/API smoke; synthetic evidence written')
    else:
        print(result, end='')
