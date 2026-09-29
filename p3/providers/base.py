"""Provider interface shared by the fal.ai adapter and the mock.

The queue-style contract (submit → status → result, addressed by request id)
is what makes restart reconciliation possible: a persisted request id can be
polled again after a restart without resubmitting paid work.
"""

from dataclasses import dataclass
from typing import Protocol


class ProviderError(Exception):
    """Permanent failure for this request (bad input, content policy, billing, provider error)."""

    def __init__(self, Message: str, Code: str = "provider_error"):
        super().__init__(Message)
        self.Code = Code


class TransientProviderError(Exception):
    """Temporary failure reading/submitting (network, 429, 5xx). Safe to retry the same call."""


@dataclass
class ProviderStatus:
    State: str                 # "queued" | "running" | "completed"
    Error: str | None = None   # set when State == "completed" but the request failed


class Provider(Protocol):
    Name: str

    async def Upload(self, Data: bytes, ContentType: str) -> str: ...
    async def Submit(self, Endpoint: str, Arguments: dict) -> str: ...
    async def Status(self, Endpoint: str, RequestId: str) -> ProviderStatus: ...
    async def Result(self, Endpoint: str, RequestId: str) -> dict: ...
    async def Download(self, Url: str) -> bytes: ...


def ClassifyErrorMessage(Message: str) -> tuple[str, str]:
    """Map raw provider text to (customer message, error_code). Adapted from P2 _ClassifyFalError."""
    Msg = (Message or "").lower()
    if any(K in Msg for K in ("payment", "billing", "budget", "insufficient", "quota exceeded", "402")):
        return ("Service temporarily unavailable due to a billing issue. "
                "Please contact the site administrator.", "billing")
    if any(K in Msg for K in ("content_policy_violation", "content policy",
                              "content could not be processed", "flagged by a content checker")):
        return ("We couldn't process this image or description because it may not meet our "
                "content guidelines. Please revise your description.", "content_policy")
    return (Message or "The generation service returned an error.", "provider_error")
