"""Shared descriptions for bounded Core functions and evaluators."""

from dataclasses import dataclass


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

