from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = ROOT / "frontend" / "tests" / "aiAssistText.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node test runs in the frontend Docker stage")
def test_ai_result_text_parses_nested_and_repaired_json_outputs() -> None:
    subprocess.run(["node", str(NODE_TEST)], cwd=ROOT, check=True)
