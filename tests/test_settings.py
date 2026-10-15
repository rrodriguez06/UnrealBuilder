from unrealbuilder.core.settings import ProjectOverride, Settings, load_settings, save_settings, settings_path


def test_defaults():
    s = Settings()
    assert s.resources.shader_workers == 8
    assert s.resources.job_memory_max_gib == 20
    assert s.resources.max_concurrent_builds == 1
    assert "5.8" in s.engines
    assert s.cook.zen_port == 8559


def test_roundtrip(tmp_path):
    s = Settings()
    s.resources.shader_workers = 6
    s.projects["TestUnreal"] = ProjectOverride(force_optional_plugins=["Foo"])
    path = save_settings(s)
    assert path == settings_path()
    loaded = load_settings()
    assert loaded.resources.shader_workers == 6
    assert loaded.projects["TestUnreal"].force_optional_plugins == ["Foo"]
    assert loaded.engines["5.8"].legendary_app == "UE_5.8"


def test_unknown_keys_are_ignored(tmp_path):
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text('schema_version = 1\nfuture_key = 3\n[resources]\nshader_workers = 4\nnew_thing = "x"\n')
    s = load_settings()
    assert s.resources.shader_workers == 4
    assert s.resources.job_memory_max_gib == 20
