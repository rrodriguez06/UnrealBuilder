"""Known-benign lines hidden by default in the log views."""

from __future__ import annotations

import re

NOISE_PATTERNS = [
    r"^MESA-EGL:",
    r"^MESA:",
    r"pci id for fd \d+",
    r"egl: failed to create dri2 screen",
    r"Failed to load '(?:aqProf|VtuneApi|VtuneApi32e|WinPixGpuCapturer)\.dll'",
    r"File '(?:aqProf|VtuneApi|VtuneApi32e|WinPixGpuCapturer)\.dll' does not exist",
    r"PIX capture plugin failed to initialize",
    r"^\s*$",
]

_NOISE_RE = re.compile("|".join(f"(?:{p})" for p in NOISE_PATTERNS))


def is_noise(text: str) -> bool:
    return bool(_NOISE_RE.search(text))
