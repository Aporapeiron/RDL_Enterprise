"""Current-state projection over immutable Compiled M_B lifecycle records."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .activation_types import ActiveCompiledMB
from .deactivation_types import DeactivationRecord, DeactivationStatus


class RegistryStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class CurrentFunctionState:
    active: ActiveCompiledMB
    status: RegistryStatus
    deactivation: Optional[DeactivationRecord] = None


def project_current_function_state(
    active: ActiveCompiledMB,
    *,
    deactivation: Optional[DeactivationRecord] = None,
) -> CurrentFunctionState:
    """Project current status without deleting activation/deactivation history."""
    if deactivation is None:
        return CurrentFunctionState(active, RegistryStatus.ACTIVE)
    if deactivation.active != active:
        raise ValueError("DeactivationRecordが対象ActiveCompiledMBと一致していません")
    status = (
        RegistryStatus.INACTIVE
        if deactivation.status == DeactivationStatus.DEACTIVATED
        else RegistryStatus.UNRESOLVED
    )
    return CurrentFunctionState(active, status, deactivation)
