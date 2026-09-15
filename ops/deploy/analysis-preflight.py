"""Validate a resolved Compose document without printing configuration or secrets."""

from __future__ import annotations

import json
import subprocess
import sys
from urllib.parse import urlsplit

HERMES_IMAGE = "dental-legal-hermes:5fc308a70719a83cccdbba4c0e39c23f5a8239d5"


def validate(document: dict) -> None:
    services = document["services"]
    researcher = services["hermes-researcher"]
    reviewer = services["hermes-reviewer"]
    agent = services["agent-orchestrator"]["environment"]
    gateway = services["telegram-gateway"]["environment"]
    core = services["legal-core"]["environment"]
    research_env = researcher["environment"]
    review_env = reviewer["environment"]

    for service in (researcher, reviewer):
        if service["image"] != HERMES_IMAGE or service.get("pull_policy") != "never":
            raise ValueError("Use the checked-in, locally built pinned Hermes image.")
    required = {
        "AGENT_INTERNAL_KEY": agent.get("AGENT_INTERNAL_KEY"),
        "HERMES_RESEARCHER_API_KEY": research_env.get("API_SERVER_KEY"),
        "HERMES_REVIEWER_API_KEY": review_env.get("API_SERVER_KEY"),
        "HERMES_LLM_BASE_URL": research_env.get("OPENAI_BASE_URL"),
        "HERMES_LLM_API_KEY": research_env.get("OPENAI_API_KEY"),
        "HERMES_RESEARCHER_LLM_MODEL": research_env.get("HERMES_MODEL"),
        "HERMES_REVIEWER_LLM_MODEL": review_env.get("HERMES_MODEL"),
    }
    for name, value in required.items():
        if (
            not isinstance(value, str)
            or not value.strip()
            or value.lower().startswith(("replace", "your-", "<"))
        ):
            raise ValueError(f"Configure {name} before enabling analysis.")
    secret_names = (
        "AGENT_INTERNAL_KEY", "HERMES_RESEARCHER_API_KEY", "HERMES_REVIEWER_API_KEY"
    )
    secrets = [required[name] for name in secret_names]
    if any(len(value) < 32 for value in secrets) or len(set(secrets)) != 3:
        raise ValueError("Configure three distinct internal API keys, each at least 32 characters.")
    if required["HERMES_LLM_API_KEY"] in secrets:
        raise ValueError("The provider API key must differ from the internal API keys.")
    if (
        gateway.get("AGENT_ORCHESTRATOR_URL") != "http://agent-orchestrator:8010"
        or gateway.get("AGENT_INTERNAL_KEY") != agent["AGENT_INTERNAL_KEY"]
        or core.get("AGENT_ORCHESTRATOR_URL") != "http://agent-orchestrator:8010"
        or core.get("AGENT_INTERNAL_KEY") != agent["AGENT_INTERNAL_KEY"]
        or agent.get("HERMES_RESEARCHER_URL") != "http://hermes-researcher:8642"
        or agent.get("HERMES_REVIEWER_URL") != "http://hermes-reviewer:8642"
        or agent.get("HERMES_RESEARCHER_API_KEY") != research_env["API_SERVER_KEY"]
        or agent.get("HERMES_REVIEWER_API_KEY") != review_env["API_SERVER_KEY"]
        or research_env["OPENAI_BASE_URL"] != review_env.get("OPENAI_BASE_URL")
        or research_env["OPENAI_API_KEY"] != review_env.get("OPENAI_API_KEY")
    ):
        raise ValueError("Use the checked-in internal Hermes and gateway connections.")
    try:
        provider = urlsplit(required["HERMES_LLM_BASE_URL"])
        valid_url = (
            provider.scheme in {"http", "https"}
            and bool(provider.hostname)
            and not provider.username
            and not provider.password
            and not provider.query
            and not provider.fragment
            and (provider.port is None or 1 <= provider.port <= 65535)
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ValueError("HERMES_LLM_BASE_URL must be a valid HTTP(S) provider URL.")


def main() -> int:
    try:
        validate(json.load(sys.stdin))
    except ValueError as error:
        # JSON parser messages can include input. Only our bounded validation messages
        # may reach stderr; no raw configuration or traceback is ever printed.
        message = (
            "Could not read the resolved Compose configuration."
            if isinstance(error, json.JSONDecodeError)
            else str(error)
        )
        print(message, file=sys.stderr)
        return 1
    except (KeyError, TypeError, AttributeError):
        print("Incomplete analysis Compose configuration.", file=sys.stderr)
        return 1
    try:
        image = subprocess.run(
            ["docker", "image", "inspect", HERMES_IMAGE],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        print("Could not inspect the local pinned Hermes image.", file=sys.stderr)
        return 1
    if image.returncode:
        print(
            "Build the pinned image with sh ops/hermes/build-pinned-image.sh first.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
