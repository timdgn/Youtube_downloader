"""The help text is the operator's, so it is asserted against the contract (T055)."""

from __future__ import annotations

import ast
from pathlib import Path

from ytdl.bot import messages

CONTRACT = (
    Path(__file__).resolve().parents[3]
    / "specs"
    / "002-telegram-bot-interface"
    / "contracts"
    / "bot-commands.md"
)


def _contract_block() -> str:
    """Return the fenced help block of contracts/bot-commands.md, verbatim."""
    text = CONTRACT.read_text(encoding="utf-8")
    heading = text.index("## The help text, verbatim")
    start = text.index("```text", heading) + len("```text\n")
    end = text.index("```", start)
    block = text[start:end]
    return block[:-1] if block.endswith("\n") else block


def test_the_help_text_is_the_contract_block_character_for_character() -> None:
    assert _contract_block() == messages.HELP


def test_the_help_text_keeps_the_fixed_details() -> None:
    assert messages.HELP.startswith("📚 Available commands:")
    assert "/link number - Send a fresh download link" in messages.HELP
    assert "/delete filename.zip - Delete a specific video" in messages.HELP
    assert "• mp3 - audio only (MP3 format)" in messages.HELP
    assert messages.HELP.endswith('Example: "https://www.youtube.com/watch?v=example medium"')


def test_the_help_text_has_no_trailing_blank_line() -> None:
    assert messages.HELP.rstrip("\n") == messages.HELP


def test_the_help_text_is_a_single_literal_not_a_runtime_assembly() -> None:
    """A concatenation of pieces could drift from the contract; one literal cannot."""
    source = Path(messages.__file__).read_text(encoding="utf-8")
    module = ast.parse(source)

    for node in ast.walk(module):
        if not isinstance(node, ast.Assign):
            continue
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if "HELP" not in names:
            continue
        value = node.value
        assert isinstance(value, ast.Constant), "HELP must be one literal"
        assert value.value == messages.HELP


def test_start_points_at_help() -> None:
    assert messages.START == (
        "Hello. Send a YouTube link with a resolution word and I will fetch it.\n"
        "\n"
        "Send /help for the full command list."
    )
    assert "/help" in messages.START


def test_start_answers_with_the_greeting_and_the_fixed_block() -> None:
    assert messages.START_REPLY == messages.START + "\n\n" + messages.HELP
    assert messages.START_REPLY.endswith(messages.HELP)
    assert messages.START_REPLY.count(messages.HELP) == 1


def test_start_is_one_message_under_the_telegram_limit() -> None:
    assert len(messages.START_REPLY) <= 3500
    assert messages.paginate(messages.START_REPLY) == [messages.START_REPLY]


def test_every_command_in_the_help_text_is_a_real_command() -> None:
    for word in ("/start", "/list", "/delete", "/empty", "/history", "/info", "/test", "/help"):
        assert f"\n{word} " in messages.HELP

    assert messages.TEST_STARTED == "Pipeline test started."
