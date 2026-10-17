"""Structured safety decision contracts."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyDecision:
    action: str
    code: str
    response: str | None = None

