"""The bot's access boundary: an SSM allowlist read on every message (T047).

Three facts decide whether a delivery is served: the webhook secret, the sender's
numeric identifier, and the chat type. All three are configuration; none is code
branching, and the list governs the next message with no deployment (FR-020).
"""

from __future__ import annotations

import hmac
import re
from typing import Any

from ytdl.config import ConfigError

_NUMERIC_ID = re.compile(r"[0-9]{1,20}\Z")
_PRIVATE = "private"


def parse_allowlist(raw: str | None) -> frozenset[str]:
    """Turn ``"111, 222"`` into ``{"111", "222"}``; an empty value allows nobody."""
    if not raw or not raw.strip():
        return frozenset()

    entries: set[str] = set()
    for candidate in raw.split(","):
        value = candidate.strip()
        if not value:
            continue
        if not _NUMERIC_ID.match(value):
            raise ConfigError(
                "TELEGRAM_APPROVED_PARAM",
                f"approved user ids must be numeric, got {value!r}",
            )
        entries.add(value)
    return frozenset(entries)


class AllowlistPolicy:
    """One parameter read, then three pure questions about one delivery."""

    def __init__(
        self,
        ssm_client: Any,
        token_param: str | None,
        secret_param: str | None,
        approved_param: str | None,
    ) -> None:
        self._ssm = ssm_client
        self._names = {
            "token": token_param,
            "secret": secret_param,
            "approved": approved_param,
        }
        self.token = ""
        self.secret = ""
        self.approved: frozenset[str] = frozenset()

    def read(self) -> None:
        """Fetch the token, the secret and the allowlist in one call.

        Reading per message costs one call and removes every staleness question a
        cold-start cache would raise (research D-03).
        """
        names = [name for name in self._names.values() if name]
        if len(names) != 3:
            raise ConfigError("TELEGRAM_APPROVED_PARAM", "missing bot parameter name")

        response = self._ssm.get_parameters(Names=names, WithDecryption=True)
        values = {
            str(parameter.get("Name")): str(parameter.get("Value") or "")
            for parameter in response.get("Parameters") or []
            if isinstance(parameter, dict)
        }
        missing = [name for name in names if name not in values]
        if missing:
            raise ConfigError(missing[0], "missing bot parameter")

        self.token = values[names[0]]
        self.secret = values[names[1]]
        self.approved = parse_allowlist(values[names[2]])
        if not self.token:
            raise ConfigError(names[0], "empty bot token")

    def check_secret(self, presented: str | None) -> bool:
        """Compare the secret header in constant time; a missing one never matches."""
        if not presented or not self.secret:
            return False
        return hmac.compare_digest(presented.encode("utf-8"), self.secret.encode("utf-8"))

    def is_approved(self, user_id: int | str) -> bool:
        return str(user_id) in self.approved

    def is_private(self, chat_type: str | None) -> bool:
        return chat_type == _PRIVATE
