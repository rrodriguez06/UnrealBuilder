"""Plugin and module descriptors, mirroring the subset of UnrealBuildTool logic we need.

References:
- UnrealBuildTool/Configuration/Descriptors/ModuleDescriptor.cs  (IsCompiledInConfiguration)
- UnrealBuildTool/Configuration/Descriptors/PluginReferenceDescriptor.cs
- UnrealBuildTool/System/Plugins.cs  (IsEnabledByDefault)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class TargetType(StrEnum):
    GAME = "Game"
    EDITOR = "Editor"
    CLIENT = "Client"
    SERVER = "Server"
    PROGRAM = "Program"


class LoadedFrom(StrEnum):
    ENGINE = "Engine"
    PROJECT = "Project"


# Module types that can never be compiled into a cooked Game target.
EDITOR_ONLY_TYPES = {"Editor", "EditorNoCommandlet", "UncookedOnly", "EditorAndProgram", "Developer", "Program"}
ASSET_SUFFIXES = (".uasset", ".umap")


def load_json_lenient(path: Path) -> dict[str, Any]:
    """Load a .uplugin/.uproject; tolerate a BOM and trailing commas."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))


def _str_list(obj: dict[str, Any], key: str, legacy: str | None = None) -> list[str] | None:
    value = obj.get(key)
    if value is None and legacy:
        value = obj.get(legacy)
    if value is None:
        return None
    return [str(v) for v in value]


@dataclass
class ModuleDescriptor:
    name: str
    type: str
    platform_allow: list[str] | None = None
    platform_deny: list[str] | None = None
    target_allow: list[str] | None = None
    target_deny: list[str] | None = None
    config_allow: list[str] | None = None
    config_deny: list[str] | None = None

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> ModuleDescriptor:
        return cls(
            name=str(obj.get("Name", "")),
            type=str(obj.get("Type", "Runtime")),
            platform_allow=_str_list(obj, "PlatformAllowList", "WhitelistPlatforms"),
            platform_deny=_str_list(obj, "PlatformDenyList", "BlacklistPlatforms"),
            target_allow=_str_list(obj, "TargetAllowList", "WhitelistTargets"),
            target_deny=_str_list(obj, "TargetDenyList", "BlacklistTargets"),
            config_allow=_str_list(obj, "TargetConfigurationAllowList", "WhitelistTargetConfigurations"),
            config_deny=_str_list(obj, "TargetConfigurationDenyList", "BlacklistTargetConfigurations"),
        )

    def is_compiled_in(
        self,
        platform: str,
        configuration: str,
        target: TargetType,
        build_developer_tools: bool,
        requires_cooked_data: bool,
    ) -> bool:
        # Note: an existing-but-empty platform allow list disallows every platform (as in UBT).
        if self.platform_allow is not None and platform not in self.platform_allow:
            return False
        if self.platform_deny is not None and platform in self.platform_deny:
            return False
        if self.target_allow and target.value not in self.target_allow:
            return False
        if self.target_deny and target.value in self.target_deny:
            return False
        if self.config_allow and configuration not in self.config_allow:
            return False
        if self.config_deny and configuration in self.config_deny:
            return False
        t = self.type
        if t in ("Runtime", "RuntimeNoCommandlet"):
            return target != TargetType.PROGRAM
        if t == "RuntimeAndProgram":
            return True
        if t == "CookedOnly":
            return requires_cooked_data
        if t == "UncookedOnly":
            return not requires_cooked_data
        if t == "Developer":
            return target in (TargetType.EDITOR, TargetType.PROGRAM)
        if t == "DeveloperTool":
            return build_developer_tools
        if t in ("Editor", "EditorNoCommandlet"):
            return target == TargetType.EDITOR
        if t == "EditorAndProgram":
            return target in (TargetType.EDITOR, TargetType.PROGRAM)
        if t == "Program":
            return target == TargetType.PROGRAM
        if t == "ServerOnly":
            return target not in (TargetType.PROGRAM, TargetType.CLIENT)
        if t in ("ClientOnly", "ClientOnlyNoCommandlet"):
            return target not in (TargetType.PROGRAM, TargetType.SERVER)
        return False


@dataclass
class PluginReference:
    name: str
    enabled: bool = True
    optional: bool = False
    platform_allow: list[str] | None = None
    platform_deny: list[str] | None = None
    target_allow: list[str] | None = None
    target_deny: list[str] | None = None
    config_allow: list[str] | None = None
    config_deny: list[str] | None = None
    supported_platforms: list[str] | None = None
    has_explicit_platforms: bool = False

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> PluginReference:
        return cls(
            name=str(obj.get("Name", "")),
            enabled=bool(obj.get("Enabled", False)),
            optional=bool(obj.get("Optional", False)),
            platform_allow=_str_list(obj, "PlatformAllowList", "WhitelistPlatforms"),
            platform_deny=_str_list(obj, "PlatformDenyList", "BlacklistPlatforms"),
            target_allow=_str_list(obj, "TargetAllowList", "WhitelistTargets"),
            target_deny=_str_list(obj, "TargetDenyList", "BlacklistTargets"),
            config_allow=_str_list(obj, "TargetConfigurationAllowList", "WhitelistTargetConfigurations"),
            config_deny=_str_list(obj, "TargetConfigurationDenyList", "BlacklistTargetConfigurations"),
            supported_platforms=_str_list(obj, "SupportedTargetPlatforms"),
            has_explicit_platforms=bool(obj.get("HasExplicitPlatforms", False)),
        )

    def enabled_for(self, platform: str, configuration: str, target: TargetType) -> bool:
        if not self.enabled:
            return False
        if self.has_explicit_platforms:
            if not self.platform_allow or platform not in self.platform_allow:
                return False
        elif self.platform_allow and platform not in self.platform_allow:
            return False
        if self.platform_deny and platform in self.platform_deny:
            return False
        if self.config_allow and configuration not in self.config_allow:
            return False
        if self.config_deny and configuration in self.config_deny:
            return False
        if self.target_allow and target.value not in self.target_allow:
            return False
        if self.target_deny and target.value in self.target_deny:
            return False
        return True

    def supports_platform(self, platform: str) -> bool:
        if self.has_explicit_platforms:
            return bool(self.supported_platforms) and platform in self.supported_platforms
        return not self.supported_platforms or platform in self.supported_platforms


