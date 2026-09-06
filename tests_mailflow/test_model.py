import io
import json
from unittest.mock import patch
from mailflow.model import propose


def test_model_error_routes_fail_closed_and_no_retry():
    with patch("mailflow.model.urlopen", side_effect=TimeoutError("sensitive provider response")) as call:
        result = propose({"subject": "x", "body": "y"}, base_url="https://example.com/v1", api_key="dummy")
    assert result == {"error": "TimeoutError", "method": "llm"}
    assert call.call_count == 1


def test_bounded_json_request_and_usage():
    response = {"choices": [{"message": {"content": '{"category":"unknown","confidence":0.2,"source_id":null}'}}], "usage": {"total_tokens": 20, "internal": "excluded"}}
    with patch("mailflow.model.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as call:
        result = propose({"subject": "hello", "body": "test", "sender": "private@example.com"}, base_url="https://example.com/v1", api_key="dummy")
    request = call.call_args.args[0]
    data = json.loads(request.data)
    assert data["max_completion_tokens"] == 900
    assert "private@example.com" not in request.data.decode()
    assert result["usage"] == {"total_tokens": 20}


def test_non_object_response_fails_closed():
    response = {"choices": [{"message": {"content": '[]'}}]}
    with patch("mailflow.model.urlopen", return_value=io.BytesIO(json.dumps(response).encode())):
        assert propose({}, base_url="https://example.com", api_key="dummy")["error"] == "ValueError"
