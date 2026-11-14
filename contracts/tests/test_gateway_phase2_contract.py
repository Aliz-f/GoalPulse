import copy
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "contracts/openapi/gateway.phase2.extension.v1.yaml"


def load_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def load_spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


def expand_local_refs(value, spec: dict):
    """Expand only component-schema refs so jsonschema can validate examples."""
    if isinstance(value, dict):
        if set(value) == {"$ref"} and value["$ref"].startswith("#/components/schemas/"):
            name = value["$ref"].rsplit("/", 1)[-1]
            return expand_local_refs(copy.deepcopy(spec["components"]["schemas"][name]), spec)
        return {key: expand_local_refs(item, spec) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_local_refs(item, spec) for item in value]
    return value


def validate_component(spec: dict, name: str, instance: dict) -> None:
    schema = expand_local_refs(copy.deepcopy(spec["components"]["schemas"][name]), spec)
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(instance))
    assert not errors, errors[0].message if errors else ""


def test_extension_is_additive_owner_scoped_and_has_no_internal_route():
    spec = load_spec()
    frozen = yaml.safe_load((ROOT / "contracts/openapi/gateway.v1.yaml").read_text(encoding="utf-8"))
    assert spec["openapi"] == "3.1.0"
    assert spec["info"]["version"] == "1.0.0"
    assert not set(spec["paths"]) & set(frozen["paths"])
    assert all(not path.startswith("/internal/") for path in spec["paths"])
    assert set(spec["paths"]) == {
        "/v2/demo/sessions",
        "/v2/demo/session",
        "/v2/demo/sessions/logout",
        "/v1/goals/{goal_id}/evaluations",
        "/v1/notifications",
        "/v1/notifications/{notification_id}/read",
        "/v1/action-packages/{package_id}",
    }
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            assert method in {"get", "post", "patch"}, path
            assert operation["operationId"]
            assert operation["responses"]
    for path in (
        "/v1/goals/{goal_id}/evaluations",
        "/v1/notifications",
        "/v1/notifications/{notification_id}/read",
        "/v1/action-packages/{package_id}",
    ):
        operation = next(iter(spec["paths"][path].values()))
        assert operation["x-goalpulse-owner-scoped"] is True
        assert "401" in operation["responses"]


def test_demo_identity_contract_is_cookie_only_and_fail_closed():
    spec = load_spec()
    login = spec["paths"]["/v2/demo/sessions"]["post"]
    current = spec["paths"]["/v2/demo/session"]["get"]
    logout = spec["paths"]["/v2/demo/sessions/logout"]["post"]
    assert login["security"] == []
    assert set(login["responses"]) >= {"200", "401", "403", "429", "503"}
    assert "Set-Cookie" in login["responses"]["200"]["headers"]
    assert current["security"] == [{"DemoSession": []}]
    assert logout["security"] == [{"DemoSession": []}]
    assert set(logout["responses"]) >= {"204", "401", "403", "503"}
    demo_user = spec["components"]["schemas"]["DemoUser"]
    assert "token" not in demo_user["properties"]
    assert "password" not in demo_user["properties"]


def test_notification_and_grouped_action_package_examples_are_strict():
    spec = load_spec()
    strict = {"DemoLogin", "DemoUser", "Notification", "SellerOrderItem", "SellerOrder", "ActionPackage"}
    assert all(spec["components"]["schemas"][name]["additionalProperties"] is False for name in strict)
    validate_component(
        spec,
        "Notification",
        load_json("contracts/phase2/examples/notification.v1.json"),
    )
    package = load_json("services/agent-integration/tests/fixtures/action-package.product.fixture.json")
    validate_component(spec, "ActionPackage", package)
    local_schema = load_json("services/agent-integration/schemas/action-package.v1.schema.json")
    local_errors = list(Draft202012Validator(local_schema, format_checker=FormatChecker()).iter_errors(package))
    assert not local_errors
    assert "seller_orders" in spec["components"]["schemas"]["ActionPackage"]["properties"]
    assert "seller_offers" not in spec["components"]["schemas"]["ActionPackage"]["properties"]
    unsafe = copy.deepcopy(package)
    unsafe["auto_execute"] = True
    schema = expand_local_refs(copy.deepcopy(spec["components"]["schemas"]["ActionPackage"]), spec)
    assert not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(unsafe)


def test_two_user_contract_cases_mask_cross_owner_resources_and_cover_401_403_404():
    spec = load_spec()
    matrix = load_json("contracts/phase2/examples/owner-access-cases.v1.json")
    cases = matrix["cases"]
    assert set(matrix["users"]) == {"owner_a", "owner_b"}
    assert {case["expected_status"] for case in cases} >= {200, 401, 403, 404}
    cross_owner = [
        case for case in cases
        if case.get("authenticated_as") not in {None, case["resource_owner"]}
    ]
    assert cross_owner
    assert all(case["expected_status"] == 404 for case in cross_owner)
    evaluation = spec["paths"]["/v1/goals/{goal_id}/evaluations"]["get"]
    mark_read = spec["paths"]["/v1/notifications/{notification_id}/read"]["patch"]
    assert set(evaluation["responses"]) >= {"200", "401", "404"}
    assert set(mark_read["responses"]) >= {"204", "401", "403", "404"}


def test_frozen_event_set_remains_exactly_four():
    routing = yaml.safe_load((ROOT / "contracts/event-routing.yaml").read_text(encoding="utf-8"))
    assert set(routing["routing"]) == {
        "goal.activated.v1",
        "evidence.updated.v1",
        "goal.evaluated.v1",
        "goal.opportunity_verified.v1",
    }
