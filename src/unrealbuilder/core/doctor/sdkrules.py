"""Version rules from Engine/Config/Windows/Windows_SDK.json and PE version reading."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from unrealbuilder.core.fsutil import read_json


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in text.strip().split(".") if x.isdigit())


def in_range(version: tuple[int, ...], rng: str) -> bool:
    lo, _, hi = rng.partition("-")
    lo_v, hi_v = parse_version(lo), parse_version(hi or lo)
    n = max(len(version), len(lo_v), len(hi_v))
    pad = lambda v: v + (0,) * (n - len(v))  # noqa: E731
    return pad(lo_v) <= pad(version) <= pad(hi_v)


@dataclass
class WindowsSdkRules:
    main_sdk: str = "10.0.22621.0"
    min_sdk: str = "10.0.19041.0"
    max_sdk: str = "10.9.99999.0"
    preferred_msvc: list[str] = field(default_factory=list)
    banned_msvc: list[str] = field(default_factory=list)
    minimum_msvc: str = "14.38.33130"

    @classmethod
    def load(cls, engine_root: Path) -> WindowsSdkRules:
        data = read_json(engine_root / "Engine" / "Config" / "Windows" / "Windows_SDK.json", default={}) or {}
        return cls(
            main_sdk=data.get("MainVersion", cls.main_sdk),
            min_sdk=data.get("MinVersion", cls.min_sdk),
            max_sdk=data.get("MaxVersion", cls.max_sdk),
            preferred_msvc=list(data.get("PreferredVisualCppVersions", [])),
            banned_msvc=list(data.get("BannedVisualCppVersions", [])),
            minimum_msvc=data.get("MinimumVisualCppVersion", cls.minimum_msvc),
        )

    def msvc_verdict(self, version: str) -> tuple[str, str]:
        """(status, message) with status in ok | warn | error."""
        v = parse_version(version)
        for rng in self.banned_msvc:
            if in_range(v, rng):
                return "error", f"MSVC {version} is banned by Epic ({rng})"
        if v < parse_version(self.minimum_msvc):
            return "error", f"MSVC {version} is too old (minimum {self.minimum_msvc})"
        for rng in self.preferred_msvc:
            if in_range(v, rng):
                return "ok", f"MSVC {version} (preferred version)"
        return "warn", f"MSVC {version} accepted but not preferred ({', '.join(self.preferred_msvc)})"

    def sdk_verdict(self, version: str) -> tuple[str, str]:
        v = parse_version(version)
        if not (parse_version(self.min_sdk) <= v <= parse_version(self.max_sdk)):
            return "error", f"Windows SDK {version} out of range ({self.min_sdk} – {self.max_sdk})"
        if v == parse_version(self.main_sdk):
            return "ok", f"Windows SDK {version} (reference version)"
        return "warn", f"Windows SDK {version} accepted (reference: {self.main_sdk})"


def pe_product_version(path: Path) -> str | None:
    """ProductVersion of a PE file (what UBT reads for cl.exe)."""
    try:
        import pefile
    except ImportError:
        return None
    try:
        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        for info in getattr(pe, "FileInfo", []) or []:
            for entry in info:
                for table in getattr(entry, "StringTable", []) or []:
                    value = table.entries.get(b"ProductVersion")
                    if value:
                        return value.decode(errors="replace").strip()
        if hasattr(pe, "VS_FIXEDFILEINFO") and pe.VS_FIXEDFILEINFO:
            fi = pe.VS_FIXEDFILEINFO[0]
            return f"{fi.ProductVersionMS >> 16}.{fi.ProductVersionMS & 0xFFFF}.{fi.ProductVersionLS >> 16}.{fi.ProductVersionLS & 0xFFFF}"
    except Exception:  # noqa: BLE001 - pefile raises many types on odd binaries
        return None
    return None
