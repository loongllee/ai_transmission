"""异常调用检测与告警（方案第十五节异常告警 / 第二十节风控）。

进程内滑动窗口统计每个 Token 的错误次数，超过阈值则：
  1) 写入 Alert 记录；
  2) 若开启 alert_auto_disable_token，则自动停用该 Token（异常 API 调用封禁）。
"""
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Deque, Dict, Optional

from sqlalchemy.orm import Session

from .config import settings
from .models import Alert, UserApiToken

_lock = threading.Lock()
_error_windows: Dict[int, Deque[float]] = defaultdict(deque)


def on_call_error(db: Session, token: Optional[UserApiToken], user_id: int, error_code: str) -> None:
    """记录一次调用错误；达到阈值则告警并按配置封禁 Token。"""
    if token is None:
        return
    now = time.time()
    window = settings.alert_error_window_seconds
    threshold = settings.alert_error_threshold
    with _lock:
        dq = _error_windows[token.id]
        dq.append(now)
        while dq and dq[0] < now - window:
            dq.popleft()
        count = len(dq)
        triggered = count >= threshold
        if triggered:
            dq.clear()
    if not triggered:
        return

    auto_action = "none"
    if settings.alert_auto_disable_token:
        token.status = "disabled"
        auto_action = "token_disabled"
    db.add(
        Alert(
            user_id=user_id,
            token_id=token.id,
            alert_type="high_error_rate",
            severity="critical",
            message=f"Token#{token.id} 在 {window}s 内出现 {count} 次调用错误（最近错误码 {error_code}）",
            status="open",
            auto_action=auto_action,
            created_at=datetime.utcnow(),
        )
    )
    db.commit()


def reset() -> None:
    """测试用：清空窗口。"""
    with _lock:
        _error_windows.clear()
