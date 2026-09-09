"""Bounded comparison records for compiled Functions."""

from dataclasses import dataclass

from .function_types import FunctionInvocation


@dataclass(frozen=True)
class FunctionComparison:
    """Compare two Function invocations without asserting which is true."""

    left: FunctionInvocation
    right: FunctionInvocation

    @property
    def same_boundary(self) -> bool:
        return self.left.context == self.right.context

    @property
    def same_purpose(self) -> bool:
        return self.left.purpose == self.right.purpose

    @property
    def same_config(self) -> bool:
        return self.left.config == self.right.config

    @property
    def comparable(self) -> bool:
        return self.same_boundary and self.same_purpose and self.same_config

    @property
    def same_function(self) -> bool:
        return self.left.function == self.right.function
