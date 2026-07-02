from __future__ import annotations

import pytest

import config
from cmd_arg import parse_cmd


@pytest.mark.asyncio
async def test_cmd_arg_accepts_cdp_runtime_options():
    original_enable_cdp = config.ENABLE_CDP_MODE
    original_connect_existing = config.CDP_CONNECT_EXISTING
    try:
        await parse_cmd(
            [
                "--platform",
                "xhs",
                "--enable_cdp_mode",
                "false",
                "--cdp_connect_existing",
                "false",
            ]
        )

        assert config.ENABLE_CDP_MODE is False
        assert config.CDP_CONNECT_EXISTING is False
    finally:
        config.ENABLE_CDP_MODE = original_enable_cdp
        config.CDP_CONNECT_EXISTING = original_connect_existing
