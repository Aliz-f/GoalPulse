from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_goal_sources_extension_is_additive_bounded_and_authenticated():
    extension = yaml.safe_load(
        (ROOT / "contracts/openapi/internal.goal-sources.v1.yaml").read_text(encoding="utf-8")
    )
    accepted = yaml.safe_load(
        (ROOT / "contracts/openapi/internal.proposed.v1.yaml").read_text(encoding="utf-8")
    )
    path = "/internal/v1/goals/{goal_id}/versions/{version}/sources"
    assert extension["openapi"] == "3.1.0"
    assert extension["info"]["version"] == "1.0.0"
    assert set(extension["paths"]) == {path}
    assert path not in accepted["paths"]
    assert extension["security"] == [{"ServiceBearer": []}]
    schema = extension["components"]["schemas"]["GoalSources"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["source_ids"]["maxItems"] == 3
    assert schema["properties"]["source_ids"]["uniqueItems"] is True
    operation = extension["paths"][path]["get"]
    assert {parameter["$ref"] for parameter in operation["parameters"]} == {
        "#/components/parameters/GoalId",
        "#/components/parameters/Version",
        "#/components/parameters/CorrelationId",
    }
    assert set(operation["responses"]) == {"200", "400", "403", "404", "503"}
