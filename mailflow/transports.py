"""Explicit adapter for the team's GraphService; not exposed by the demo server."""
class GraphTransport:
    name = "microsoft-graph"
    simulated = False

    def __init__(self, graph_service, *, enabled=False):
        if not enabled:
            raise ValueError("Graph transport requires explicit enablement")
        self.graph_service = graph_service

    def send(self, job):
        # One attempt only: a timeout after provider acceptance must not duplicate a reply.
        result = self.graph_service.send_reply(job["message_id"], job["reply"], max_attempts=1)
        if not isinstance(result, dict) or result.get("attempts") != 1:
            raise ValueError("Unconfirmed Graph response")
        # Do not mark_as_read here: failure of that separate operation cannot undo a send.
        return "graph-accepted:" + job["id"]
