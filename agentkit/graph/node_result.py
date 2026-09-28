"""
NodeResult: how every graph node ends.

A refusal (DECLINED) or a hand-off to a person (HANDED_OFF) is a normal
*result*, not a failure: the graph keeps going and the finding reports it.
Only an unexpected problem is FAILED, and it keeps the transport's error
kind so the run record says why. BYPASSED means the node never ran because
a dependency it required did not resolve.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

RESOLVED = "resolved"
DECLINED = "declined"
HANDED_OFF = "handed_off"
FAILED = "failed"
BYPASSED = "bypassed"
VERDICTS = (RESOLVED, DECLINED, HANDED_OFF, FAILED, BYPASSED)


@dataclass
class NodeResult:
    verdict: str
    data: Any = None
    reason: Optional[str] = None
    error_kind: Optional[str] = None

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"unknown node verdict {self.verdict!r}")

    @property
    def resolved(self) -> bool:
        return self.verdict == RESOLVED

    @classmethod
    def of(cls, value: Any) -> "NodeResult":
        return value if isinstance(value, NodeResult) else cls(RESOLVED, data=value)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NodeResult":
        return cls(**d)


def declined(reason: str, data: Any = None, error_kind: Optional[str] = None) -> NodeResult:
    return NodeResult(DECLINED, data=data, reason=reason, error_kind=error_kind)


def handed_off(reason: str, data: Any = None) -> NodeResult:
    return NodeResult(HANDED_OFF, data=data, reason=reason)
