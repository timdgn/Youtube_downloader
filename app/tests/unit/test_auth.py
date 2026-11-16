"""Unit tests for bearer authentication and the token cache (T015)."""

from __future__ import annotations

import hmac
from unittest.mock import MagicMock

import pytest

from ytdl.auth import TokenProvider, Unauthorized, check_bearer

TOKEN = "correct-horse-battery-staple"
PARAM_NAME = "/ytdl/dev/api-token"

BAD_HEADERS = (
    None,
    "",
    "   ",
    "Bearer",
    "Bearer ",
    f"Bearer  {TOKEN}",
    f"Bearer {TOKEN} ",
    f"Bearer\t{TOKEN}",
    f"Basic {TOKEN}",
    TOKEN,
    "Bearer wrong-token",
)


def test_correct_bearer_token_is_accepted() -> None:
    assert check_bearer(f"Bearer {TOKEN}", TOKEN) is None


def test_bad_headers_all_fail_indistinguishably() -> None:
    results = []
    for header in BAD_HEADERS:
        with pytest.raises(Unauthorized) as excinfo:
            check_bearer(header, TOKEN)
        results.append((type(excinfo.value), str(excinfo.value)))

    assert len(set(results)) == 1


def test_empty_expected_token_never_accepts_a_bearer_prefix() -> None:
    with pytest.raises(Unauthorized):
        check_bearer("Bearer ", "")
    with pytest.raises(Unauthorized):
        check_bearer("Bearer anything", "")


def test_comparison_goes_through_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    real = hmac.compare_digest
    calls: list[tuple[bytes, bytes]] = []

    def spy(presented: bytes, expected: bytes) -> bool:
        calls.append((presented, expected))
        return real(presented, expected)

    monkeypatch.setattr("ytdl.auth.hmac.compare_digest", spy)

    check_bearer(f"Bearer {TOKEN}", TOKEN)

    assert len(calls) == 1
    assert calls[0][1] == TOKEN.encode()


def test_wrong_token_is_compared_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    real = hmac.compare_digest
    calls: list[tuple[bytes, bytes]] = []

    def spy(presented: bytes, expected: bytes) -> bool:
        calls.append((presented, expected))
        return real(presented, expected)

    monkeypatch.setattr("ytdl.auth.hmac.compare_digest", spy)

    with pytest.raises(Unauthorized):
        check_bearer("Bearer wrong-token", TOKEN)

    assert len(calls) == 1


def _ssm(value: str = TOKEN) -> MagicMock:
    client = MagicMock()
    client.get_parameter.return_value = {
        "Parameter": {"Name": PARAM_NAME, "Type": "SecureString", "Value": value}
    }
    return client


def test_token_is_read_once_per_provider() -> None:
    client = _ssm()
    provider = TokenProvider(PARAM_NAME, client)

    assert provider.get() == TOKEN
    assert provider.get() == TOKEN
    assert provider.get() == TOKEN

    assert client.get_parameter.call_count == 1


def test_token_is_read_with_decryption() -> None:
    client = _ssm()

    TokenProvider(PARAM_NAME, client).get()

    client.get_parameter.assert_called_once_with(Name=PARAM_NAME, WithDecryption=True)


def test_failed_read_raises_and_is_not_cached() -> None:
    client = _ssm()
    client.get_parameter.side_effect = RuntimeError("throttled")
    provider = TokenProvider(PARAM_NAME, client)

    with pytest.raises(RuntimeError):
        provider.get()

    client.get_parameter.side_effect = None
    assert provider.get() == TOKEN
    assert client.get_parameter.call_count == 2


def test_empty_parameter_value_raises_instead_of_returning_empty() -> None:
    client = _ssm("")
    provider = TokenProvider(PARAM_NAME, client)

    with pytest.raises(ValueError):
        provider.get()
    with pytest.raises(ValueError):
        provider.get()

    assert client.get_parameter.call_count == 2
