# -*- coding: utf-8 -*-
import base64
import hashlib
import hmac


class Credential:
    def __init__(self, secret_id, secret_key, token=""):
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.token = token


def hmac_sha1_base64(secret_key: str, message: str) -> str:
    digest = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")
