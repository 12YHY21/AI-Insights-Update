from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

import httpx


class FeishuSender:
    def __init__(self, webhook_url: str, secret: str = "", timeout_seconds: int = 25):
        if not webhook_url.startswith("https://"):
            raise ValueError("飞书 Webhook 必须使用 HTTPS")
        self.webhook_url = webhook_url
        self.secret = secret
        self.timeout_seconds = timeout_seconds

    def _signature(self, timestamp: str) -> str:
        string_to_sign = f"{timestamp}\n{self.secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _signed_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.secret:
            return payload
        timestamp = str(int(time.time()))
        return {**payload, "timestamp": timestamp, "sign": self._signature(timestamp)}

    def _post(self, payload: dict[str, Any]) -> None:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = httpx.post(
                    self.webhook_url,
                    json=self._signed_payload(payload),
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                data = response.json()
                if "code" in data:
                    code = data["code"]
                elif "StatusCode" in data:
                    code = data["StatusCode"]
                else:
                    raise RuntimeError(f"飞书返回了未知响应：{data}")
                if code != 0:
                    raise RuntimeError(f"飞书返回错误：{data}")
                return
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(attempt)
        raise RuntimeError("飞书推送连续三次失败") from last_error

    def send_markdown_card(self, title: str, markdown: str, sequence: str = "") -> None:
        payload: dict[str, Any] = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "blue",
                    "title": {"tag": "plain_text", "content": f"{title}{sequence}"},
                },
                "elements": [{"tag": "markdown", "content": markdown}],
            },
        }
        self._post(payload)

    def send_digest(self, title: str, chunks: list[str]) -> None:
        total = len(chunks)
        for index, chunk in enumerate(chunks, 1):
            sequence = f"（{index}/{total}）" if total > 1 else ""
            self.send_markdown_card(title, chunk, sequence)

    def send_notice(self, title: str, content: str, template: str = "blue") -> None:
        payload: dict[str, Any] = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "template": template,
                    "title": {"tag": "plain_text", "content": title},
                },
                "elements": [{"tag": "markdown", "content": content}],
            },
        }
        self._post(payload)
