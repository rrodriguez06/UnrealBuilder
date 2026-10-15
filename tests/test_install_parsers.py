from unrealbuilder.core.logparse.install import LegendaryProgress, VsDownloadProgress, feed_legendary, feed_vsdownload
from unrealbuilder.core.logparse.lines import LineSplitter


def lines_of(data: bytes):
    s = LineSplitter()
    return s.feed(data) + s.flush()


def test_legendary_install_log(log_fixture):
    p = LegendaryProgress()
    for line in lines_of(log_fixture("legendary_install.log")):
        feed_legendary(p, line)
    assert p.resumed_files == 54229
    assert p.install_size_mib and p.install_size_mib > 17000
    assert p.download_size_mib and 5000 < p.download_size_mib < 7000
    assert p.finished and p.percent == 100.0
    assert p.total > 0


def test_vsdownload_log(log_fixture):
    p = VsDownloadProgress()
    for line in lines_of(log_fixture("vsdownload.log")):
        feed_vsdownload(p, line)
    assert p.selected == 471
    assert p.downloaded > 100
