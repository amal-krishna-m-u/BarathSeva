"""Provider-agnostic inference contract.

AI is used for exactly one class of problem: unstructured judgment that no rule
can express — reading a sentence, looking at a photo, deciding whether the two
agree, and writing a sentence a citizen will read. Everything a provider
returns is coerced back into a controlled vocabulary by the caller before it
reaches the database.

Keeping the surface this narrow is what makes the model layer swappable: a
provider only has to turn an ``InferenceRequest`` into a JSON object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


class Task:
    """Named inference tasks. The stub provider branches on these."""

    VERIFY = "verify"
    CLASSIFY = "classify"
    CLUSTER_SUMMARY = "cluster_summary"
    SOCIAL = "social"
    RESOLUTION = "resolution"


@dataclass
class InferenceRequest:
    task: str
    system: str
    user: str
    schema: dict[str, Any]
    #: Structured, already-verified facts. Deterministic providers read these
    #: directly; model providers receive them as serialised context.
    context: dict[str, Any] = field(default_factory=dict)
    image_bytes: Optional[bytes] = None
    image_mime: Optional[str] = None


@dataclass
class InferenceResult:
    data: dict[str, Any]
    rationale: str = ""
    confidence: float = 0.5
    provider: str = "stub"
    model: str = "deterministic"
    latency_ms: int = 0
    is_ai: bool = False
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@runtime_checkable
class AIProvider(Protocol):
    name: str
    model: str
    is_ai: bool

    def infer(self, request: InferenceRequest) -> InferenceResult: ...
