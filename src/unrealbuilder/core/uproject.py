"""Project descriptor (.uproject) analysis for a Win64 Game build.

Two questions are answered here, against the *Windows* packaging engine:
1. Which plugin references would break the build, and which can safely be patched to `"Optional": true`
   (editor-only Fab plugins that only exist in the Linux engine, e.g. BpGeneratorUltimate)?
2. Will UAT treat the project as code-based (and therefore compile a Win64 target)?
   This mirrors UnrealBuildTool/System/NativeProjects.cs (RequiresTempTarget / GetCodePluginsForTarget).
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from unrealbuilder.core.engines import EnginePair
from unrealbuilder.core.plugins import (
    LoadedFrom,
    ModuleDescriptor,
    PluginDescriptor,
    PluginReference,
    TargetType,
    load_json_lenient,
    scan_plugins,
)

PLATFORM = "Win64"


@dataclass
class ProjectDescriptor:
    path: Path
    raw: dict[str, Any]
    engine_association: str
    modules: list[ModuleDescriptor]
    plugins: list[PluginReference]
    disable_engine_plugins_by_default: bool

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def directory(self) -> Path:
        return self.path.parent

    @classmethod
    def load(cls, path: Path) -> ProjectDescriptor:
        raw = load_json_lenient(path)
        return cls.from_raw(path, raw)

    @classmethod
    def from_raw(cls, path: Path, raw: dict[str, Any]) -> ProjectDescriptor:
        return cls(
            path=path,
            raw=raw,
            engine_association=str(raw.get("EngineAssociation", "")),
            modules=[ModuleDescriptor.from_json(m) for m in raw.get("Modules", []) or []],
            plugins=[PluginReference.from_json(p) for p in raw.get("Plugins", []) or []],
            disable_engine_plugins_by_default=bool(raw.get("DisableEnginePluginsByDefault", False)),
        )

    def has_code(self) -> bool:
        """Like UBT's ProjectHasCode: a Source/ folder containing *.Target.cs files."""
        source = self.directory / "Source"
        return source.is_dir() and any(source.glob("*.Target.cs"))


class RefStatus(StrEnum):
    OK = "ok"  # found in the Win64 engine or the project
    DISABLED = "disabled"  # "Enabled": false
    ALREADY_OPTIONAL = "already-optional"  # missing but already Optional
    PATCH_OPTIONAL = "patch-optional"  # missing, editor-only: patched to Optional in the mirror
    MISSING_UNKNOWN = "missing-unknown"  # missing everywhere: patched to Optional, with a warning
    BLOCKER = "blocker"  # missing from the Win64 engine and needed at runtime


@dataclass
class RefAnalysis:
    name: str
    status: RefStatus
    origin: str  # "engine-win64", "project", "engine-linux", "none"
    message: str
    for_game: bool  # reference active for Win64 Game targets
    marketplace: bool = False


@dataclass
class ProjectAnalysis:
    descriptor: ProjectDescriptor
    engine_version: str | None
    references: list[RefAnalysis] = field(default_factory=list)
    has_code: bool = False
    code_based: bool = False
    code_based_reason: str | None = None
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def optional_patches(self) -> list[str]:
        return [r.name for r in self.references if r.status in (RefStatus.PATCH_OPTIONAL, RefStatus.MISSING_UNKNOWN)]

    @property
    def needs_build_step(self) -> bool:
        return self.has_code or self.code_based

    @property
    def kind_label(self) -> str:
        if self.has_code:
            return "C++"
        if self.code_based:
            return f"Blueprint, compiled ({self.code_based_reason})"
        return "Blueprint (content only)"


def _project_plugins(project_dir: Path) -> dict[str, PluginDescriptor]:
    return scan_plugins(project_dir / "Plugins", LoadedFrom.PROJECT)


