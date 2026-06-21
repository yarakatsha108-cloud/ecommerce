import time
import statistics
import threading
import logging
import json
import os
from functools import wraps
from collections import defaultdict
from datetime import datetime, timedelta
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

_SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "benchmark_snapshots")
os.makedirs(_SNAPSHOT_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Thread-safe benchmark data store
# ---------------------------------------------------------------------------
_benchmark_data = defaultdict(list)
_data_lock = threading.Lock()

_snapshots = {}
_snapshot_lock = threading.Lock()

BENCHMARK_ENABLED = True


def _record(name, duration):
    if not BENCHMARK_ENABLED:
        return
    with _data_lock:
        _benchmark_data[name].append({
            "timestamp": datetime.now(),
            "duration": duration,
        })


def _stats(values):
    if not values:
        return {"count": 0, "min": 0, "avg": 0, "p95": 0, "max": 0, "total": 0}
    s = sorted(values)
    return {
        "count": len(s),
        "min": round(min(s), 4),
        "avg": round(statistics.mean(s), 4),
        "p95": round(s[int(len(s) * 0.95)], 4),
        "max": round(max(s), 4),
        "total": round(sum(s), 4),
    }


# ---------------------------------------------------------------------------
# AOP — Context Manager  (for wrapping code blocks)
# ---------------------------------------------------------------------------
class BenchmarkContext:
    """Time a block of code using `with BenchmarkContext('name'):``"""

    def __init__(self, name):
        self.name = name
        self.start = None

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *args):
        _record(self.name, time.perf_counter() - self.start)


# ---------------------------------------------------------------------------
# AOP — Decorator  (for wrapping functions / methods)
# ---------------------------------------------------------------------------
def benchmark(name=None):
    """Decorator: @benchmark or @benchmark('custom_name')"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            label = name or f"{func.__module__}.{func.__qualname__}"
            with BenchmarkContext(label):
                return func(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# AOP — Django Middleware  (times every HTTP request)
# ---------------------------------------------------------------------------
class BenchmarkMiddleware(MiddlewareMixin):
    """Add to MIDDLEWARE to time every request automatically."""

    def __init__(self, get_response):
        self.get_response = get_response

    def process_request(self, request):
        request._bm_start = time.perf_counter()
        return None

    def process_response(self, request, response):
        start = getattr(request, "_bm_start", None)
        if start:
            label = f"HTTP {request.method} {request.path}"
            _record(label, time.perf_counter() - start)
        return response


# ---------------------------------------------------------------------------
# Snapshot system  — for before / after comparison (persisted to disk)
# ---------------------------------------------------------------------------
def _snapshot_path(name):
    safe = name.replace(" ", "_").replace("/", "_")
    return os.path.join(_SNAPSHOT_DIR, f"{safe}.json")


def take_snapshot(name):
    with _data_lock:
        data = {k: [{"duration": x["duration"], "timestamp": x["timestamp"].isoformat()}
                      for x in v] for k, v in _benchmark_data.items()}
    path = _snapshot_path(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with _snapshot_lock:
        _snapshots[name] = data
    return name


def _load_snapshot(name):
    path = _snapshot_path(name)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_snapshots():
    files = sorted(os.listdir(_SNAPSHOT_DIR)) if os.path.isdir(_SNAPSHOT_DIR) else []
    return [os.path.splitext(f)[0] for f in files if f.endswith(".json")]


def compare_snapshots(before, after):
    b = _load_snapshot(before)
    a = _load_snapshot(after)
    if not b and not a:
        return []
    all_keys = set(b.keys()) | set(a.keys())
    rows = []
    for key in sorted(all_keys):
        b_vals = [x["duration"] for x in b.get(key, [])]
        a_vals = [x["duration"] for x in a.get(key, [])]
        bs = _stats(b_vals)
        ac = _stats(a_vals)
        improvement = ""
        if bs["avg"] and ac["avg"]:
            pct = round((bs["avg"] - ac["avg"]) / bs["avg"] * 100, 1)
            improvement = f"{pct:+.1f}%"
        rows.append({
            "operation": key,
            "before": bs,
            "after": ac,
            "improvement": improvement,
        })
    return rows


# ---------------------------------------------------------------------------
# Stats & report helpers
# ---------------------------------------------------------------------------
def get_all_stats():
    with _data_lock:
        keys = list(_benchmark_data.keys())
    result = {}
    for key in sorted(keys):
        with _data_lock:
            vals = [x["duration"] for x in _benchmark_data[key]]
        result[key] = _stats(vals)
    return result


def get_stats(name):
    with _data_lock:
        vals = [x["duration"] for x in _benchmark_data.get(name, [])]
    return _stats(vals)


def reset_all():
    with _data_lock:
        _benchmark_data.clear()


def reset_key(name):
    with _data_lock:
        _benchmark_data.pop(name, None)


# ---------------------------------------------------------------------------
# Bottleneck analysis
# ---------------------------------------------------------------------------
def identify_bottleneck():
    """Identify the single biggest bottleneck from collected metrics."""
    stats = get_all_stats()
    if not stats:
        return {"bottleneck": None, "message": "No benchmark data collected yet."}

    scored = []
    for name, s in stats.items():
        if s["count"] < 2:
            continue
        score = s["avg"] * 0.4 + s["p95"] * 0.4 + s["max"] * 0.2
        scored.append((score, name, s))

    if not scored:
        return {"bottleneck": None, "message": "Insufficient data to identify bottleneck."}

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[0]
    return {
        "bottleneck": top[1],
        "avg_seconds": top[2]["avg"],
        "p95_seconds": top[2]["p95"],
        "max_seconds": top[2]["max"],
        "samples": top[2]["count"],
        "score": round(top[0], 4),
        "message": (
            f"Bottleneck identified: '{top[1]}' "
            f"(avg={top[2]['avg']}s, p95={top[2]['p95']}s, {top[2]['count']} samples). "
        ),
    }


def generate_report():
    stats = get_all_stats()
    bottleneck = identify_bottleneck()
    snapshots = list_snapshots()
    return {
        "operations": stats,
        "bottleneck": bottleneck,
        "total_operations": sum(s["count"] for s in stats.values()),
        "snapshots_available": snapshots,
        "generated_at": datetime.now().isoformat(),
    }
