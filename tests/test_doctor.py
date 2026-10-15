import json

import pytest

from unrealbuilder.core.doctor.checks import run_checks
from unrealbuilder.core.doctor.sdkrules import WindowsSdkRules, in_range, parse_version
from unrealbuilder.core.doctor.winereg import WineRegistry, d3dcompiler_is_native
from unrealbuilder.core.install.msvc import LicenseNotAccepted, install_netfx, vsdownload_argv
from unrealbuilder.core.install.prefix import ensure_autosdk_links, ensure_drive_link
from unrealbuilder.core.maint import create_maint, label, list_maint, split_action
from unrealbuilder.core.paths import ToolchainLayout
from unrealbuilder.core.settings import EngineSettings, Settings

USER_REG = '''WINE REGISTRY Version 2
;; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000

[Software\\\\Wine\\\\DllOverrides] 1792057827
#time=1dd5c8a9b842784
"d3dcompiler_47"="native"
'''
SYSTEM_REG = '''WINE REGISTRY Version 2

[Software\\\\Microsoft\\\\VSCommon\\\\17.0\\\\SQM] 1792052328
"OptIn"=dword:00000000

[Software\\\\Microsoft\\\\VisualStudio\\\\14.0\\\\VC\\\\Runtimes\\\\x64] 1792052364
"Installed"=dword:00000001
"Version"="v14.50.35719.00"

[Software\\\\Wow6432Node\\\\Microsoft\\\\VSCommon\\\\17.0\\\\SQM] 1792052334
"OptIn"=dword:00000000
'''


def test_registry_parsing(tmp_path):
    (tmp_path / "user.reg").write_text(USER_REG)
    (tmp_path / "system.reg").write_text(SYSTEM_REG)
    assert d3dcompiler_is_native(tmp_path)
    reg = WineRegistry.load(tmp_path / "system.reg")
    assert reg.get(r"Software\Microsoft\VSCommon\17.0\SQM", "OptIn") == 0
    assert reg.get(r"Software\Microsoft\VisualStudio\14.0\VC\Runtimes\x64", "Version") == "v14.50.35719.00"
    (tmp_path / "user.reg").write_text(USER_REG.replace('"native"', '"builtin"'))
    assert not d3dcompiler_is_native(tmp_path)


def test_version_rules():
    rules = WindowsSdkRules(
        preferred_msvc=["14.50.35717-14.50.99999", "14.44.35207-14.44.99999"],
        banned_msvc=["14.50.0-14.50.35722", "14.44.0-14.44.35210", "14.40.0-14.43.99999"],
        minimum_msvc="14.38.33130",
    )
    assert rules.msvc_verdict("14.44.35229.0")[0] == "ok"
    assert rules.msvc_verdict("14.44.35207")[0] == "error"  # the folder name of a good toolchain: banned
    assert rules.msvc_verdict("14.42.1")[0] == "error"
    assert rules.msvc_verdict("14.38.33135")[0] == "warn"
    assert rules.msvc_verdict("14.30.0")[0] == "error"
    assert rules.sdk_verdict("10.0.22621.0")[0] == "ok"
    assert rules.sdk_verdict("10.0.26100.0")[0] == "warn"
    assert rules.sdk_verdict("10.0.17763.0")[0] == "error"
    assert in_range(parse_version("14.44.35210"), "14.44.0-14.44.35210")


def make_toolchain(tmp_path, with_netfx=False):
    ue = tmp_path / "UE"
    t = ue / "win64-toolchain"
    prefix = t / "wineprefix"
    prefix.mkdir(parents=True)
    (prefix / "user.reg").write_text(USER_REG)
    (prefix / "system.reg").write_text(SYSTEM_REG)
    msvc = t / "msvc"
    (msvc / "VC" / "Tools" / "MSVC" / "14.44.35207" / "bin" / "Hostx64" / "x64").mkdir(parents=True)
    (msvc / "Windows Kits" / "10" / "Include" / "10.0.22621.0").mkdir(parents=True)
    (msvc / "DIA SDK").mkdir()
    if with_netfx:
        inc = msvc / "Windows Kits" / "NETFXSDK" / "4.6.2" / "Include" / "um"
        inc.mkdir(parents=True)
        (inc / "mscoree.h").write_text("")
    bv = {"MajorVersion": 5, "MinorVersion": 8, "PatchVersion": 3, "Changelist": 1, "CompatibleChangelist": 1}
    for name in ("UE_5.8", "UE_5.8_Win64"):
        (ue / name / "Engine" / "Build").mkdir(parents=True)
        (ue / name / "Engine" / "Build" / "Build.version").write_text(json.dumps(bv))
    s = Settings()
    s.paths.ue_root = str(ue)
    s.paths.toolchain_root = str(t)
    s.wine.prefix = str(prefix)
    s.wine.wine = "/nonexistent/wine"
    s.engines = {"5.8": EngineSettings(linux_root=str(ue / "UE_5.8"), win_root=str(ue / "UE_5.8_Win64"), legendary_app="UE_5.8", autosdk_root=str(t / "sdks"))}
    layout = ToolchainLayout.from_settings(s)
    return s, layout


def statuses(settings):
    return {c.id: (c.status, c.fix) for c in run_checks(settings)}


def test_checks_detect_and_fix_links(tmp_path):
    s, layout = make_toolchain(tmp_path)
    st = statuses(s)
    assert st["prefix"][0] == "ok" and st["d3dcompiler"][0] == "ok" and st["vctip"][0] == "ok" and st["vcredist"][0] == "ok"
    assert st["drive"] == ("error", "drive-link")
    assert st["autosdk"] == ("error", "autosdk-links")
    assert st["netfx"] == ("error", "netfx-install")
    assert st["engine-5.8"][0] == "ok"
    ensure_drive_link(layout)
    ensure_autosdk_links(layout, layout.sdks)
    st = statuses(s)
    assert st["drive"][0] == "ok" and st["autosdk"][0] == "ok"
    assert st["msvc"][1] == "msvc-install"  # no cl.exe in the fake tree


def test_netfx_detected(tmp_path):
    s, layout = make_toolchain(tmp_path, with_netfx=True)
    ensure_autosdk_links(layout, layout.sdks)
    assert statuses(s)["netfx"][0] == "ok"


def test_license_is_mandatory(tmp_path):
    _s, layout = make_toolchain(tmp_path)
    with pytest.raises(LicenseNotAccepted):
        vsdownload_argv(layout.msvc_wine, layout.msvc, accept_license=False)
    with pytest.raises(LicenseNotAccepted):
        install_netfx(layout, lambda argv, env: 0, accept_license=False)
    assert "--accept-license" in vsdownload_argv(layout.msvc_wine, layout.msvc, accept_license=True)


def test_maint_dirs():
    m = create_maint("engine-update:5.8", {"x": 1})
    assert split_action("engine-update:5.8") == ("engine-update", "5.8")
    assert label("engine-update:5.8") == "Update Windows engine (5.8)"
    assert m.action()["params"] == {"x": 1}
    assert list_maint()[0].root == m.root
