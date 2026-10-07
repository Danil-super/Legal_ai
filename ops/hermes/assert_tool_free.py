"""Fail closed unless the active pinned Hermes profile exposes zero model tools.

Run this inside the Hermes runtime immediately before `hermes gateway run`.
The imports intentionally use pinned Hermes internals: this guard is coupled to
ADR-0013's immutable commit and must be reviewed again on every Hermes upgrade.
"""

from __future__ import annotations

import json
import sys
from typing import Any

EXPECTED_HERMES_VERSION = "0.20.6"


def assert_call_budget(config: dict[str, Any]) -> dict[str, int]:
    error = "Hermes legal call budget mismatch; refusing to start"
    model = config.get("model")
    agent = config.get("agent")
    if not isinstance(model, dict) or not isinstance(agent, dict):
        raise SystemExit(error)
    provider = model.get("provider")
    model_id = model.get("default")
    attempts = agent.get("api_max_retries")
    if provider != "custom" or not isinstance(model_id, str) or not model_id:
        raise SystemExit(error)
    if type(attempts) is not int or attempts != 1:
        raise SystemExit(error)
    try:
        from hermes_cli.timeouts import get_provider_request_timeout, get_provider_stale_timeout

        request_timeout = get_provider_request_timeout(provider, model_id)
        stale_timeout = get_provider_stale_timeout(provider, model_id)
    except Exception:
        # Loader/adapter errors must not print private configuration values.
        raise SystemExit(error) from None
    for timeout in (request_timeout, stale_timeout):
        if type(timeout) not in (int, float) or timeout != 29:
            raise SystemExit(error)
    return {"apiAttemptsPerCycle": 1, "requestTimeoutSeconds": 29, "staleTimeoutSeconds": 29}


def _tool_name(definition: dict[str, Any]) -> str:
    function = definition.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return function["name"]
    value = definition.get("name")
    return value if isinstance(value, str) else "<unknown>"


def main() -> None:
    try:
        from importlib.metadata import PackageNotFoundError, version

        import model_tools
        from hermes_cli.config import load_config
        from hermes_cli.plugins import discover_plugins
        from hermes_cli.tools_config import _get_platform_tools
        from tools.registry import discover_builtin_tools
    except ImportError as exc:
        raise SystemExit(f"Hermes preflight imports failed: {exc}") from exc

    package_version: str | None = None
    for package_name in ("hermes-agent", "hermes_agent"):
        try:
            package_version = version(package_name)
            break
        except PackageNotFoundError:
            continue
    if package_version is None:
        raise SystemExit("Hermes package metadata is unavailable; refusing to start")
    if package_version != EXPECTED_HERMES_VERSION:
        raise SystemExit(
            f"Hermes version mismatch: expected {EXPECTED_HERMES_VERSION}, got {package_version}"
        )

    # Resolve the same runtime registries that the gateway can see. A bundled or
    # configured plugin must therefore also survive this check before the server starts.
    discover_builtin_tools()
    try:
        discover_plugins()
    except Exception as exc:
        raise SystemExit(f"Hermes plugin discovery failed closed: {type(exc).__name__}") from exc

    config = load_config()
    call_budget = assert_call_budget(config)
    enabled_toolsets = sorted(_get_platform_tools(config, "api_server"))
    agent_config = config.get("agent")
    disabled_toolsets: list[str] = []
    if isinstance(agent_config, dict):
        raw_disabled = agent_config.get("disabled_toolsets")
        if isinstance(raw_disabled, list):
            disabled_toolsets = [str(value) for value in raw_disabled]

    definitions = model_tools.get_tool_definitions(
        enabled_toolsets=enabled_toolsets,
        disabled_toolsets=disabled_toolsets,
        quiet_mode=True,
    )
    tool_names = sorted({_tool_name(item) for item in definitions if isinstance(item, dict)})

    result = {
        "platform": "api_server",
        "enabledToolsets": enabled_toolsets,
        "toolSchemas": tool_names,
        "callBudget": call_budget,
    }
    if enabled_toolsets or tool_names:
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        raise SystemExit(
            "Hermes legal profile is not tool-free; refusing to start the API server"
        )

    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
