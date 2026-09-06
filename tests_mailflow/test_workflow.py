from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
import pytest
from mailflow.store import Store, Conflict
from mailflow.workflow import Workflow
from mailflow.retrieval import search


def email(message_id="m1", body="如何查看物流？"):
    return {"mailbox": "demo", "message_id": message_id, "subject": "客服咨询", "sender": "customer@example.com", "body": body}


def classification(**changes):
    return {"category": "order_tracking", "intents": ["order_tracking"], "confidence": .99, "risks": [], "summary": "询问物流查询方法", "usage": {"total_tokens": 20}, **changes}


def draft(**changes):
    return {"draft": "这是针对来信生成的草稿", "citations": ["tracking-help"], "unresolved": [], "usage": {"total_tokens": 30}, **changes}


@pytest.fixture
def flow(tmp_path):
    return Workflow(Store(tmp_path / "workflow.db"), live=True, maximum=6)


def test_two_stage_flow_and_duplicate_does_not_spend(flow):
    with patch("mailflow.model.classify", return_value=classification()) as classify, patch("mailflow.model.draft", return_value=draft()) as generate:
        first = flow.process(email())
        again = flow.process(email())
    assert first["status"] == "simulated_sent" and first["id"] == again["id"]
    assert first["reply"] != "这是针对来信生成的草稿"
    assert first["decision"]["analysis"]["draft"]["draft"] == "这是针对来信生成的草稿"
    assert generate.call_args.args[2][0]["zh"]
    assert classify.call_count == generate.call_count == 1
    assert flow.ledger.budget()["used"] == 2
    assert flow.ledger.budget()["known_tokens"] == 50


def test_mixed_intent_preserves_model_draft_for_review(flow):
    c = classification(intents=["order_tracking", "order_cancellation"], risks=["refund"])
    d = draft(draft="您好，物流可在确认邮件中查看。退款需人工核实，请提供订单编号。", citations=["tracking-help", "refund-process"], unresolved=["订单编号"])
    with patch("mailflow.model.classify", return_value=c), patch("mailflow.model.draft", return_value=d):
        result = flow.process(email(body="如何查看物流？另外我要退款。"))
    assert result["status"] == "review"
    assert result["reply"] == d["draft"]
    assert "multiple_intents" in result["decision"]["reasons"]
    assert "refund-process" in [s["id"] for s in result["decision"]["analysis"]["sources"]]


def test_restart_cannot_reset_budget(flow):
    with patch("mailflow.model.classify", return_value={"error": "TimeoutError"}) as call:
        limited = Workflow(flow.store, live=True, maximum=1)
        limited.process(email())
        restarted = Workflow(Store(flow.store.path), live=True, maximum=1)
        result = restarted.process(email("m2"))
    assert call.call_count == 1
    assert result["status"] == "review"
    assert result["decision"]["analysis"]["error"] == "model_call_budget_exhausted"


def test_budget_exhausted_between_stages_never_sends(flow):
    limited = Workflow(flow.store, live=True, maximum=1)
    with patch("mailflow.model.classify", return_value=classification()), patch("mailflow.model.draft") as generate:
        result = limited.process(email())
    generate.assert_not_called()
    assert result["status"] == "review"


def test_no_evidence_does_not_invent_or_spend_on_draft(flow):
    with patch("mailflow.model.classify", return_value=classification(category="unknown", intents=["unknown"])), patch("mailflow.model.draft") as generate:
        result = flow.process(email(body="请帮我写一首唐诗"))
    generate.assert_not_called()
    assert result["decision"]["analysis"]["error"] == "no_relevant_knowledge"


def test_draft_error_blocks_auto_and_is_auditable(flow):
    with patch("mailflow.model.classify", return_value=classification()), patch("mailflow.model.draft", return_value={"error": "invalid_draft_or_citations", "usage": {"total_tokens": 10}}):
        result = flow.process(email())
    assert result["status"] == "review"
    assert flow.ledger.budget()["known_tokens"] == 30
    assert flow.ledger.status("m1")["stages"][-2]["stage"] == "drafted"


def test_parallel_duplicate_does_not_double_call(flow):
    entered, release = Event(), Event()
    def slow(_):
        entered.set(); release.wait(5)
        return classification()
    with patch("mailflow.model.classify", side_effect=slow) as classify, patch("mailflow.model.draft", return_value=draft()):
        with ThreadPoolExecutor(2) as pool:
            future = pool.submit(flow.process, email())
            assert entered.wait(3)
            with pytest.raises(Conflict):
                flow.process(email())
            release.set(); future.result()
    assert classify.call_count == 1


def test_same_key_changed_body_rejected_before_spending(flow):
    with patch("mailflow.model.classify", return_value={"error": "test"}) as call:
        flow.process(email())
        with pytest.raises(Conflict):
            flow.process(email(body="changed"))
    assert call.call_count == 1


def test_crash_after_saved_job_recovers_without_another_call(flow):
    with patch("mailflow.model.classify", return_value=classification()), patch("mailflow.model.draft", return_value=draft()), patch.object(flow.ledger, "complete", side_effect=RuntimeError("crash")):
        with pytest.raises(RuntimeError):
            flow.process(email())
    with patch("mailflow.model.classify") as call:
        recovered = Workflow(Store(flow.store.path), live=True, maximum=6).process(email())
    call.assert_not_called()
    assert recovered["status"] == "ready"  # Recovery never invents an external send.


@pytest.mark.parametrize("query,expected", [("Please cancel my order", "refund-process"), ("包裹破损了", "damage-process"), ("I need an invoice", "invoice-process"), ("请提供运费报价", "quote-process")])
def test_bilingual_retrieval(query, expected):
    assert search(query)[0]["id"] == expected


def test_generic_english_words_do_not_pull_unrelated_policies():
    hits = search("My parcel arrived damaged. What information should I provide to have this investigated?")
    assert [s["id"] for s in hits] == ["damage-process"]


def test_two_different_requests_share_atomic_budget(flow):
    from threading import Barrier
    gate = Barrier(2)
    limited = Workflow(flow.store, live=True, maximum=1)
    def run(ident):
        gate.wait()
        return limited.process(email(ident))
    with patch('mailflow.model.classify', return_value={'error':'mock_failure'}) as call:
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(run, ['parallel1','parallel2']))
    assert call.call_count == 1
    assert limited.ledger.budget()['used'] == 1


def test_preexisting_v1_job_is_adopted_without_model_call(flow):
    from mailflow.policy import decide, local_proposal
    message = email()
    prior = flow.store.submit(message, decide(message, local_proposal(message)).to_dict())
    with patch('mailflow.model.classify') as call:
        current = flow.process(message)
    assert current['id'] == prior['id']
    call.assert_not_called()
