"""轻量级 Prometheus 指标（进程内计数器，零额外依赖）。

通过 /metrics 以 Prometheus 文本格式暴露，供 Prometheus + Grafana 采集（方案第十七节）。
多实例部署时各实例各自暴露，由 Prometheus 按实例聚合。
"""
import threading
from collections import defaultdict
from typing import Dict, Tuple

_lock = threading.Lock()
# (metric_name, sorted label tuple) -> value
_counters: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = defaultdict(float)

_HELP = {
    "relay_requests_total": "LLM 调用次数",
    "relay_errors_total": "调用错误次数",
    "relay_tokens_total": "累计 token 数",
    "relay_points_total": "累计扣点数",
    "relay_jobs_total": "批量任务数",
}


def inc(name: str, value: float = 1.0, **labels: str) -> None:
    key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
    with _lock:
        _counters[key] += value


def _fmt_labels(label_tuple: Tuple[Tuple[str, str], ...]) -> str:
    if not label_tuple:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in label_tuple)
    return "{" + inner + "}"


def render(extra_gauges: Dict[str, float] = None) -> str:
    lines = []
    seen_help = set()
    with _lock:
        snapshot = dict(_counters)
    for (name, labels), value in sorted(snapshot.items()):
        if name not in seen_help:
            if name in _HELP:
                lines.append(f"# HELP {name} {_HELP[name]}")
            lines.append(f"# TYPE {name} counter")
            seen_help.add(name)
        lines.append(f"{name}{_fmt_labels(labels)} {value}")
    for gname, gval in (extra_gauges or {}).items():
        lines.append(f"# TYPE {gname} gauge")
        lines.append(f"{gname} {gval}")
    return "\n".join(lines) + "\n"


def reset() -> None:
    with _lock:
        _counters.clear()
