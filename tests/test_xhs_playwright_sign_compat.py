from __future__ import annotations

from media_platform.xhs.playwright_sign import _build_payload_array_compat


class OldCrypto:
    def build_payload_array(
        self,
        hex_parameter,
        a1_value,
        app_identifier="xhs-pc-web",
        string_param="",
        timestamp=None,
        sign_state=None,
    ):
        return [hex_parameter, a1_value, app_identifier, string_param, timestamp]


class NewCrypto:
    def build_payload_array(
        self,
        hex_parameter,
        hex_md5_path,
        a1_value,
        app_identifier="xhs-pc-web",
        string_param="",
        timestamp=None,
        sign_state=None,
    ):
        return [hex_parameter, hex_md5_path, a1_value, app_identifier, string_param, timestamp]


def test_build_payload_array_compat_supports_old_xhshow_signature():
    result = _build_payload_array_compat(OldCrypto(), "md5", "a1", "app", "uri?k=v", 123.0)

    assert result == ["md5", "a1", "app", "uri?k=v", 123.0]


def test_build_payload_array_compat_supports_new_xhshow_signature():
    result = _build_payload_array_compat(NewCrypto(), "md5", "a1", "app", "uri?k=v", 123.0)

    assert result == ["md5", "md5", "a1", "app", "uri?k=v", 123.0]
