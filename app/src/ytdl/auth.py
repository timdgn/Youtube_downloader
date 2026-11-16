"""Bearer-token authentication and the cached SSM token (T021)."""

from __future__ import annotations

import hmac
from typing import Any

_PREFIX = "Bearer "
_UNAUTHORIZED_MESSAGE = "unauthorized"


class Unauthorized(Exception):
    """Raised for any missing, malformed, or wrong credential."""

    def __init__(self) -> None:
        super().__init__(_UNAUTHORIZED_MESSAGE)


def check_bearer(authorization_header: str | None, expected_token: str) -> None:
    """Accept exactly ``Bearer <token>``, with a constant-time token comparison."""
    if not authorization_header or not expected_token:
        raise Unauthorized()
    if not authorization_header.startswith(_PREFIX):
        raise Unauthorized()

    presented = authorization_header[len(_PREFIX) :]
    if (
        not presented
        or presented != presented.strip()
        or any(character.isspace() for character in presented)
    ):
        raise Unauthorized()

    if not hmac.compare_digest(presented.encode("utf-8"), expected_token.encode("utf-8")):
        raise Unauthorized()


class TokenProvider:
    """Reads the API token from SSM SecureString once per process."""

    def __init__(self, param_name: str, ssm_client: Any) -> None:
        self._param_name = param_name
        self._ssm_client = ssm_client
        self._value: str | None = None

    def get(self) -> str:
        if self._value is None:
            response = self._ssm_client.get_parameter(Name=self._param_name, WithDecryption=True)
            value = response["Parameter"]["Value"]
            if not value:
                raise ValueError(f"SSM parameter {self._param_name} is empty")
            self._value = value
        return self._value
