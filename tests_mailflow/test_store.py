from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from mailflow.policy import decide, local_proposal
from mailflow.store import Conflict, DemoTransport, Store
from mailflow.transports import GraphTransport


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


def submit(store, *, review=False, mailbox="a", message_id="id1"):
    email = {"mailbox": mailbox, "message_id": message_id, "sender": "test@example.com", "subject": "如何查看物流", "body": "如何查看物流"}
    return store.submit(email, decide(email, local_proposal(email), enabled=not review).to_dict())


def test_duplicate_ingest_and_mailbox_isolation(store):
    first = submit(store)
    assert submit(store)["id"] == first["id"]
    assert submit(store, mailbox="b")["id"] != first["id"]
    assert len(store.get(first["id"])["events"]) == 1


def test_message_id_content_collision(store):
    job = submit(store)
    changed = job["email"] | {"body": "changed"}
    with pytest.raises(Conflict):
        store.submit(changed, job["decision"])


def test_no_transport_is_not_sent(store):
    job = submit(store)
    assert store.dispatch(job["id"])["status"] == "ready"
    assert store.get(job["id"])["receipt"] is None


def test_repeat_dispatch_does_not_send_again(store):
    job = submit(store)
    assert store.dispatch(job["id"], DemoTransport())["status"] == "simulated_sent"
    with pytest.raises(Conflict):
        store.dispatch(job["id"], DemoTransport())


def test_parallel_dispatch_has_one_winner(store):
    job = submit(store)
    barrier = Barrier(2)
    class Transport(DemoTransport):
        calls = 0
        def send(self, job):
            self.calls += 1
            return super().send(job)
    transport = Transport()
    def run():
        barrier.wait()
        try:
            return store.dispatch(job["id"], transport)["status"]
        except Conflict:
            return "conflict"
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(results) == ["conflict", "simulated_sent"]
    assert transport.calls == 1


def test_review_optimistic_lock_and_no_automatic_send_of_review(store):
    job = submit(store, review=True)
    with pytest.raises(Conflict):
        store.dispatch(job["id"], DemoTransport())
    approved = store.review(job["id"], job["version"], "Reviewed response", "approve")
    assert approved["reply"] == "Reviewed response"
    with pytest.raises(Conflict):
        store.review(job["id"], job["version"], "stale edit", "approve")


def test_rejected_mail_cannot_dispatch(store):
    job = submit(store, review=True)
    store.review(job["id"], job["version"], "", "reject")
    with pytest.raises(Conflict):
        store.dispatch(job["id"], DemoTransport())


def test_uncertain_send_is_persisted_and_not_retried_after_restart(store):
    class TimeoutTransport(DemoTransport):
        def send(self, job):
            raise TimeoutError("Do not leak provider detail")
    job = submit(store)
    assert store.dispatch(job["id"], TimeoutTransport())["status"] == "send_unknown"
    restarted = Store(store.path)
    with pytest.raises(Conflict):
        restarted.dispatch(job["id"], DemoTransport())
    assert restarted.get(job["id"])["events"][-1]["detail"] == "TimeoutError"


def test_crash_during_send_remains_claimed(store):
    class CrashTransport(DemoTransport):
        def send(self, job):
            raise KeyboardInterrupt()
    job = submit(store)
    with pytest.raises(KeyboardInterrupt):
        store.dispatch(job["id"], CrashTransport())
    assert Store(store.path).get(job["id"])["status"] == "sending"
    with pytest.raises(Conflict):
        Store(store.path).dispatch(job["id"], DemoTransport())


def test_graph_adapter_single_attempt_and_no_mark_as_read():
    from unittest.mock import Mock
    graph = Mock()
    graph.send_reply.return_value = {"attempts": 1}
    with pytest.raises(ValueError):
        GraphTransport(graph)
    t = GraphTransport(graph, enabled=True)
    assert t.send({"id": "x", "message_id": "m", "reply": "hello"}) == "graph-accepted:x"
    graph.send_reply.assert_called_once_with("m", "hello", max_attempts=1)
    graph.mark_as_read.assert_not_called()


def test_knowledge_change_after_enqueue_requires_review(store, monkeypatch):
    from mailflow import policy
    job = submit(store)
    monkeypatch.setattr(policy, "knowledge_hash", lambda: "changed")
    result = store.dispatch(job["id"], DemoTransport())
    assert result["status"] == "review"
    assert result["events"][-1]["action"] == "policy_changed"
