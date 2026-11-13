"""Versioned retention policy, independent of detection and model providers."""

POLICY_VERSION = "static-all-accelerate/v1"


def action_for_activity(activity: str | None) -> str:
    """Retain active/uncertain ranges; static ranges permit acceleration."""
    return "accelerate" if activity == "static" else "retain"
