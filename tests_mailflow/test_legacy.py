"""Regression coverage for changed upstream routing; no external network or source DB."""
import sys
from pathlib import Path
from unittest.mock import Mock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from config import Config
from models.database import init_db
from services.reply_service import ReplyService


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "DATABASE_PATH", str(tmp_path / "legacy.db"))
    monkeypatch.setattr(Config, "AUTO_SEND_ENABLED", True)
    monkeypatch.setattr("services.reply_service.log_audit_event", lambda **kwargs: None)
    init_db()
    svc = ReplyService.__new__(ReplyService)
    svc.threshold = .75
    svc.auto_send_minimum = .80
    svc.pii_service = Mock()
    svc.pii_service.detect_pii.return_value = {}
    svc.scoring_service = Mock()
    svc.scoring_service.score_auto_send_readiness.return_value = {"auto_send_recommended": True}
    svc.validation_service = Mock()
    svc.validation_service.validate_reply_quality.return_value = {"passed": True}
    svc.generate_reply = Mock(return_value="Draft awaiting checks")
    return svc


def process(service, graph=None, **classification):
    return service.process_email(message_id="test", subject="如何查看物流", sender="test@example.com",
         received_at="2026-09-06T00:00:00Z", body="请告诉我如何查看物流", graph_service=graph,
         classification={"category": "order_tracking", "confidence": .99, **classification})


def test_missing_graph_not_recorded_as_sent(service):
    assert process(service)["status"] == "pending_review"


def test_validation_error_does_not_fall_back_to_score(service):
    service.validation_service.validate_reply_quality.side_effect = TimeoutError()
    graph = Mock()
    assert process(service, graph)["status"] == "pending_review"
    graph.send_reply.assert_not_called()


def test_rubric_cannot_override_low_classification(service):
    graph = Mock()
    assert process(service, graph, confidence=.4)["status"] == "pending_review"
    graph.send_reply.assert_not_called()


def test_mark_as_read_failure_cannot_undo_success(service):
    graph = Mock()
    graph.send_reply.return_value = {"attempts": 1}
    graph.mark_as_read.side_effect = TimeoutError()
    assert process(service, graph)["status"] == "auto_sent"
    assert graph.send_reply.call_args.kwargs["max_attempts"] == 1


def test_uncertain_send_does_not_offer_blind_retry(service):
    graph = Mock()
    graph.send_reply.side_effect = TimeoutError()
    assert process(service, graph)["status"] == "send_unknown"
