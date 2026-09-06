import json
from pathlib import Path
from mailflow.policy import decide
from mailflow.evaluate import evaluate

ROOT = Path(__file__).resolve().parents[1]


def test_authored_policy_regressions():
    result = evaluate(ROOT / "examples/policy-cases.json")
    assert result["policy_matches_expected"] == result["cases"] == 20
    assert result["guarded_unsafe_auto"] == 0


def test_recorded_model_response_replays_without_network():
    record = json.loads((ROOT / "examples/live-smoke.json").read_text(encoding="utf-8"))
    for case in record["cases"]:
        assert decide(case["email"], case["proposal"]).to_dict() == case["decision"]
