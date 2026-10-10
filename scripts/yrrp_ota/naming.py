"""Release identity shared by every channel: version, ID shapes, and vanilla names."""
from __future__ import annotations

import re

from .channel import BUILD_ID_PATTERN, INCREMENTAL_PATTERN, LINEAGE_VERSION, VANILLA

DEVICE = VANILLA.device
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

__all__ = ["BUILD_ID_PATTERN", "DEVICE", "INCREMENTAL_PATTERN", "LINEAGE_VERSION", "SHA256_PATTERN"]
