"""Fail-closed configuration for a synthetic-only, non-production preview."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class PreviewMode(StrEnum):
    OFF = "off"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True)
class PreviewSettings:
    mode: PreviewMode = PreviewMode.OFF
    tester_ids: frozenset[int] = frozenset()
    api_key: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.mode, PreviewMode):
            raise ValueError("invalid personal preview mode")
        if not isinstance(self.tester_ids, frozenset):
            raise ValueError("invalid personal preview testers")
        if any(type(item) is not int or not 0 < item < 2**52 for item in self.tester_ids):
            raise ValueError("invalid personal preview testers")
        if len(self.tester_ids) > 32:
            raise ValueError("too many personal preview testers")
        if self.mode is PreviewMode.SYNTHETIC:
            if not self.tester_ids:
                raise ValueError("personal preview requires explicit testers")
            if not isinstance(self.api_key, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{32,128}", self.api_key,
            ):
                raise ValueError("personal preview requires a separate strong API key")

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> "PreviewSettings":
        raw = env.get("PERSONAL_PREVIEW_MODE", "off")
        if raw == "off":
            # Disabled mode does not need a token, testers, a DB or clinic credentials.
            return cls()
        if raw != "synthetic":
            raise ValueError("PERSONAL_PREVIEW_MODE must be off or synthetic")
        raw_ids = env.get("PERSONAL_PREVIEW_TESTER_IDS", "")
        if not re.fullmatch(r"[0-9]+(?:,[0-9]+){0,31}", raw_ids):
            raise ValueError("invalid personal preview testers")
        identifiers = [int(value) for value in raw_ids.split(",")]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate personal preview tester")
        key = env.get("PERSONAL_PREVIEW_API_KEY", "")
        if key and key in {
            env.get("AGENT_INTERNAL_KEY"), env.get("HERMES_RESEARCHER_API_KEY"),
            env.get("HERMES_REVIEWER_API_KEY"), env.get("TELEGRAM_BOT_TOKEN"),
            env.get("PERSONAL_TELEGRAM_BOT_TOKEN"),
        }:
            raise ValueError("personal and clinic credentials must be distinct")
        return cls(PreviewMode.SYNTHETIC, frozenset(identifiers), key)

    def allows_tester(self, actor: int | None) -> bool:
        return (
            self.mode is PreviewMode.SYNTHETIC
            and type(actor) is int
            and actor in self.tester_ids
        )


def personal_bot_token(env: Mapping[str, str], settings: PreviewSettings) -> str | None:
    if settings.mode is PreviewMode.OFF:
        return None
    token = env.get("PERSONAL_TELEGRAM_BOT_TOKEN", "")
    if not re.fullmatch(r"[1-9][0-9]{4,15}:[A-Za-z0-9_-]{30,100}", token):
        raise ValueError("personal preview requires its own bot token")
    # Compare bot IDs too: rotating a token does not create a different bot.
    clinic_id = env.get("CLINIC_TELEGRAM_BOT_ID")
    clinic_token = env.get("TELEGRAM_BOT_TOKEN", "")
    if clinic_token:
        if not re.fullmatch(r"[1-9][0-9]{4,15}:[A-Za-z0-9_-]{30,100}", clinic_token):
            raise ValueError("invalid clinic bot identity")
        from_token = clinic_token.split(":", 1)[0]
        if clinic_id is not None and clinic_id != from_token:
            raise ValueError("conflicting clinic bot identity")
        clinic_id = from_token
    if clinic_id is None or not re.fullmatch(r"[1-9][0-9]{4,15}", clinic_id):
        raise ValueError("clinic bot ID is required to prevent accidental reuse")
    if token.split(":", 1)[0] == clinic_id:
        raise ValueError("personal preview must not run on the clinic bot")
    return token
