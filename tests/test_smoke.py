from unrealbuilder import __version__
from unrealbuilder.app import main


def test_version(capsys):
    assert main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_every_cli_command_is_routed_to_the_cli(monkeypatch):
    """Regression: `unrealbuilder follow` used to open the GUI because it was missing from a hardcoded list."""
    import unrealbuilder.cli as cli

    seen = []
    monkeypatch.setattr(cli, "run_cli", lambda args: seen.append(args[0]) or 0)
    for command in cli.cli_commands():
        assert main([command]) == 0
    assert sorted(seen) == sorted(cli.cli_commands())
    assert {"follow", "cancel", "run-job", "status"} <= set(seen)


def test_unknown_command_does_not_open_the_gui(capsys):
    assert main(["definitely-not-a-command"]) == 2
