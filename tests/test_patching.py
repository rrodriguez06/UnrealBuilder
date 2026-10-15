import json

from unrealbuilder.core.patching import (
    BLOCK_BEGIN,
    apply_ini_block,
    editor_ini_sections,
    engine_ini_sections,
    patch_uproject,
)

UPROJECT = '{\n\t"FileVersion": 3,\n\t"Plugins": [\n\t\t{\n\t\t\t"Name": "BpGeneratorUltimate",\n\t\t\t"Enabled": true\n\t\t},\n\t\t{\n\t\t\t"Name": "Other",\n\t\t\t"Enabled": true\n\t\t}\n\t]\n}'


def test_patch_uproject_sets_optional_and_is_idempotent(tmp_path):
    p = tmp_path / "P.uproject"
    p.write_text(UPROJECT)
    assert patch_uproject(p, ["bpgeneratorultimate"]) is True
    data = json.loads(p.read_text())
    assert data["Plugins"][0]["Optional"] is True
    assert "Optional" not in data["Plugins"][1]
    assert list(data["Plugins"][0]) == ["Name", "Enabled", "Optional"]
    assert "\t\t\t\"Optional\": true" in p.read_text()
    assert patch_uproject(p, ["BpGeneratorUltimate"]) is False


def test_patch_uproject_keeps_crlf_and_bom(tmp_path):
    p = tmp_path / "P.uproject"
    p.write_bytes(b"\xef\xbb\xbf" + UPROJECT.replace("\n", "\r\n").encode())
    assert patch_uproject(p, ["Other"])
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")


def test_ini_block_insert_replace_noop_remove(tmp_path):
    ini = tmp_path / "DefaultEngine.ini"
    ini.write_text("[/Script/Engine.RendererSettings]\nr.X=1\n")
    s1 = engine_ini_sections(24, 8, 8559, True)
    assert apply_ini_block(ini, s1)
    text = ini.read_text()
    assert text.startswith("[/Script/Engine.RendererSettings]\nr.X=1\n")
    assert "NumUnusedShaderCompilingThreads=16" in text
    assert "DesiredPort=8559" in text and "LogShaderCompilers=Verbose" in text
    assert apply_ini_block(ini, s1) is False
    s2 = engine_ini_sections(24, 6, None, False)
    assert apply_ini_block(ini, s2)
    text = ini.read_text()
    assert text.count(BLOCK_BEGIN) == 1
    assert "NumUnusedShaderCompilingThreads=18" in text and "DesiredPort" not in text
    assert apply_ini_block(ini, {})
    assert ini.read_text().rstrip() == "[/Script/Engine.RendererSettings]\nr.X=1"


def test_ini_block_creates_missing_file_with_crlf(tmp_path):
    ini = tmp_path / "Config" / "DefaultEditor.ini"
    assert apply_ini_block(ini, editor_ini_sections(20, 8))
    raw = ini.read_bytes()
    assert b"MemoryMaxUsedPhysical=12800\r\n" in raw
    assert b"MemoryMinFreePhysical=0\r\n" in raw


def test_ini_block_preserves_crlf(tmp_path):
    ini = tmp_path / "x.ini"
    ini.write_bytes(b"[A]\r\nk=v\r\n")
    apply_ini_block(ini, {"B": {"x": "1"}})
    raw = ini.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")
