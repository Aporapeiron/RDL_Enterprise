"""Shared descriptions for bounded Core functions and evaluators."""

from dataclasses import dataclass
from typing import Mapping, Optional

from .contracts import BoundaryContext, FrozenBoundaryValue, Provenance, freeze_boundary_value


@dataclass(frozen=True)
class FunctionDescription:
    """Identity of a local evaluator; identity is not a truth claim."""

    function_id: str
    version: str

    def __post_init__(self) -> None:
        if not isinstance(self.function_id, str) or not isinstance(self.version, str):
            raise TypeError("function_id/versionは文字列である必要があります")
        if not self.function_id.strip() or not self.version.strip():
            raise ValueError("function_id/versionは空にできません")


@dataclass(frozen=True)
class FunctionInvocation:
    """Recoverable application of a function under one finite boundary."""

    function: FunctionDescription
    context: BoundaryContext
    purpose: str = ""
    config: Mapping[str, FrozenBoundaryValue] = ()
    provenance: Optional[Provenance] = None

    def __post_init__(self) -> None:
        if not isinstance(self.function, FunctionDescription):
            raise TypeError("functionはFunctionDescriptionである必要があります")
        if not isinstance(self.context, BoundaryContext):
            raise TypeError("contextはBoundaryContextである必要があります")
        if not isinstance(self.purpose, str):
            raise TypeError("purposeは文字列である必要があります")
        if self.config == ():
            frozen_config = freeze_boundary_value({})
        elif not isinstance(self.config, Mapping):
            raise TypeError("configは文字列キーのmappingである必要があります")
        else:
            frozen_config = freeze_boundary_value(dict(self.config))
        object.__setattr__(self, "config", frozen_config)
