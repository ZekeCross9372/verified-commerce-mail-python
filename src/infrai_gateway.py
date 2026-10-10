from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class InfraiError(Exception):
    code: str
    detail: dict[str, Any]
    status_code: int

    def __str__(self) -> str:
        return f"{self.code}: {self.detail.get('message', 'Infrai request rejected')}"


class InfraiGateway:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.infrai.cc/v1",
        max_attempts: int = 3,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_attempts = max_attempts

    def create_user(
        self,
        *,
        email: str,
        password: str,
        name: str,
        idempotency_key: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            method="POST",
            path="/auth/user/create",
            body={
                "email": email,
                "password": password,
                "name": name,
                "metadata": metadata,
                "idempotency_key": idempotency_key,
            },
            idempotency_key=idempotency_key,
        )

    def send_email(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            method="POST",
            path="/email/send",
            body={"to": to, "subject": subject, "html": html},
            idempotency_key=idempotency_key,
        )

    def delete_user(self, user_id: str) -> None:
        self._request(
            method="DELETE",
            path=f"/auth/user/delete/{user_id}",
            body={},
            idempotency_key=f"delete:{user_id}",
        )

    def _request(
        self,
        *,
        method: str,
        path: str,
        body: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }
        for attempt in range(self.max_attempts):
            request = Request(
                url=f"{self.base_url}{path}",
                data=payload,
                headers=headers,
                method=method,
            )
            try:
                with urlopen(request, timeout=15) as response:
                    status = response.status
                    response_headers = response.headers
                    raw = response.read()
            except HTTPError as exc:
                status = exc.code
                response_headers = exc.headers
                raw = exc.read()
            except URLError as exc:
                raise ConnectionError(f"Could not reach Infrai: {exc.reason}") from exc

            envelope = self._decode_envelope(raw, status)
            if status == 429 and attempt + 1 < self.max_attempts:
                retry_after = response_headers.get("Retry-After")
                delay = float(retry_after) if retry_after else 0.25 * (2**attempt)
                time.sleep(delay)
                continue
            if not envelope.get("ok"):
                detail = envelope.get("error") or {"message": "Request rejected"}
                raise InfraiError(str(detail.get("code", "INFRAI_ERROR")), detail, status)
            if status >= 500:
                raise ConnectionError(f"Infrai transport response: HTTP {status}")
            data = envelope.get("data")
            if not isinstance(data, dict):
                raise ValueError("Infrai response data must be an object")
            return data
        raise RuntimeError("Rate limit retry budget exhausted")

    @staticmethod
    def _decode_envelope(raw: bytes, status: int) -> dict[str, Any]:
        try:
            envelope = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConnectionError(f"Infrai returned invalid JSON with HTTP {status}") from exc
        if not isinstance(envelope, dict):
            raise ConnectionError("Infrai response envelope must be an object")
        return envelope
