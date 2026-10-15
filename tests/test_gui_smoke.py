from unrealbuilder.core.logparse.lines import make_line
from unrealbuilder.core.settings import Settings
from unrealbuilder.gui.main_window import PAGES, MainWindow
from unrealbuilder.gui.widgets.log_view import LogView


def test_main_window_builds(qtbot, tmp_path):
    s = Settings()
    s.paths.project_roots = [str(tmp_path / "projects")]
    s.paths.toolchain_root = str(tmp_path / "toolchain")
    window = MainWindow(s, start_services=False)
    qtbot.addWidget(window)
    assert window.sidebar.count() == len(PAGES)
    assert window.stack.count() == len(PAGES)


def test_log_view_filters(qtbot):
    view = LogView({"a": "A"})
    qtbot.addWidget(view)
    view.append(
        "a",
        [
            make_line(1, "LogInit: Display: hello"),
            make_line(2, "LogCook: Warning: careful"),
            make_line(3, "LogCook: Error: boom"),
            make_line(4, "MESA-EGL: warning: noise"),
            make_line(5, "LogShaderCompilers: Verbose: Worker (1/8): shaders left to compile 3"),
        ],
    )
    assert view.proxy.rowCount() == 3  # noise and verbose hidden by default
    view.issues_only.setChecked(True)
    assert view.proxy.rowCount() == 2
    view.issues_only.setChecked(False)
    view.noise.setChecked(True)
    assert view.proxy.rowCount() == 5
    view.search.setText("boom")
    view._apply_filters()
    assert view.proxy.rowCount() == 1
