from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest

from goalpulse_discovery.webhooks import ReceiverConfig, WebhookError, WebhookReceiver

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
SECRET = "whsec_synthetic_test_only"


def delivery(*, event_type="change.detected", baseline=False, monitor="mon_fixture", stamp=None):
    change = {"monitor_id": monitor, "run_id": "run_fixture"}
    data = {"change": change}
    if event_type == "run.completed":
        data["run"] = {"id": "run_fixture", "monitor_id": monitor, "status": "completed",
                       "baseline_created": baseline, "change_detected": not baseline}
    payload = {"id": "evt_fixture", "event": event_type, "created_at": "2026-11-13T12:00:00Z", "data": data}
    raw = json.dumps(payload).encode()
    stamp = str(int(NOW.timestamp())) if stamp is None else str(stamp)
    signature = hmac.new(SECRET.encode(), stamp.encode() + b"." + raw, sha256).hexdigest()
    return raw, {"X-Context-Id": "evt_fixture", "X-Context-Event": event_type,
                 "X-Context-Signature": f"t={stamp},v1={signature}"}


def receiver(store=None):
    store = Mock() if store is None else store
    store.accept.return_value = True
    secrets = Mock(get=Mock(return_value=SECRET))
    return WebhookReceiver(store, secrets, clock=lambda: NOW), store


def config():
    return ReceiverConfig(uuid4(), "mon_fixture", "secret/ref")


def test_signed_event_persists_and_returns_without_provider_work():
    subject, store = receiver()
    assert subject.receive(config(), *delivery()) == (True, True)
    assert store.accept.call_count == 1
    assert store.accept.call_args.kwargs["changed"] is True
    assert SECRET not in repr(store.accept.call_args)


@pytest.mark.parametrize("signature", ["", "t=bad,v1=bad", "t=1,t=2,v1=" + "0" * 64,
                                       "t=1,v1=" + "0" * 64])
def test_malformed_or_old_signatures_never_persist(signature):
    subject, store = receiver()
    raw, headers = delivery()
    headers["X-Context-Signature"] = signature
    with pytest.raises(WebhookError) as error:
        subject.receive(config(), raw, headers)
    assert error.value.status == 401
    store.accept.assert_not_called()


@pytest.mark.parametrize("offset", [-301, 301])
def test_signed_stale_or_future_attempt_is_rejected(offset):
    subject, store = receiver()
    with pytest.raises(WebhookError):
        subject.receive(config(), *delivery(stamp=int(NOW.timestamp()) + offset))
    store.accept.assert_not_called()


def test_tampering_monitor_mismatch_and_header_mismatch_are_rejected():
    subject, store = receiver()
    raw, headers = delivery()
    with pytest.raises(WebhookError):
        subject.receive(config(), raw + b" ", headers)
    with pytest.raises(WebhookError):
        subject.receive(config(), *delivery(monitor="mon_other"))
    headers["X-Context-Id"] = "evt_different"
    with pytest.raises(WebhookError):
        subject.receive(config(), raw, headers)
    store.accept.assert_not_called()


def test_baseline_receipt_is_recorded_without_change_job():
    subject, store = receiver()
    assert subject.receive(config(), *delivery(event_type="run.completed", baseline=True)) == (True, False)
    assert store.accept.call_args.kwargs["changed"] is False


def test_duplicate_event_is_safe_and_persistence_failure_prevents_ack():
    subject, store = receiver()
    store.accept.side_effect = [True, False, OSError("DB unavailable")]
    cfg, args = config(), delivery()
    assert subject.receive(cfg, *args) == (True, True)
    assert subject.receive(cfg, *args) == (False, True)
    with pytest.raises(OSError):
        subject.receive(cfg, *args)


def test_oversized_body_rejected_before_secret_lookup():
    subject, store = receiver()
    with pytest.raises(WebhookError) as error:
        subject.receive(config(), b"a" * 262145, {})
    assert error.value.status == 413
    subject.secrets.get.assert_not_called()


def test_reserialized_signed_replay_has_same_semantic_deduplication_hash():
    subject, store = receiver()
    cfg = config()
    raw, headers = delivery()
    subject.receive(cfg, raw, headers)
    first_hash = store.accept.call_args.args[3]
    new_raw = json.dumps(json.loads(raw), indent=2, sort_keys=True).encode()
    stamp = str(int(NOW.timestamp()))
    digest = hmac.new(SECRET.encode(), stamp.encode() + b"." + new_raw, sha256).hexdigest()
    headers["X-Context-Signature"] = f"t={stamp},v1={digest}"
    subject.receive(cfg, new_raw, headers)
    assert store.accept.call_args.args[3] == first_hash
