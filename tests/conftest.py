"""Keep test data and subprocess probes inside the project workspace."""

from pathlib import Path

import pytest


def pytest_configure(config):
    root = Path(__file__).resolve().parents[1]
    target = (
        Path(config.option.basetemp)
        if config.option.basetemp
        else root / ".cache/pytest"
    )
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    if not target.is_relative_to(root / ".cache"):
        raise pytest.UsageError("测试临时目录必须位于项目.cache内")
    config.option.basetemp = target
