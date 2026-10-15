"""MSVC + Windows SDK + .NET Framework SDK through msvc-wine (Microsoft license accepted per run, by the user)."""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from unrealbuilder.core.paths import ToolchainLayout

Run = Callable[[list[str], dict[str, str] | None], int]
MSVC_WINE_REPO = "https://github.com/mstorsjo/msvc-wine.git"
LICENSE_URL = "https://go.microsoft.com/fwlink/?LinkId=2179911"
NETFX_COMPONENT = "Microsoft.Net.Component.4.6.2.SDK"
NETFX_PACKAGE_PREFIX = "Microsoft.Net.4.6.2.SDK-"
NETFX_VERSION = "4.6.2"


class LicenseNotAccepted(RuntimeError):
    pass


def ensure_msvc_wine(layout: ToolchainLayout, run: Run) -> Path:
    repo = layout.msvc_wine
    if (repo / "vsdownload.py").is_file():
        run(["git", "-C", str(repo), "pull", "--ff-only"], None)
    else:
        rc = run(["git", "clone", "--depth", "1", MSVC_WINE_REPO, str(repo)], None)
        if rc != 0:
            raise RuntimeError("git clone of msvc-wine failed")
    return repo


def vsdownload_argv(repo: Path, dest: Path, accept_license: bool) -> list[str]:
    if not accept_license:
        raise LicenseNotAccepted("The Microsoft license must be accepted explicitly")
    return [
        sys.executable,
        "-I",
        str(repo / "vsdownload.py"),
        "--accept-license",
        "--major",
        "17",
        "--sdk-version",
        "10.0.22621",
        "--architecture",
        "x64",
        "--host-arch",
        "x64",
        "--only-host",
        "yes",
        "--with-asan",
        "no",
        "--with-msbuild",
        "no",
        "--with-devcmd",
        "no",
        "--dest",
        str(dest),
    ]


def install_msvc(layout: ToolchainLayout, run: Run, accept_license: bool) -> int:
    repo = ensure_msvc_wine(layout, run)
    return run(vsdownload_argv(repo, layout.msvc, accept_license), None)


def netfx_installed(layout: ToolchainLayout) -> bool:
    return (layout.msvc / "Windows Kits" / "NETFXSDK" / NETFX_VERSION / "Include" / "um" / "mscoree.h").is_file()


def install_netfx(layout: ToolchainLayout, run: Run, accept_license: bool) -> int:
    """Epic's AutoSDK recipe: download the 4.6.2 SDK package, msiextract sdk_tools462.msi, keep NETFXSDK."""
    if not accept_license:
        raise LicenseNotAccepted("The Microsoft license must be accepted explicitly")
    repo = ensure_msvc_wine(layout, run)
    with tempfile.TemporaryDirectory(prefix="ub-netfx-", dir=layout.root) as tmp:
        cache = Path(tmp) / "cache"
        rc = run(
            [sys.executable, "-I", str(repo / "vsdownload.py"), "--accept-license", "--major", "17", "--only-download", "--cache", str(cache), NETFX_COMPONENT],
            None,
        )
        if rc != 0:
            return rc
        pkgs = sorted(cache.glob(f"{NETFX_PACKAGE_PREFIX}*"))
        msis = [m for p in pkgs for m in p.glob("sdk_tools*.msi")]
        if not msis:
            raise FileNotFoundError(f"sdk_tools*.msi not found in {cache}")
        out = Path(tmp) / "x"
        out.mkdir()
        rc = run(["msiextract", "-C", str(out), str(msis[0])], None)
        if rc != 0:
            return rc
        found = [p for p in out.rglob("NETFXSDK") if p.is_dir()]
        if not found:
            raise FileNotFoundError("NETFXSDK folder missing from the extracted MSI")
        dest = layout.msvc / "Windows Kits" / "NETFXSDK"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(found[0], dest, dirs_exist_ok=True)
    return 0 if netfx_installed(layout) else 1