def crypto_requires_build(project_dir: Path) -> str | None:
    """Encryption or pak signing forces a code-based build (NativeProjects.RequiresTempTarget)."""
    ini = project_dir / "Config" / "DefaultCrypto.ini"
    if not ini.is_file():
        return None
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(ini.read_text(encoding="utf-8-sig", errors="replace"))
    except configparser.Error:
        return None
    for section in parser.sections():
        for key in ("bEnablePakSigning", "bEncryptPakIniFiles", "bEncryptPakIndex", "bEncryptUAssetFiles", "bEncryptAllAssetFiles"):
            if parser.get(section, key, fallback="False").strip().lower() == "true":
                return "encryption/signing is enabled"
    return None


def _configure(
    first: PluginReference,
    configuration: str,
    target: TargetType,
    all_plugins: dict[str, PluginDescriptor],
    enabled: dict[str, PluginDescriptor],
) -> PluginReference | None:
    """Port of NativeProjects.ConfigureEnabledPluginForTarget. Returns the missing reference, if any."""
    if first.name.lower() in enabled:
        return None
    seen = {first.name.lower()}
    queue = [first]
    idx = 0
    while idx < len(queue):
        ref = queue[idx]
        idx += 1
        if not ref.enabled_for(PLATFORM, configuration, target):
            continue
        if not ref.supports_platform(PLATFORM):
            continue
        desc = all_plugins.get(ref.name.lower())
        if desc is None:
            if ref.optional:
                continue
            return ref
        if not desc.supports_platform(PLATFORM):
            continue
        for dep in desc.plugins:
            key = dep.name.lower()
            if key not in enabled and key not in seen:
                seen.add(key)
                queue.append(dep)
        enabled[desc.name.lower()] = desc
    return None


def code_plugins_for_target(
    descriptor: ProjectDescriptor | None,
    configuration: str,
    target: TargetType,
    all_plugins: dict[str, PluginDescriptor],
) -> tuple[set[str], PluginReference | None]:
    """Port of NativeProjects.GetCodePluginsForTarget: names of enabled plugins with compiled-in modules."""
    enabled: dict[str, PluginDescriptor] = {}
    configured: set[str] = set()
    allow_engine_default = True
    if descriptor is not None:
        allow_engine_default = not descriptor.disable_engine_plugins_by_default
        for ref in descriptor.plugins:
            key = ref.name.lower()
            if key in configured:
                continue
            missing = _configure(ref, configuration, target, all_plugins, enabled)
            if missing is not None:
                return set(), missing
            configured.add(key)
    for key, desc in all_plugins.items():
        if key in configured or not desc.is_enabled_by_default(allow_engine_default):
            continue
        missing = _configure(PluginReference(name=desc.name, enabled=True), configuration, target, all_plugins, enabled)
        if missing is not None:
            return set(), missing
        configured.add(key)
    build_dev_tools = target in (TargetType.EDITOR, TargetType.PROGRAM) or configuration not in ("Test", "Shipping")
    requires_cooked = target != TargetType.EDITOR
    code = {
        key
        for key, desc in enabled.items()
        if any(m.is_compiled_in(PLATFORM, configuration, target, build_dev_tools, requires_cooked) for m in desc.modules)
    }
    return code, None


def requires_temp_target(
    descriptor: ProjectDescriptor,
    configuration: str,
    all_plugins: dict[str, PluginDescriptor],
) -> str | None:
    """Port of NativeProjects.RequiresTempTarget for Win64 Game. Returns the reason, or None."""
    reason = crypto_requires_build(descriptor.directory)
    if reason:
        return reason
    target = TargetType.GAME
    project_code, missing = code_plugins_for_target(descriptor, configuration, target, all_plugins)
    if missing is not None:
        return f"{missing.name} plugin is referenced by target but not found"
    default_code, missing = code_plugins_for_target(None, configuration, target, all_plugins)
    if missing is not None:
        return f"{missing.name} plugin is referenced by the default target but not found"
    names = {k: all_plugins[k].name for k in all_plugins}
    for key in sorted(project_code - default_code):
        return f"{names.get(key, key)} plugin is enabled"
    for key in sorted(default_code - project_code):
        return f"{names.get(key, key)} plugin is disabled"
    return None


