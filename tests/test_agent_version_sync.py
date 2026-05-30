"""Guard: the RMM agent's two version sources must never drift apart.

There are two places the agent version lives:
  * rmm_agent/version.txt        — what the SERVER serves from /rmm/agent/version,
                                    i.e. what online agents compare against to decide
                                    whether to self-update.
  * AGENT_VERSION in agent_client.py — what a running agent reports as its own version
                                    (User-Agent, heartbeat, telemetry).

If these disagree, agents either think they're permanently out of date (update loop)
or report a version that doesn't match what the server believes is current. They were
hand-edited separately in the past and drifted (2.5.7 vs 3.1.1). This test fails the
build the moment they diverge — a release must bump both (use release.py, which does).

Pure file parsing on purpose: no app import, no DB, runs in the existing CI job.
"""
import os
import re

_AGENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rmm_agent")


def _version_txt() -> str:
    with open(os.path.join(_AGENT_DIR, "version.txt"), encoding="utf-8") as f:
        return f.read().strip()


def _agent_client_version() -> str:
    path = os.path.join(_AGENT_DIR, "agent_client.py")
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r'\s*AGENT_VERSION\s*=\s*["\']([^"\']+)["\']', line)
            if m:
                return m.group(1)
    raise AssertionError("AGENT_VERSION assignment not found in agent_client.py")


def test_agent_version_sources_match():
    txt = _version_txt()
    code = _agent_client_version()
    assert txt == code, (
        f"Agent version drift: version.txt={txt!r} but agent_client.py "
        f"AGENT_VERSION={code!r}. Bump both together (run rmm_agent/release.py)."
    )


def test_version_is_semver_like():
    txt = _version_txt()
    assert re.fullmatch(r"\d+\.\d+\.\d+", txt), f"version.txt {txt!r} is not X.Y.Z"
