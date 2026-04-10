"""Bento Lab PCR Thermocycler Control Library.

Usage:
    from bentolab import BentoLabBLE

    async with BentoLabBLE() as lab:
        status = await lab.get_status()
        profiles = await lab.list_profiles()
"""

__version__ = "0.1.0"

from .ble_client import BentoLabBLE, ProfileData
from .protocol import (
    CycleData,
    ProfileEntry,
    RunStatus,
    StageData,
    StatusBroadcast,
)

__all__ = [
    "BentoLabBLE",
    "CycleData",
    "ProfileData",
    "ProfileEntry",
    "RunStatus",
    "StageData",
    "StatusBroadcast",
]
