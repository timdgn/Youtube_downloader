"""Unit tests for the allowlist, the secret comparison and the chat rule (T044)."""

from __future__ import annotations

import pytest

from tests.fakes.ssm_fake import FakeSsmClient
from ytdl.bot import access
from ytdl.bot.access import AllowlistPolicy, parse_allowlist
from ytdl.config import ConfigError

TOKEN_PARAM = "/ytdl/test/telegram-bot-token"
SECRET_PARAM = "/ytdl/test/telegram-webhook-secret"
APPROVED_PARAM = "/ytdl/test/telegram-approved-user-ids"
TOKEN = "123456789:AA-token-value"
SECRET = "0123456789abcdef"


def _policy(
    approved: str = "111, 222", secret: str = SECRET
) -> tuple[AllowlistPolicy, FakeSsmClient]:
    ssm = FakeSsmClient({TOKEN_PARAM: TOKEN, SECRET_PARAM: secret, APPROVED_PARAM: approved})
    return AllowlistPolicy(ssm, TOKEN_PARAM, SECRET_PARAM, APPROVED_PARAM), ssm


# --- the parser --------------------------------------------------------------


def test_the_parser_splits_on_commas_and_trims() -> None:
    assert parse_allowlist("111, 222") == frozenset({"111", "222"})
    assert parse_allowlist("  111 ,222  ") == frozenset({"111", "222"})
    assert parse_allowlist("111,222,111") == frozenset({"111", "222"})


def test_an_empty_value_allows_nobody() -> None:
    for raw in ("", "   ", None):
        assert parse_allowlist(raw) == frozenset()


def test_a_non_numeric_entry_names_the_parameter() -> None:
    with pytest.raises(ConfigError) as excinfo:
        parse_allowlist("111, @operator")

    assert excinfo.value.variable == "TELEGRAM_APPROVED_PARAM"


@pytest.mark.parametrize("value", ["12345678901234567890", "12", "0", "987654321"])
def test_a_numeric_entry_of_up_to_twenty_digits_is_accepted(value: str) -> None:
    assert parse_allowlist(value) == frozenset({value})


def test_twenty_one_digits_is_refused() -> None:
    with pytest.raises(ConfigError):
        parse_allowlist("123456789012345678901")


# --- one read per message ----------------------------------------------------


def test_one_get_parameters_call_loads_all_three_values() -> None:
    policy, ssm = _policy()

    policy.read()

    assert [call[0] for call in ssm.calls] == ["get_parameters"]
    _name, arguments = ssm.calls[0]
    assert arguments["Names"] == [TOKEN_PARAM, SECRET_PARAM, APPROVED_PARAM]
    assert arguments["WithDecryption"] is True
    assert policy.token == TOKEN
    assert policy.secret == SECRET
    assert policy.approved == frozenset({"111", "222"})


def test_a_missing_parameter_names_it() -> None:
    ssm = FakeSsmClient({TOKEN_PARAM: TOKEN, SECRET_PARAM: SECRET})
    policy = AllowlistPolicy(ssm, TOKEN_PARAM, SECRET_PARAM, APPROVED_PARAM)

    with pytest.raises(ConfigError) as excinfo:
        policy.read()

    assert excinfo.value.variable == APPROVED_PARAM


def test_an_empty_token_is_refused() -> None:
    ssm = FakeSsmClient({TOKEN_PARAM: "", SECRET_PARAM: SECRET, APPROVED_PARAM: "111"})
    policy = AllowlistPolicy(ssm, TOKEN_PARAM, SECRET_PARAM, APPROVED_PARAM)

    with pytest.raises(ConfigError) as excinfo:
        policy.read()

    assert excinfo.value.variable == TOKEN_PARAM


# --- the three questions -----------------------------------------------------


def test_is_approved_agrees_on_the_string_form_of_an_id() -> None:
    policy, _ssm = _policy()
    policy.read()

    assert policy.is_approved(111) is True
    assert policy.is_approved("111") is True
    assert policy.is_approved(222) is True
    assert policy.is_approved(333) is False


def test_the_secret_comparison_is_constant_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, _ssm = _policy()
    policy.read()
    seen: list[tuple[str, str]] = []
    real = access.hmac.compare_digest

    def recording(left: bytes, right: bytes) -> bool:
        seen.append((left.decode(), right.decode()))
        return real(left, right)

    monkeypatch.setattr(access.hmac, "compare_digest", recording)

    assert policy.check_secret(SECRET) is True
    assert seen == [(SECRET, SECRET)]


@pytest.mark.parametrize("presented", [None, "", "short", SECRET + "x", SECRET[:-1]])
def test_a_wrong_length_or_absent_secret_is_refused(presented: str | None) -> None:
    policy, _ssm = _policy()
    policy.read()

    assert policy.check_secret(presented) is False


def test_only_a_private_chat_is_served() -> None:
    policy, _ssm = _policy()
    policy.read()

    assert policy.is_private("private") is True
    for chat_type in ("group", "supergroup", "channel", None, ""):
        assert policy.is_private(chat_type) is False


def test_a_change_to_the_allowlist_governs_the_next_read() -> None:
    policy, ssm = _policy()
    policy.read()
    assert policy.is_approved(333) is False

    ssm.parameters[APPROVED_PARAM] = "333"
    policy.read()

    assert policy.is_approved(333) is True
    assert policy.is_approved(111) is False
