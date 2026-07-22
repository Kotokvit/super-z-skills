import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = REPO_ROOT / "skills" / "_shared"
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))

from sandbox import SandboxBridge, SandboxV2Backend, create_backend


@pytest.mark.parametrize(
    "query",
    [
        "summarize the market shift",
        "find the main risk",
        "plan the launch",
        "extract the most relevant fragment",
        "review the architecture",
        "explain the policy change",
        "compare the two proposals",
        "highlight the edge cases",
        "generate a brief implementation plan",
        "detect the wrong assumption",
        "find the root cause",
        "summarize the customer complaint",
        "draft a short response",
        "identify the key constraint",
        "outline the migration path",
        "look for anomalies",
        "surface the tradeoff",
        "explain the failure mode",
        "map the decision factors",
        "trace the dependency chain",
        "mention the unknowns",
        "clarify the priority",
        "review the onboarding flow",
        "list the open questions",
        "summarize the support ticket",
        "rank the opportunities",
        "highlight the bottleneck",
        "describe the security impact",
        "report the user pain points",
        "create a short action list",
        "recommend the next step",
        "inspect the regression trend",
        "summarize the release notes",
    ],
)
def test_sandbox_v2_backend_emits_structured_output(query: str) -> None:
    backend = SandboxV2Backend(enable_observer=False)
    payload = json.loads(backend.chat("system", query))
    assert payload["backend"] == "sandbox_v2"
    assert payload["estimated_llm_calls"] in {1, 2, 3}
    assert payload["roles"]


def test_bridge_defaults_to_v2_backend() -> None:
    bridge = SandboxBridge()
    backend = bridge.get_backend()
    assert isinstance(backend, SandboxV2Backend)


def test_bridge_can_switch_to_v1() -> None:
    bridge = SandboxBridge()
    backend = bridge.get_backend("v1")
    assert backend.name == "sandbox_v1"


def test_factory_accepts_v2_aliases() -> None:
    for alias in ("v2", "sandbox_v2", "sandbox-v2"):
        backend = create_backend(alias)
        assert backend.name in {"sandbox_v2", "sandbox_v1"}
