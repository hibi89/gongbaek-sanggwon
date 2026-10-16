#!/usr/bin/env python3
"""Broad plausibility bounds for coordinates in a South Korea store snapshot."""

from __future__ import annotations

import math


# Intentionally wider than the observed national snapshot to include Jeju,
# remote islands and small source-provider coordinate shifts.
KOREA_LONGITUDE_MIN = 124.0
KOREA_LONGITUDE_MAX = 132.5
KOREA_LATITUDE_MIN = 32.5
KOREA_LATITUDE_MAX = 39.5


def is_korea_plausible(longitude: float, latitude: float) -> bool:
    return (
        math.isfinite(longitude)
        and math.isfinite(latitude)
        and KOREA_LONGITUDE_MIN <= longitude <= KOREA_LONGITUDE_MAX
        and KOREA_LATITUDE_MIN <= latitude <= KOREA_LATITUDE_MAX
    )


def korea_bbox() -> dict[str, float]:
    return {
        "longitude_min": KOREA_LONGITUDE_MIN,
        "longitude_max": KOREA_LONGITUDE_MAX,
        "latitude_min": KOREA_LATITUDE_MIN,
        "latitude_max": KOREA_LATITUDE_MAX,
    }
