"""Reproducible policy regression, not a model-accuracy benchmark."""
import argparse
import hashlib
import json
from pathlib import Path
from .policy import decide, knowledge_hash


def evaluate(path):
    raw = Path(path).read_bytes()
    fixtures = json.loads(raw)
    rows = []
    for case in fixtures["cases"]:
        result = decide(case["email"], case["proposal"]).to_dict()
        score = case["proposal"].get("confidence")
        baseline_auto = type(score) in (int, float) and .9 <= score <= 1
        rows.append({"id": case["id"], "expected_auto": case["expected_auto"], "guarded_auto": result["route"] == "auto",
                     "threshold_only_auto": baseline_auto, "reasons": result["reasons"]})
    return {"scope": fixtures["scope"], "fixture_sha256": hashlib.sha256(raw).hexdigest(),
            "knowledge_sha256": knowledge_hash(), "cases": len(rows),
            "policy_matches_expected": sum(r["expected_auto"] == r["guarded_auto"] for r in rows),
            "guarded_auto": sum(r["guarded_auto"] for r in rows),
            "guarded_unsafe_auto": sum(r["guarded_auto"] and not r["expected_auto"] for r in rows),
            "threshold_only_unsafe_auto": sum(r["threshold_only_auto"] and not r["expected_auto"] for r in rows),
            "baseline_note": "A score>=0.9 ablation only; not an execution of the upstream multi-stage pipeline.", "results": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", default="examples/policy-cases.json")
    parser.add_argument("--out")
    args = parser.parse_args()
    result = evaluate(args.fixtures)
    if args.out:
        path = Path(args.out); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "results"}, ensure_ascii=False))
    raise SystemExit(0 if result["policy_matches_expected"] == result["cases"] else 1)
