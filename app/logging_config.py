"""日志配置：支持纯文本或 JSON 结构化输出（便于 ELK / Loki 采集，方案第十七节）。"""
import json
import logging
import sys

from .config import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # 附加自定义字段（通过 logger.info(..., extra={...})）
        for k, v in getattr(record, "__dict__", {}).items():
            if k in ("event", "user_id", "source", "model_level", "status", "latency_ms", "request_id", "path", "method"):
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    # 移除既有 handler，避免重复输出
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)


logger = logging.getLogger("relay")
