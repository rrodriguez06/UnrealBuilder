"""Progress parsers for legendary (engine download) and msvc-wine's vsdownload.py."""

from __future__ import annotations

import re
from dataclasses import dataclass

LEGENDARY_PROGRESS_RE = re.compile(
    r"= Progress: (?P<pct>[\d.]+)% \((?P<done>\d+)/(?P<total>\d+)\), Running for (?P<elapsed>[\d:]+), ETA: (?P<eta>[\d:]+)"
)
LEGENDARY_DOWNLOADED_RE = re.compile(r"- Downloaded: (?P<dl>[\d.]+) MiB, Written: (?P<wr>[\d.]+) MiB")
LEGENDARY_SPEED_RE = re.compile(r"\+ Download\s+- (?P<raw>[\d.]+) MiB/s \(raw\)")
LEGENDARY_SIZES_RE = re.compile(r"(?P<what>Install size|Download size): (?P<mib>[\d.]+) MiB")
LEGENDARY_SKIP_RE = re.compile(r"Skipping (?P<n>\d+) files based on resume data")

VS_SELECTED_RE = re.compile(r"Selected (?P<n>\d+) packages, for a total download size of (?P<dl>[\d.]+ \w+), install size of (?P<inst>[\d.]+ \w+)")


@dataclass
class LegendaryProgress:
    percent: float = 0.0
    done: int = 0
    total: int = 0
    elapsed: str = ""
    eta: str = ""
    downloaded_mib: float = 0.0
    written_mib: float = 0.0
    speed_mib_s: float = 0.0
    install_size_mib: float | None = None
    download_size_mib: float | None = None
    resumed_files: int = 0
    finished: bool = False


def feed_legendary(progress: LegendaryProgress, line: str) -> None:
    if m := LEGENDARY_PROGRESS_RE.search(line):
        progress.percent = float(m.group("pct"))
        progress.done, progress.total = int(m.group("done")), int(m.group("total"))
        progress.elapsed, progress.eta = m.group("elapsed"), m.group("eta")
    elif m := LEGENDARY_DOWNLOADED_RE.search(line):
        progress.downloaded_mib, progress.written_mib = float(m.group("dl")), float(m.group("wr"))
    elif m := LEGENDARY_SPEED_RE.search(line):
        progress.speed_mib_s = float(m.group("raw"))
    elif m := LEGENDARY_SIZES_RE.search(line):
        if m.group("what") == "Install size":
            progress.install_size_mib = float(m.group("mib"))
        else:
            progress.download_size_mib = float(m.group("mib"))
    elif m := LEGENDARY_SKIP_RE.search(line):
        progress.resumed_files = int(m.group("n"))
    elif "Finished installation process" in line:
        progress.finished = True
        progress.percent = 100.0


@dataclass
class VsDownloadProgress:
    selected: int = 0
    download_size: str = ""
    install_size: str = ""
    downloaded: int = 0
    unpacked: int = 0
    extracted: int = 0


def feed_vsdownload(progress: VsDownloadProgress, line: str) -> None:
    if m := VS_SELECTED_RE.search(line):
        progress.selected = int(m.group("n"))
        progress.download_size, progress.install_size = m.group("dl"), m.group("inst")
    elif line.startswith("Downloading "):
        progress.downloaded += 1
    elif line.startswith("Unpacking "):
        progress.unpacked += 1
    elif line.startswith("Extracting "):
        progress.extracted += 1
