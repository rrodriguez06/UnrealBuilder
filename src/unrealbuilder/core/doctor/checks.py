"""Toolchain health checks. File-based (no Wine process), safe to run while a build is running."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from unrealbuilder.core.doctor.sdkrules import WindowsSdkRules, pe_product_version
from unrealbuilder.core.doctor.winereg import WineRegistry, d3dcompiler_is_native
from unrealbuilder.core.engines import engine_pairs
from unrealbuilder.core.paths import ToolchainLayout, is_within, wine_home_prefix
from unrealbuilder.core.settings import Settings, expand

NETFX_VERSIONS = ("4.6.2", "4.6.1", "4.6")
TOOLS = {
    "wine": "wine",
    "wineserver": "wine",
    "legendary": "legendary-rare (chaotic-aur)",
    "7z": "p7zip",
    "msiextract": "msitools",
    "notify-send": "libnotify",
    "xdg-open": "xdg-utils",
    "systemd-run": "systemd",
    "btrfs": "btrfs-progs",
    "git": "git",
}


@dataclass
class CheckResult:
    id: str
    title: str
    status: str  # ok | warn | error | info
    detail: str
    fix: str | None = None  # maintenance action id
    fix_label: str | None = None
    needs_license: bool = False
    group: str = "Wine prefix"


def _check_prefix(layout: ToolchainLayout) -> CheckResult:
    real = Path(os.path.realpath(layout.prefix))
    if is_within(real, Path(os.path.realpath(wine_home_prefix()))) or real == Path(os.path.realpath(wine_home_prefix())):
        return CheckResult("prefix", "Dedicated Wine prefix", "error", "The prefix points to ~/.wine: forbidden")
    if not is_within(real, Path(os.path.realpath(layout.root))):
        return CheckResult("prefix", "Dedicated Wine prefix", "error", f"Prefix {real} must be inside {layout.root}")
    if not (real / "system.reg").is_file():
        return CheckResult("prefix", "Dedicated Wine prefix", "error", f"Prefix missing or not initialized: {real}", "prefix-create", "Create prefix")
    return CheckResult("prefix", "Dedicated Wine prefix", "ok", str(real))


def _check_drive(layout: ToolchainLayout) -> CheckResult:
    link = layout.prefix / "dosdevices" / f"{layout.drive}:"
    target = os.path.realpath(link) if link.is_symlink() else None
    if target and Path(target) == Path(os.path.realpath(layout.ue_root)):
        return CheckResult("drive", f"{layout.drive.upper()}: drive", "ok", f"→ {target}")
    return CheckResult("drive", f"{layout.drive.upper()}: drive", "error", f"The drive must point to {layout.ue_root}", "drive-link", "Recreate link")


def _check_d3dcompiler(layout: ToolchainLayout) -> CheckResult:
    if d3dcompiler_is_native(layout.prefix):
        return CheckResult("d3dcompiler", "d3dcompiler_47 = native", "ok", "SM5 shaders are compiled by FXC (Microsoft)")
    return CheckResult(
        "d3dcompiler",
        "d3dcompiler_47 = native",
        "error",
        "Without this override, Wine swaps FXC for its own HLSL compiler: hangs and wrong SM5 shaders",
        "dll-override",
        "Apply override",
    )


def _check_registry(layout: ToolchainLayout) -> list[CheckResult]:
    reg = WineRegistry.load(
        layout.prefix / "system.reg",
        ("software\\microsoft\\vscommon", "software\\wow6432node\\microsoft\\vscommon", "software\\microsoft\\visualstudio\\14.0\\vc\\runtimes"),
    )
    out = []
    opt64 = reg.get(r"Software\Microsoft\VSCommon\17.0\SQM", "OptIn")
    opt32 = reg.get(r"Software\Wow6432Node\Microsoft\VSCommon\17.0\SQM", "OptIn")
    if opt64 == 0 and opt32 == 0:
        out.append(CheckResult("vctip", "MSVC telemetry disabled", "ok", "vctip.exe will not crash compilations"))
    else:
        out.append(CheckResult("vctip", "MSVC telemetry disabled", "warn", "vctip.exe can crash under Wine and fail a compilation", "vctip", "Disable"))
    installed = reg.get(r"Software\Microsoft\VisualStudio\14.0\VC\Runtimes\x64", "Installed")
    version = reg.get(r"Software\Microsoft\VisualStudio\14.0\VC\Runtimes\x64", "Version")
    if installed == 1:
        out.append(CheckResult("vcredist", "Visual C++ x64 runtime", "ok", str(version or "installed")))
    else:
        out.append(CheckResult("vcredist", "Visual C++ x64 runtime", "error", "Required by the Windows editor", "vcredist", "Install"))
    return out


def _check_autosdk(layout: ToolchainLayout, settings: Settings) -> list[CheckResult]:
    out = []
    eng = next(iter(settings.engines.values()), None)
    autosdk = expand(eng.autosdk_root) if eng else layout.sdks
    win64 = autosdk / "HostWin64" / "Win64"
    vs = win64 / "VS2022"
    kits = win64 / "Windows Kits"
    ok_links = vs.is_dir() and kits.is_dir()
    out.append(
        CheckResult(
            "autosdk",
            "AutoSDK layout",
            "ok" if ok_links else "error",
            str(win64) if ok_links else "VS2022 / Windows Kits links missing or broken",
            None if ok_links else "autosdk-links",
            None if ok_links else "Recreate links",
            group="Compiler",
        )
    )
    rules = None
    for e in settings.engines.values():
        if expand(e.win_root).exists():
            rules = WindowsSdkRules.load(expand(e.win_root))
            break
    rules = rules or WindowsSdkRules()
    cls = sorted(vs.glob("*/bin/Hostx64/x64/cl.exe")) if vs.is_dir() else []
    if not cls:
        out.append(
            CheckResult("msvc", "MSVC (cl.exe)", "error", "No MSVC installed", "msvc-install", "Install MSVC", True, group="Compiler")
        )
    else:
        version = pe_product_version(cls[-1]) or cls[-1].parts[-5]
        status, msg = rules.msvc_verdict(version)
        out.append(
            CheckResult(
                "msvc",
                "MSVC (cl.exe)",
                status,
                f"{msg} — {cls[-1].parts[-5]}",
                "msvc-install" if status == "error" else None,
                "Reinstall MSVC" if status == "error" else None,
                status == "error",
                group="Compiler",
            )
        )
    includes = sorted((kits / "10" / "Include").glob("10.*")) if kits.is_dir() else []
    if not includes:
        out.append(CheckResult("winsdk", "Windows SDK", "error", "Windows SDK missing", "msvc-install", "Install MSVC + SDK", True, group="Compiler"))
    else:
        status, msg = rules.sdk_verdict(includes[-1].name)
        out.append(CheckResult("winsdk", "Windows SDK", status, msg, group="Compiler"))
    netfx = [v for v in NETFX_VERSIONS if (kits / "NETFXSDK" / v / "Include" / "um" / "mscoree.h").is_file()]
    if netfx:
        out.append(CheckResult("netfx", ".NET Framework SDK (NETFXSDK)", "ok", f"Version {netfx[0]}", group="Compiler"))
    else:
        out.append(
            CheckResult(
                "netfx",
                ".NET Framework SDK (NETFXSDK)",
                "error",
                "Required to compile the editor target of C++ projects (SwarmInterface module)",
                "netfx-install",
                "Install (≈ 5 MB)",
                True,
                group="Compiler",
            )
        )
    return out


def _check_engines(settings: Settings) -> list[CheckResult]:
    out = []
    for version, pair in engine_pairs(settings).items():
        title = f"UE {version} engine"
        if not pair.win.exists:
            out.append(
                CheckResult(f"engine-{version}", title, "error", f"Windows engine missing: {pair.win.root}", f"engine-install:{version}", "Install (legendary)", group="Engines")
            )
            continue
        lv, wv = pair.linux.build_version, pair.win.build_version
        if pair.changelists_match:
            out.append(CheckResult(f"engine-{version}", title, "ok", f"Linux and Windows both on {wv.label}", group="Engines"))
        elif pair.compatible:
            out.append(
                CheckResult(
                    f"engine-{version}",
                    title,
                    "warn",
                    f"Linux {lv.label if lv else '?'} / Windows {wv.label if wv else '?'} (compatible)",
                    f"engine-update:{version}",
                    "Update",
                    group="Engines",
                )
            )
        else:
            out.append(
                CheckResult(
                    f"engine-{version}",
                    title,
                    "error",
                    f"Linux {lv.label if lv else '?'} / Windows {wv.label if wv else '?'}: versions differ",
                    f"engine-update:{version}",
                    "Update",
                    group="Engines",
                )
            )
        dll = pair.win.engine_dir / "Binaries" / "ThirdParty" / "Windows" / "DirectX" / "x64" / "d3dcompiler_47.dll"
        if not dll.is_file():
            out.append(CheckResult(f"engine-{version}-fxc", f"{title}: d3dcompiler_47.dll", "error", "Microsoft FXC DLL missing from the engine", group="Engines"))
    return out


def _check_system(layout: ToolchainLayout, settings: Settings) -> list[CheckResult]:
    out = []
    missing = [f"{tool} ({pkg})" for tool, pkg in TOOLS.items() if not shutil.which(tool)]
    out.append(
        CheckResult(
            "tools",
            "System tools",
            "ok" if not missing else "warn",
            "All present" if not missing else "Missing: " + ", ".join(missing),
            group="System",
        )
    )
    legendary_user = Path.home() / ".config" / "legendary" / "user.json"
    out.append(
        CheckResult(
            "legendary",
            "legendary login (Epic)",
            "ok" if legendary_user.exists() else "warn",
            "Logged in" if legendary_user.exists() else "Run `legendary auth` in a terminal",
            group="System",
        )
    )
    try:
        free = shutil.disk_usage(layout.root if layout.root.exists() else layout.root.parent).free / 2**30
        status = "ok" if free > 50 else ("warn" if free > 20 else "error")
        out.append(CheckResult("disk", "Disk space (toolchain)", status, f"{free:.0f} GiB free", group="System"))
    except OSError:
        pass
    try:
        state = subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        state = "unknown"
    out.append(
        CheckResult(
            "systemd",
            "systemd --user",
            "ok" if state in ("running", "degraded") else "error",
            f"State: {state}" + (" (some user units failed, harmless)" if state == "degraded" else ""),
            group="System",
        )
    )
    try:
        wine = subprocess.run([settings.wine.wine, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        wine = ""
    out.append(CheckResult("wine", "Wine", "ok" if wine else "error", wine or "wine not found", group="System"))
    return out


def _check_slots(settings: Settings) -> list[CheckResult]:
    from unrealbuilder.core.slots import slots

    out = []
    if settings.resources.max_concurrent_builds < 2:
        return out
    for slot in slots(settings)[1 : settings.resources.max_concurrent_builds]:
        if slot.exists:
            out.append(CheckResult(f"slot-{slot.index}", f"Parallel slot {slot.index}", "ok", f"{slot.prefix} (Zen {slot.zen_port})"))
        else:
            out.append(
                CheckResult(
                    f"slot-{slot.index}",
                    f"Parallel slot {slot.index}",
                    "warn",
                    "Second slot prefix missing: builds will stay sequential",
                    f"slot-create:{slot.index}",
                    "Create (reflink clone)",
                )
            )
    return out


def run_checks(settings: Settings) -> list[CheckResult]:
    layout = ToolchainLayout.from_settings(settings)
    results = [_check_prefix(layout)]
    if results[0].status != "error" or results[0].fix == "prefix-create":
        results.append(_check_drive(layout))
        results.append(_check_d3dcompiler(layout))
        results.extend(_check_registry(layout))
        results.extend(_check_slots(settings))
    results.extend(_check_autosdk(layout, settings))
    results.extend(_check_engines(settings))
    results.extend(_check_system(layout, settings))
    return results
