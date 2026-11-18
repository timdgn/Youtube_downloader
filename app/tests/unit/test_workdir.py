"""The host worker refuses a scratch directory on the boot device."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

from ytdl.server import worker_loop
from ytdl.server.worker_loop import WorkdirError, check_workdir

BOOT_DEVICE = 100
USB_DEVICE = 200


def _usr_stat(target: str) -> Any:
    """Every path except / looks like a separate device."""
    return SimpleNamespace(st_dev=BOOT_DEVICE if target == "/" else USB_DEVICE)


def test_an_unset_variable_is_refused() -> None:
    with pytest.raises(WorkdirError, match="YTDL_WORK_DIR is not set"):
        check_workdir("")


def test_a_missing_directory_is_refused(tmp_path: Any) -> None:
    with pytest.raises(WorkdirError, match="cannot be inspected"):
        check_workdir(str(tmp_path / "absent"))


def test_a_file_is_refused(tmp_path: Any) -> None:
    target = tmp_path / "a-file"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(WorkdirError, match="is not a directory"):
        check_workdir(str(target), stat=_usr_stat)


def test_the_boot_device_is_refused(tmp_path: Any) -> None:
    with pytest.raises(WorkdirError, match="same filesystem as /"):
        check_workdir(str(tmp_path), stat=lambda _path: SimpleNamespace(st_dev=BOOT_DEVICE))


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_read_only_directory_is_refused(tmp_path: Any) -> None:
    target = tmp_path / "scratch"
    target.mkdir()
    os.chmod(target, 0o500)

    try:
        with pytest.raises(WorkdirError, match="not writable"):
            check_workdir(str(target), stat=_usr_stat)
    finally:
        os.chmod(target, 0o700)


def test_a_writable_directory_on_another_device_is_accepted(tmp_path: Any) -> None:
    target = tmp_path / "scratch"
    target.mkdir()

    check_workdir(str(target), stat=_usr_stat)

    assert list(target.iterdir()) == []


def test_main_exits_with_the_reason_in_an_allowed_field(monkeypatch: Any) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    class _Log:
        def error(self, event: str, **fields: object) -> None:
            calls.append((event, fields))

    monkeypatch.setattr("ytdl.server.worker_loop.get_logger", lambda _name: _Log())
    monkeypatch.setenv("YTDL_WORK_DIR", "")

    with pytest.raises(SystemExit) as exit_info:
        worker_loop.main()

    assert exit_info.value.code == 1
    assert calls[0][0] == "workdir_invalid"
    assert "message" in calls[0][1]
