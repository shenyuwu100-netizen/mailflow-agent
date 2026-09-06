"""Classify -> retrieve -> draft -> release policy -> durable review/outbox."""
from . import model
from .ledger import Ledger
from .policy import decide, local_proposal, language, KNOWLEDGE
from .retrieval import search
from .store import DemoTransport


class Workflow:
    def __init__(self, store, *, live=False, maximum=12):
        self.store, self.live = store, live
        self.ledger = Ledger(store, maximum)

    def call(self, email, stage, function, *args):
        call_id = self.ledger.reserve(email, stage)
        if call_id is None:
            return {"error": "model_call_budget_exhausted"}
        try:
            result = function(*args)
            if not isinstance(result, dict):
                raise ValueError("Expected object")
        except Exception as exc:
            result = {"error": type(exc).__name__}
        self.ledger.finish(call_id, result)
        return result

    def process(self, email, *, automatic=True):
        existing = self.ledger.claim(email, automatic)
        if existing:
            # Do not re-analyze or re-send on duplicate HTTP requests.
            return self.store.get(existing)
        if not self.live:
            proposal = local_proposal(email)
            analysis = {"method": "deterministic_demo"}
            decision = decide(email, proposal, enabled=automatic).to_dict()
            self.ledger.stage(email, "rules", {"route": decision["route"]})
        else:
            analysis, decision = self.analyze(email, automatic)
        decision["analysis"] = analysis
        self.ledger.stage(email, "policy", {"route": decision["route"], "reasons": decision["reasons"]})
        job = self.store.submit(email, decision)
        self.ledger.complete(email, job["id"])
        if job["status"] == "ready":
            job = self.store.dispatch(job["id"], DemoTransport())
        return job

    def analyze(self, email, automatic):
        self.ledger.stage(email, "classifying", {})
        classification = self.call(email, "classification", model.classify, email)
        self.ledger.stage(email, "classified", classification)
        analysis = {"method": "llm-rag", "classification": classification, "sources": [], "draft": None}
        if classification.get("error"):
            analysis["error"] = classification["error"]
            return analysis, self.failure(email, classification["error"], automatic)
        sources = search(email["subject"] + '\n' + email["body"], classification["intents"])
        analysis["sources"] = sources
        self.ledger.stage(email, "retrieved", {"ids": [s["id"] for s in sources]})
        if not sources:
            analysis["error"] = "no_relevant_knowledge"
            return analysis, self.failure(email, "no_relevant_knowledge", automatic, classification)
        self.ledger.stage(email, "drafting", {})
        generated = self.call(email, "draft", model.draft, email, classification, sources)
        analysis["draft"] = generated
        self.ledger.stage(email, "drafted", generated)
        if generated.get("error"):
            analysis["error"] = generated["error"]
            return analysis, self.failure(email, generated["error"], automatic, classification)
        source_id = generated["citations"][0]
        proposal = {"category": classification["category"], "confidence": classification["confidence"],
                    "source_id": source_id, "draft": generated["draft"]}
        decision = decide(email, proposal, enabled=automatic).to_dict()
        extra = []
        if classification["risks"]:
            extra.append("model_risk_flags")
        if len(set(classification["intents"])) != 1:
            extra.append("multiple_intents")
        if generated["unresolved"]:
            extra.append("unresolved_facts")
        if len(set(generated["citations"])) != 1 or source_id not in KNOWLEDGE:
            extra.append("manual_only_knowledge")
        if extra:
            decision["route"] = "review"
            decision["reasons"] = [r for r in decision["reasons"] if r != "low_risk_verified_faq"] + extra
        if decision["route"] == "review":
            # A reviewer sees the generated draft, not a generic FAQ substituted for a mixed request.
            decision["reply"] = generated["draft"]
        analysis["reply_mode"] = "approved_source" if decision["route"] == "auto" else "model_draft_for_review"
        analysis["usage"] = {"total_tokens": sum(r.get("usage", {}).get("total_tokens", 0) for r in (classification, generated))}
        return analysis, decision

    @staticmethod
    def failure(email, error, automatic, classification=None):
        fallback = ("感谢您的来信。自动分析暂未完成，请客服核实后编辑回复。" if language(email['body']) == 'zh'
                    else "Thank you for your message. Automated analysis could not be completed; an agent needs to verify and edit this reply.")
        proposal = {"error": error, "draft": fallback, "confidence": (classification or {}).get("confidence"), "category": (classification or {}).get("category")}
        return decide(email, proposal, enabled=automatic).to_dict()
