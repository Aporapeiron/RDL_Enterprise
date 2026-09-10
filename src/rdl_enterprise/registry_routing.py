"""Enterprise-local routing over coexisting active function artifacts."""

from typing import Iterable, Optional

from rdl_core import ActiveCompiledMB, BoundaryContext, FunctionDescription


def select_active_for_boundary(
    active_functions: Iterable[ActiveCompiledMB],
    registry: FunctionDescription,
    context: BoundaryContext,
) -> Optional[ActiveCompiledMB]:
    """Select one exact-boundary artifact without global replacement.

    A missing or ambiguous match remains unresolved rather than falling back to
    the newest or strongest artifact.
    """
    matches = tuple(
        item for item in active_functions
        if item.registry == registry and item.context == context
    )
    return matches[0] if len(matches) == 1 else None
