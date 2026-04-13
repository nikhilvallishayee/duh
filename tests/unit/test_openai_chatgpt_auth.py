from __future__ import annotations

import base64
import json

from duh.auth.openai_chatgpt import _extract_account_id


def _fake_jwt(payload: dict) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode().rstrip("=")
    return f"{header}.{body}.sig"


def test_extract_account_id_from_jwt_claim():
    token = _fake_jwt(
        {"https://api.openai.com/auth": {"chatgpt_account_id": "acct_123"}}
    )
    assert _extract_account_id(token) == "acct_123"

