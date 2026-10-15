import json
from pathlib import Path

import pytest

from unrealbuilder.core.engines import EngineInstall, EnginePair
from unrealbuilder.core.settings import EngineSettings
from unrealbuilder.core.uproject import ProjectDescriptor, RefStatus, analyze_project

BUILD_VERSION = {"MajorVersion": 5, "MinorVersion": 8, "PatchVersion": 3, "Changelist": 1, "CompatibleChangelist": 1}


def make_engine(root: Path, plugins: dict[str, dict]) -> EngineInstall:
    (root / "Engine" / "Build").mkdir(parents=True)
    (root / "Engine" / "Build" / "Build.version").write_text(json.dumps(BUILD_VERSION))
    for rel, desc in plugins.items():
        path = root / "Engine" / "Plugins" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(desc))
    return EngineInstall(root, root.name)


def runtime_plugin(enabled_by_default=False, deps=()):
    return {
        "EnabledByDefault": enabled_by_default,
        "Modules": [{"Name": "M", "Type": "Runtime"}],
        "Plugins": [{"Name": d, "Enabled": True} for d in deps],
    }


EDITOR_PLUGIN = {"EnabledByDefault": False, "Modules": [{"Name": "E", "Type": "Editor"}]}

WIN_PLUGINS = {
    "Runtime/StateTree/StateTree.uplugin": runtime_plugin(enabled_by_default=True),
    "Runtime/GameplayStateTree/GameplayStateTree.uplugin": runtime_plugin(False, deps=["StateTree"]),
    "Editor/ModelingToolsEditorMode/ModelingToolsEditorMode.uplugin": EDITOR_PLUGIN,
    "Runtime/Default/Default.uplugin": runtime_plugin(enabled_by_default=True),
}
LINUX_ONLY = {
    "Marketplace/BpGen/BpGeneratorUltimate.uplugin": {
        "Modules": [{"Name": "A", "Type": "Editor"}, {"Name": "B", "Type": "EditorNoCommandlet"}],
        "CanContainContent": True,
    },
    "Marketplace/RT/RuntimeFab.uplugin": runtime_plugin(False),
}


@pytest.fixture
def engines(tmp_path) -> EnginePair:
    win = make_engine(tmp_path / "UE_Win", WIN_PLUGINS)
    linux = make_engine(tmp_path / "UE_Linux", {**WIN_PLUGINS, **LINUX_ONLY})
    content = tmp_path / "UE_Linux" / "Engine" / "Plugins" / "Marketplace" / "BpGen" / "Content" / "AI"
    content.mkdir(parents=True)
    (content / "prompt.txt").write_text("not an asset")
    return EnginePair("5.8", EngineSettings(), linux, win)


def project(tmp_path, name, plugins, modules=None) -> ProjectDescriptor:
    d = tmp_path / name
    d.mkdir()
    raw = {"FileVersion": 3, "EngineAssociation": "5.8", "Plugins": plugins}
    if modules:
        raw["Modules"] = modules
    path = d / f"{name}.uproject"
    path.write_text(json.dumps(raw, indent="\t"))
    return ProjectDescriptor.load(path)


def statuses(analysis):
    return {r.name: r.status for r in analysis.references}


def test_blueprint_project_is_code_based_via_plugin(tmp_path, engines):
    desc = project(
        tmp_path,
        "PluginGame",
        [
            {"Name": "ModelingToolsEditorMode", "Enabled": True, "TargetAllowList": ["Editor"]},
            {"Name": "GameplayStateTree", "Enabled": True},
            {"Name": "BpGeneratorUltimate", "Enabled": True, "MarketplaceURL": "x"},
        ],
    )
    a = analyze_project(desc, engines)
    assert statuses(a) == {
        "ModelingToolsEditorMode": RefStatus.OK,
        "GameplayStateTree": RefStatus.OK,
        "BpGeneratorUltimate": RefStatus.PATCH_OPTIONAL,
    }
    assert a.optional_patches == ["BpGeneratorUltimate"]
    assert a.code_based and a.code_based_reason == "GameplayStateTree plugin is enabled"
    assert a.needs_build_step and not a.blockers


def test_blueprint_project_is_content_only(tmp_path, engines):
    desc = project(
        tmp_path,
        "ContentGame",
        [
            {"Name": "ModelingToolsEditorMode", "Enabled": True, "TargetAllowList": ["Editor"]},
            {"Name": "BpGeneratorUltimate", "Enabled": True},
        ],
    )
    a = analyze_project(desc, engines)
    assert not a.code_based and not a.needs_build_step
    assert a.optional_patches == ["BpGeneratorUltimate"]


def test_missing_plugin_without_patch_makes_ubt_fail(tmp_path, engines):
    """Without the Optional patch, UBT would report the missing plugin (sanity check of the port)."""
    from unrealbuilder.core.uproject import requires_temp_target

    desc = project(tmp_path, "P", [{"Name": "BpGeneratorUltimate", "Enabled": True}])
    all_plugins = engines.win.plugin_index()
    assert requires_temp_target(desc, "Development", all_plugins) == "BpGeneratorUltimate plugin is referenced by target but not found"


def test_runtime_plugin_missing_from_win_is_a_blocker(tmp_path, engines):
    desc = project(tmp_path, "P", [{"Name": "RuntimeFab", "Enabled": True}])
    a = analyze_project(desc, engines)
    assert statuses(a) == {"RuntimeFab": RefStatus.BLOCKER}
    assert a.blockers


def test_force_optional_overrides_blocker(tmp_path, engines):
    desc = project(tmp_path, "P", [{"Name": "RuntimeFab", "Enabled": True}])
    a = analyze_project(desc, engines, force_optional=["RuntimeFab"])
    assert statuses(a) == {"RuntimeFab": RefStatus.PATCH_OPTIONAL}
    assert not a.blockers


def test_disabling_a_default_runtime_plugin_makes_it_code_based(tmp_path, engines):
    desc = project(tmp_path, "P", [{"Name": "Default", "Enabled": False}])
    a = analyze_project(desc, engines)
    assert a.code_based_reason == "Default plugin is disabled"


def test_unknown_plugin_is_made_optional_with_warning(tmp_path, engines):
    desc = project(tmp_path, "P", [{"Name": "Ghost", "Enabled": True}])
    a = analyze_project(desc, engines)
    assert statuses(a) == {"Ghost": RefStatus.MISSING_UNKNOWN}
    assert a.warnings and not a.code_based


def test_project_with_source_is_cpp(tmp_path, engines):
    desc = project(tmp_path, "Cpp", [], modules=[{"Name": "Cpp", "Type": "Runtime"}])
    (desc.directory / "Source").mkdir()
    (desc.directory / "Source" / "Cpp.Target.cs").write_text("// target")
    a = analyze_project(desc, engines)
    assert a.has_code and a.kind_label == "C++"


def test_crypto_signing_forces_build(tmp_path, engines):
    desc = project(tmp_path, "Signed", [])
    (desc.directory / "Config").mkdir()
    (desc.directory / "Config" / "DefaultCrypto.ini").write_text(
        "[/Script/CryptoKeys.CryptoKeysSettings]\nbEnablePakSigning=True\n"
    )
    a = analyze_project(desc, engines)
    assert a.code_based_reason == "encryption/signing is enabled"
