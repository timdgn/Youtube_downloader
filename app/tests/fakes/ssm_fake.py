"""In-memory SSM parameter store for contract tests."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError


class FakeSsmClient:
    """Same call surface as the boto3 SSM client methods the service uses."""

    def __init__(self, parameters: dict[str, str] | None = None) -> None:
        self.parameters = dict(parameters or {})
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_parameter(self, *, Name: str, WithDecryption: bool = False) -> dict[str, Any]:
        self.calls.append(("get_parameter", {"Name": Name, "WithDecryption": WithDecryption}))
        if Name not in self.parameters:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ParameterNotFound",
                        "Message": f"parameter {Name} not found",
                    }
                },
                "GetParameter",
            )
        return {
            "Parameter": {
                "Name": Name,
                "Type": "SecureString",
                "Value": self.parameters[Name],
            }
        }
