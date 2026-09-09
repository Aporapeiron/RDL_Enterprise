"""Compatibility contracts for composing compiled Functions."""

from dataclasses import dataclass
from typing import Optional

from .contracts import Provenance
from .function_types import FunctionInvocation


@dataclass(frozen=True)
class FunctionComposition:
    """Describe a possible Function connection without executing it."""

    left: FunctionInvocation
    right: FunctionInvocation
    left_output: str
    right_input: str
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.left_output, str) or not isinstance(self.right_input, str):
            raise TypeError("Functionの入力/出力意味型は文字列である必要があります")
        if not self.left_output.strip() or not self.right_input.strip():
            raise ValueError("Functionの入力/出力意味型は空にできません")

    @property
    def same_boundary(self) -> bool:
        return self.left.context == self.right.context

    @property
    def semantic_types_compatible(self) -> bool:
        return self.left_output == self.right_input

    @property
    def provenance_continuous(self) -> bool:
        return self.left.provenance is not None and self.right.provenance is not None

    @property
    def composable(self) -> bool:
        return self.same_boundary and self.semantic_types_compatible and self.provenance_continuous
