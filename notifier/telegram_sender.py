"""Send messages and photos to Telegram via Bot API."""

import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_SEC = 2


class TelegramSender:
    def __init__(self, token: str, chat_id: str):
        self._token = token
        self._chat_id = chat_id
        self._base = f"https://api.telegram.org/bot{token}"
        self._ok = bool(token and chat_id)
        self.sent_count = 0

    @property
    def configured(self) -> bool:
        return self._ok

    def test_connection(self) -> tuple[bool, str]:
        if not self._ok:
            return False, "Токен или chat_id не заданы"
        try:
            r = requests.get(f"{self._base}/getMe", timeout=10)
            data = r.json()
            if data.get("ok"):
                name = data["result"].get("first_name", "")
                return True, f"Бот: {name}"
            return False, data.get("description", "Неизвестная ошибка")
        except Exception as exc:
            return False, str(exc)

    def send_photo(self, photo_path: Path, caption: str) -> bool:
        if not self._ok:
            return False
        for attempt in range(MAX_RETRIES):
            try:
                with open(photo_path, "rb") as f:
                    r = requests.post(
                        f"{self._base}/sendPhoto",
                        data={
                            "chat_id": self._chat_id,
                            "caption": caption,
                            "parse_mode": "HTML",
                        },
                        files={"photo": f},
                        timeout=30,
                    )
                if r.status_code == 200 and r.json().get("ok"):
                    self.sent_count += 1
                    return True
                log.warning("sendPhoto failed: %s", r.text)
            except Exception as exc:
                log.warning("sendPhoto error (attempt %d): %s", attempt + 1, exc)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BASE_SEC * (2 ** attempt))
        return False

    def send_message(self, text: str) -> bool:
        if not self._ok:
            return False
        for attempt in range(MAX_RETRIES):
            try:
                r = requests.post(
                    f"{self._base}/sendMessage",
                    data={
                        "chat_id": self._chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                    },
                    timeout=15,
                )
                if r.status_code == 200 and r.json().get("ok"):
                    self.sent_count += 1
                    return True
                log.warning("sendMessage failed: %s", r.text)
            except Exception as exc:
                log.warning("sendMessage error (attempt %d): %s", attempt + 1, exc)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BASE_SEC * (2 ** attempt))
        return False
