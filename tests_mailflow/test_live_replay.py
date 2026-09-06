"""Replay recorded two-stage model outputs without any paid requests."""
import json
from pathlib import Path
from unittest.mock import patch
from mailflow.store import Store
from mailflow.workflow import Workflow


def test_recorded_live_workflow(tmp_path):
    record = json.loads((Path(__file__).resolve().parents[1] / 'examples/live-workflow-v2.json').read_text(encoding='utf-8'))
    flow = Workflow(Store(tmp_path / 'replay.db'), live=True, maximum=6)
    for case in record['cases']:
        with patch('mailflow.model.classify', return_value=case['classification']), patch('mailflow.model.draft', return_value=case['draft']):
            job = flow.process(case['email'])
        assert job['decision']['route'] == case['expected_route']
        assert job['reply'] == case['expected_reply']
