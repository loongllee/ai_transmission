"""限流器（方案第十节）。

默认进程内限流（单实例试点）；配置 REDIS_URL 后启用 Redis 固定窗口限流，
多实例部署时计数一致。Redis 不可用时自动回退进程内，保证可用性。
"""
import threading
import time
from collections import defaultdict, deque
from datetime import date
from typing import Deque, Dict, Optional, Tuple

from .config import settings

# ----------------------------- 进程内实现 -----------------------------
_lock = threading.Lock()
_minute_buckets: Dict[str, Deque[float]] = defaultdict(deque)
_daily_counts: Dict[Tuple[str, str], int] = defaultdict(int)


def _mem_minute(key: str, limit: int) -> bool:
    now = time.time()
    window_start = now - 60
    with _lock:
        bucket = _minute_buckets[key]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def _mem_daily(key: str, limit: int) -> bool:
    today = date.today().isoformat()
    with _lock:
        count = _daily_counts[(key, today)]
        if count >= limit:
            return False
        _daily_counts[(key, today)] = count + 1
        return True


# ----------------------------- Redis 实现 -----------------------------
_redis_client = None
_redis_tried = False


def _get_redis():
    global _redis_client, _redis_tried
    if _redis_tried:
        return _redis_client
    _redis_tried = True
    if not settings.redis_url:
        return None
    try:
        import redis  # 可选依赖

        client = redis.Redis.from_url(settings.redis_url, socket_timeout=1.0, socket_connect_timeout=1.0)
        client.ping()
        _redis_client = client
    except Exception:  # noqa: BLE001  连接失败则回退进程内
        _redis_client = None
    return _redis_client


def _redis_fixed_window(redis_key: str, limit: int, ttl: int) -> Optional[bool]:
    client = _get_redis()
    if client is None:
        return None
    try:
        count = client.incr(redis_key)
        if count == 1:
            client.expire(redis_key, ttl)
        return int(count) <= limit
    except Exception:  # noqa: BLE001  运行期 Redis 故障 -> 回退
        return None


# ----------------------------- 对外接口 -----------------------------
def check_and_incr_minute(key: str, limit_per_minute: int) -> bool:
    if limit_per_minute <= 0:
        return True
    minute_bucket = int(time.time()) // 60
    res = _redis_fixed_window(f"rl:m:{key}:{minute_bucket}", limit_per_minute, 60)
    if res is not None:
        return res
    return _mem_minute(key, limit_per_minute)


def check_and_incr_daily(key: str, daily_limit: int) -> bool:
    if daily_limit <= 0:
        return True
    res = _redis_fixed_window(f"rl:d:{key}:{date.today().isoformat()}", daily_limit, 86400)
    if res is not None:
        return res
    return _mem_daily(key, daily_limit)


def backend() -> str:
    return "redis" if _get_redis() is not None else "memory"


def reset_all() -> None:
    """主要用于测试。"""
    with _lock:
        _minute_buckets.clear()
        _daily_counts.clear()