@dataclass
class PluginDescriptor:
    name: str
    path: Path
    loaded_from: LoadedFrom
    enabled_by_default: bool | None = None
    modules: list[ModuleDescriptor] = field(default_factory=list)
    plugins: list[PluginReference] = field(default_factory=list)
    supported_platforms: list[str] | None = None
    has_explicit_platforms: bool = False
    friendly_name: str = ""
    marketplace: bool = False

    @classmethod
    def from_file(cls, path: Path, loaded_from: LoadedFrom) -> PluginDescriptor:
        obj = load_json_lenient(path)
        ebd = obj.get("EnabledByDefault")
        return cls(
            name=path.stem,
            path=path,
            loaded_from=loaded_from,
            enabled_by_default=None if ebd is None else bool(ebd),
            modules=[ModuleDescriptor.from_json(m) for m in obj.get("Modules", []) or []],
            plugins=[PluginReference.from_json(p) for p in obj.get("Plugins", []) or []],
            supported_platforms=_str_list(obj, "SupportedTargetPlatforms"),
            has_explicit_platforms=bool(obj.get("HasExplicitPlatforms", False)),
            friendly_name=str(obj.get("FriendlyName", "")),
            marketplace="Marketplace" in path.parts,
        )

    def is_enabled_by_default(self, allow_engine_plugins_enabled_by_default: bool = True) -> bool:
        if self.enabled_by_default is not None:
            if self.enabled_by_default:
                return self.loaded_from == LoadedFrom.PROJECT or allow_engine_plugins_enabled_by_default
            return False
        return self.loaded_from == LoadedFrom.PROJECT

    def supports_platform(self, platform: str) -> bool:
        if self.has_explicit_platforms:
            return bool(self.supported_platforms) and platform in self.supported_platforms
        return not self.supported_platforms or platform in self.supported_platforms

    @property
    def module_types(self) -> set[str]:
        return {m.type for m in self.modules}

    @property
    def has_only_editor_modules(self) -> bool:
        """True when the plugin has code and none of it can end up in a cooked game."""
        return bool(self.modules) and all(m.type in EDITOR_ONLY_TYPES for m in self.modules)

    def has_assets(self) -> bool:
        """True when the plugin ships .uasset/.umap content (which a cook could need)."""
        content = self.path.parent / "Content"
        if not content.is_dir():
            return False
        for _dirpath, _dirnames, filenames in os.walk(content):
            if any(f.endswith(ASSET_SUFFIXES) for f in filenames):
                return True
        return False

    def safe_to_make_optional(self) -> bool:
        """Missing from the packaging engine but harmless to drop for a Game build."""
        return self.has_only_editor_modules and not self.has_assets()

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "loaded_from": self.loaded_from.value,
            "enabled_by_default": self.enabled_by_default,
            "modules": [m.__dict__ for m in self.modules],
            "plugins": [p.__dict__ for p in self.plugins],
            "supported_platforms": self.supported_platforms,
            "has_explicit_platforms": self.has_explicit_platforms,
            "friendly_name": self.friendly_name,
            "marketplace": self.marketplace,
        }

    @classmethod
    def from_cache(cls, obj: dict[str, Any]) -> PluginDescriptor:
        return cls(
            name=obj["name"],
            path=Path(obj["path"]),
            loaded_from=LoadedFrom(obj["loaded_from"]),
            enabled_by_default=obj["enabled_by_default"],
            modules=[ModuleDescriptor(**m) for m in obj["modules"]],
            plugins=[PluginReference(**p) for p in obj["plugins"]],
            supported_platforms=obj["supported_platforms"],
            has_explicit_platforms=obj["has_explicit_platforms"],
            friendly_name=obj.get("friendly_name", ""),
            marketplace=obj.get("marketplace", False),
        )


# Directories that never contain .uplugin files worth scanning.
_PRUNE = {"Binaries", "Intermediate", "Content", "Source", "Resources", "Config", "Shaders", "Saved", "Docs", "ThirdParty", "Extras"}


def find_uplugins(root: Path) -> list[Path]:
    """Recursively find .uplugin files; like UE, a plugin directory is not searched further."""
    found: list[Path] = []
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        ups = [f for f in filenames if f.endswith(".uplugin")]
        if ups:
            found.extend(Path(dirpath) / f for f in ups)
            dirnames.clear()
            continue
        dirnames[:] = [d for d in dirnames if d not in _PRUNE and not d.startswith(".")]
    return sorted(found)


def scan_plugins(root: Path, loaded_from: LoadedFrom) -> dict[str, PluginDescriptor]:
    result: dict[str, PluginDescriptor] = {}
    for path in find_uplugins(root):
        try:
            desc = PluginDescriptor.from_file(path, loaded_from)
        except (OSError, ValueError):
            continue
        result.setdefault(desc.name.lower(), desc)
    return result
