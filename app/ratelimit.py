"""进程内限流器（方案第十节限流熔断的 MVP 实现）。

仅适用于单进程试点。多实例部署时应替换为 Redis 计数（接口保持一致）。
"""
import threading
import time
from collections import defaultdict, deque
from datetime import date
from typing import Deque, Dict, Tuple

_lock = threading.Lock()
# key -> 最近一分钟内的请求时间戳队列
_minute_buckets: Dict[str, Deque[float]] = defaultdict(deque)
# (key, yyyy-mm-dd) -> 当日请求计数
_daily_counts: Dict[Tuple[str, str], int] = defaultdict(int)


def check_and_incr_minute(key: str, limit_per_minute: int) -> bool:
    """检查并占用一次每分钟配额。返回 True 表示放行。"""
    if limit_per_minute <= 0:
        return True
    now = time.time()
    window_start = now - 60
    with _lock:
        bucket = _minute_buckets[key]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= limit_per_minute:
            return False
        bucket.append(now)
        return True


def check_and_incr_daily(key: str, daily_limit: int) -> bool:
    """检查并占用一次每日请求配额。返回 True 表示放行。"""
    if daily_limit <= 0:
        return True
    today = date.today().isoformat()
    with _lock:
        count = _daily_counts[(key, today)]
        if count >= daily_limit:
            return False
        _daily_counts[(key, today)] = count + 1
        return True


def reset_all() -> None:
    """主要用于测试。"""
    with _lock:
        _minute_buckets.clear()
        _daily_counts.clear()
