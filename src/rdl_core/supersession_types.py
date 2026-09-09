"""Explicit v1-to-v2 Active Compiled M_B replacement records."""

from dataclasses import dataclass
from typing import Optional

from .activation_types import ActiveCompiledMB
from .contracts import BoundaryContext, Provenance
from .recompilation_types import CompiledReplacement


@dataclass(frozen=True)
class SupersessionRecord:
    predecessor: ActiveCompiledMB
    replacement: ActiveCompiledMB
    compiled_replacement: CompiledReplacement
    context: BoundaryContext
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if self.compiled_replacement.predecessor != self.predecessor.artifact:
            raise ValueError("Supersessionのpredecessorが一致していません")
        if self.compiled_replacement.compiled != self.replacement.artifact:
            raise ValueError("Supersessionのreplacementが一致していません")
        if self.predecessor.artifact == self.replacement.artifact:
            raise ValueError("Supersessionには異なるCompiledMBが必要です")
