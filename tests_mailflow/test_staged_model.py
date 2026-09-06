from unittest.mock import patch
import pytest
from mailflow import model
from mailflow.retrieval import search


@pytest.mark.parametrize("changes", [{"confidence": True}, {"confidence": float('nan')}, {"category": []}, {"intents": ["invented"]}, {"risks": "none"}, {"summary": None}])
def test_classifier_schema_validation(changes):
    value = {"category": "order_tracking", "intents": ["order_tracking"], "confidence": .99, "risks": [], "summary": "test", "usage": {}, **changes}
    with patch("mailflow.model._request", return_value=value):
        assert model.classify({})["error"] == "invalid_classification_schema"


@pytest.mark.parametrize("citations", [[], ["invented-source"], [None], "tracking-help"])
def test_unverifiable_citation_rejected(citations):
    with patch("mailflow.model._request", return_value={"draft": "reply", "citations": citations, "unresolved": [], "usage": {"total_tokens": 10}}):
        result = model.draft({}, {"category": "order_tracking", "intents": ["order_tracking"]}, search("如何查看物流"))
    assert result["error"] == "invalid_draft_or_citations"
    assert result["usage"]["total_tokens"] == 10


def test_language_is_explicitly_sent_to_classifier():
    with patch("mailflow.model._request", return_value={"error": "test"}) as call:
        model.classify({"subject": "Damaged parcel", "body": "My parcel arrived damaged."})
    assert call.call_args.args[1]["output_language"] == "en"