def apply_optional(descriptor: ProjectDescriptor, names: list[str]) -> ProjectDescriptor:
    """Return a copy of the descriptor where the given plugin references are Optional."""
    wanted = {n.lower() for n in names}
    raw = dict(descriptor.raw)
    plugins = []
    for item in raw.get("Plugins", []) or []:
        item = dict(item)
        if str(item.get("Name", "")).lower() in wanted:
            item["Optional"] = True
        plugins.append(item)
    if "Plugins" in raw:
        raw["Plugins"] = plugins
    return ProjectDescriptor.from_raw(descriptor.path, raw)


def analyze_project(
    descriptor: ProjectDescriptor,
    engine: EnginePair | None,
    configuration: str = "Development",
    force_optional: list[str] | None = None,
) -> ProjectAnalysis:
    analysis = ProjectAnalysis(descriptor=descriptor, engine_version=engine.version if engine else None)
    analysis.has_code = descriptor.has_code()
    if engine is None:
        analysis.blockers.append(f"No engine configured for EngineAssociation={descriptor.engine_association!r}")
        return analysis
    if not engine.win.exists:
        analysis.blockers.append(f"Windows engine not found: {engine.win.root}")
        return analysis

    win_index = engine.win.plugin_index()
    linux_index = engine.linux.plugin_index() if engine.linux.exists else {}
    project_index = _project_plugins(descriptor.directory)
    all_plugins = dict(win_index)
    all_plugins.update(project_index)
    forced = {n.lower() for n in (force_optional or [])}

    for ref in descriptor.plugins:
        key = ref.name.lower()
        for_game = ref.enabled_for(PLATFORM, configuration, TargetType.GAME)
        if not ref.enabled:
            analysis.references.append(RefAnalysis(ref.name, RefStatus.DISABLED, "-", "disabled", for_game))
            continue
        if key in project_index:
            analysis.references.append(RefAnalysis(ref.name, RefStatus.OK, "project", "project plugin", for_game))
            continue
        if key in win_index and key not in forced:
            analysis.references.append(
                RefAnalysis(ref.name, RefStatus.OK, "engine-win64", "present in the Windows engine", for_game, win_index[key].marketplace)
            )
            continue
        if ref.optional:
            analysis.references.append(
                RefAnalysis(ref.name, RefStatus.ALREADY_OPTIONAL, "none", "missing from the Windows engine, already optional", for_game)
            )
            continue
        linux_desc = linux_index.get(key)
        if key in forced:
            analysis.references.append(
                RefAnalysis(ref.name, RefStatus.PATCH_OPTIONAL, "forced", "made optional (forced in settings)", for_game)
            )
        elif linux_desc is None:
            analysis.references.append(
                RefAnalysis(ref.name, RefStatus.MISSING_UNKNOWN, "none", "not found in either engine: made optional", for_game)
            )
            analysis.warnings.append(f"Plugin {ref.name} not found: made optional in the mirror")
        elif linux_desc.safe_to_make_optional() or (not for_game and not linux_desc.has_assets()):
            analysis.references.append(
                RefAnalysis(
                    ref.name,
                    RefStatus.PATCH_OPTIONAL,
                    "engine-linux",
                    "editor plugin missing from the Windows engine: made optional",
                    for_game,
                    linux_desc.marketplace,
                )
            )
        else:
            analysis.references.append(
                RefAnalysis(
                    ref.name,
                    RefStatus.BLOCKER,
                    "engine-linux",
                    "runtime plugin missing from the Windows engine",
                    for_game,
                    linux_desc.marketplace,
                )
            )
            analysis.blockers.append(
                f"Plugin {ref.name} (modules: {', '.join(sorted(linux_desc.module_types)) or 'none'}) "
                "only exists in the Linux engine: install it in the Windows engine or force it optional"
            )

    patched = apply_optional(descriptor, analysis.optional_patches)
    if not analysis.has_code:
        reason = requires_temp_target(patched, configuration, all_plugins)
        analysis.code_based = reason is not None
        analysis.code_based_reason = reason
    return analysis
