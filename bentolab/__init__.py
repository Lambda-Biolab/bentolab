"""Bento Lab PCR Thermocycler Control Library.

Usage:
    from bentolab import BentoLabBLE

    async with BentoLabBLE() as lab:
        status = await lab.get_status()
        profiles = await lab.list_profiles()

        # Run PCR with progress tracking
        async for state in lab.run_pcr(
            stages=[(95, 180), (95, 30), (58, 30), (72, 60), (72, 300)],
            cycles=[(4, 2, 35)],
        ):
            print(f"Block: {state.block_temperature}°C")
"""

__version__ = "0.1.0"

from .ble_client import (
    BentoLabBLE,
    BentoLabCommandError,
    BentoLabConnectionError,
    BentoLabError,
    PCRRunState,
    ProfileData,
)
from .models import (
    CycleStep,
    DeviceState,
    DeviceStatus,
    PCRProfile,
    ThermalStep,
)
from .protocol import (
    CycleData,
    ProfileEntry,
    RunStatus,
    StageData,
    StatusBroadcast,
    TouchdownStageData,
)

__all__ = [
    "BentoLabBLE",
    "BentoLabCommandError",
    "BentoLabConnectionError",
    "BentoLabError",
    "CycleData",
    "CycleStep",
    "DeviceState",
    "DeviceStatus",
    "PCRProfile",
    "PCRRunState",
    "ProfileData",
    "ProfileEntry",
    "RunStatus",
    "StageData",
    "StatusBroadcast",
    "ThermalStep",
    "TouchdownStageData",
]
