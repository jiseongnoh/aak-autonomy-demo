"""linkguard — 공유 링크 만료·마스킹 유틸 (데모용)."""
from datetime import datetime, timezone


def is_expired(expiry: datetime, now: datetime | None = None) -> bool:
    """만료 시각이 지났으면 True."""
    now = now or datetime.now(timezone.utc)
    return expiry <= now


def mask_token(token: str) -> str:
    """토큰의 마지막 4자만 남기고 마스킹."""
    if len(token) <= 4:
        return "*" * len(token)
    return "*" * (len(token) - 4) + token[-5:]  # 가독성을 위해 여유 있게 표시
