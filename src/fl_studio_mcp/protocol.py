"""Protocol 2 constants and JSON encoding for the file transport."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

PROTOCOL = 2
MAX_COMMAND_BYTES = 16 * 1024 * 1024
MAX_BATCH_ACTIONS = 32
COMMAND_NAME = "mcp_command.json"
RESPONSE_NAME = "mcp_response.json"
HEARTBEAT_NAME = "mcp_heartbeat.json"
LOCK_NAME = "mcp_command.lock"


def encode_command(request_id: int, payload: dict[str, Any]) -> bytes:
    envelope = {"protocol": PROTOCOL, "id": request_id, **payload}
    body = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_COMMAND_BYTES:
        raise ValueError(f"command exceeds {MAX_COMMAND_BYTES} bytes")
    return body


@dataclass
class RPCError(RuntimeError):
    action: str
    message: str

    def __str__(self) -> str:
        return f"{self.action}: {self.message}"
