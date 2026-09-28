"""Capture a stopped Radar v1 baseline before the v2 migration.

Run from the project root after stopping the server and MT5 bridge. The backup
contains local observation data and must stay on this machine.
"""

import hashlib
import json
import sqlite3
import time
import zipfile
from pathlib import Path

from app.datafeed import ROOT
from app.fvg_checkpoint import FVGCheckpoint
from app.fvg_history_archive import export as export_fvg


SOURCE_DIRS = ("app", "web", "scripts", "tests", "docs")
SOURCE_SUFFIXES = {".py", ".js", ".css", ".html", ".ps1", ".bat", ".md", ".json", ".txt"}
ROOT_FILES = ("README.md", "start.bat", "config.json", "radar_config.json",
              "mt5_clock.json", "requirements-mt5.txt", ".gitignore")
STATE_FILES = (
    "runtime/fvg-checkpoint.json",
    "runtime/fvg-chart.sqlite3",
    "runtime/fvg-curve.sqlite3",
    "runtime/alerts.sqlite3",
    "runtime/quotes.sqlite3",
    "runtime/ticks.sqlite3",
    "results/live.json",
    "results/snr_sbr_live.json",
    "results/snr_rbs_live.json",
    "results/nested_forward_live.json",
    "results/forward_events.jsonl",
    "results/snr_sbr_forward_events.jsonl",
    "results/snr_rbs_forward_events.jsonl",
    "results/nested_forward_events.jsonl",
    "results/nested_forward_events.meta.json",
    "results/nested_forward_outcomes.jsonl",
    "results/nested_forward_tick_checks.jsonl",
)


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def source_files():
    paths = [ROOT / name for name in ROOT_FILES]
    for folder in SOURCE_DIRS:
        paths.extend(path for path in (ROOT / folder).rglob("*")
                     if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES
                     and "evidence" not in path.parts)
    return sorted(set(path for path in paths if path.exists()))


def sqlite_backup(source, target):
    with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as origin:
        with sqlite3.connect(target) as copy:
            origin.backup(copy)
            assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            schema = [{"name": name, "type": kind, "sql": sql}
                      for kind, name, sql in copy.execute(
                          "SELECT type,name,sql FROM sqlite_master "
                          "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
    return schema


def capture():
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    base = ROOT / "runtime" / "backups" / ("v2-baseline-" + stamp)
    base.mkdir(parents=True, exist_ok=False)
    manifest = {"captured_utc": stamp, "archive": "source.zip", "source": {},
                "state": {}, "sqlite_schema": {}, "excluded": [
                    "runtime/telegram-settings.json (DPAPI secret; reconfigure on restore)",
                    "materials/ and data/ historical archives (left at original locations)",
                    "test fixtures and temporary runtime files"]}

    with zipfile.ZipFile(base / "source.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in source_files():
            relative = path.relative_to(ROOT).as_posix()
            archive.write(path, relative)
            manifest["source"][relative] = digest(path)

    for relative in STATE_FILES:
        source = ROOT / relative
        if not source.exists():
            continue
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix == ".sqlite3":
            manifest["sqlite_schema"][relative] = sqlite_backup(source, target)
        else:
            target.write_bytes(source.read_bytes())
        manifest["state"][relative] = {"bytes": target.stat().st_size,
                                        "sha256": digest(target)}

    checkpoint = base / "runtime" / "fvg-checkpoint.json"
    with zipfile.ZipFile(base / "source.zip") as archive:
        cfg = json.loads(archive.read("config.json"))
    envelope = json.loads(checkpoint.read_text(encoding="utf-8"))
    verifier = FVGCheckpoint(checkpoint, "offline baseline check", cfg)
    # The source identity contains the MT5 account, which is deliberately not
    # stored in this backup. Validate the saved envelope and engine state here;
    # a live restart additionally checks its own source identity.
    verifier.identity = envelope["payload"]["identity"]
    restored = verifier.load(cfg)
    if not restored:
        raise RuntimeError("FVG checkpoint did not restore")
    fvg_archive = base / "fvg-history.sqlite3"
    manifest["fvg_archive"] = export_fvg(checkpoint, fvg_archive)
    manifest["fvg_archive"]["archive_sha256"] = digest(fvg_archive)
    manifest["source_zip_sha256"] = digest(base / "source.zip")
    (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    return base, len(manifest["source"]), len(manifest["state"])


if __name__ == "__main__":
    path, sources, state = capture()
    print(json.dumps({"path": str(path), "source_files": sources, "state_files": state,
                      "verified": True}, ensure_ascii=False))
