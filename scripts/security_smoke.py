"""本地安全/并发探针（只作回归，不作为商业容量 PASS）。

示例：
    uv run python ../scripts/security_smoke.py --base-url http://127.0.0.1:8000 \
        --path /api/v1/products --concurrency 20 --duration 10

输出 JSON：请求数、状态码分布、P50/P95/P99、RPS。没有第三方依赖。
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request


def _one(url: str, timeout: float) -> tuple[int, float]:
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"User-Agent": "starium-load-probe/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 本地探针
            response.read(1024)
            return response.status, (time.perf_counter() - started) * 1000
    except urllib.error.HTTPError as exc:
        return exc.code, (time.perf_counter() - started) * 1000
    except Exception:  # noqa: BLE001 - 网络错误统一计入失败
        return 0, (time.perf_counter() - started) * 1000


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--path", default="/api/v1/products")
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--duration", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    url = args.base_url.rstrip("/") + args.path
    stop = time.perf_counter() + max(1, args.duration)
    lock = threading.Lock()
    latencies: list[float] = []
    statuses: dict[int, int] = {}

    def worker() -> None:
        local: list[float] = []
        local_status: dict[int, int] = {}
        while time.perf_counter() < stop:
            status, elapsed = _one(url, args.timeout)
            local.append(elapsed)
            local_status[status] = local_status.get(status, 0) + 1
        with lock:
            latencies.extend(local)
            for key, value in local_status.items():
                statuses[key] = statuses.get(key, 0) + value

    threads = [threading.Thread(target=worker) for _ in range(max(1, args.concurrency))]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = max(0.001, time.perf_counter() - started)

    ordered = sorted(latencies)

    def percentile(value: float) -> float:
        if not ordered:
            return 0.0
        index = min(len(ordered) - 1, int(len(ordered) * value))
        return round(ordered[index], 2)

    summary = {
        "url": url,
        "concurrency": args.concurrency,
        "duration_seconds": round(elapsed, 2),
        "requests": len(latencies),
        "rps": round(len(latencies) / elapsed, 1),
        "statuses": {str(key): value for key, value in sorted(statuses.items())},
        "latency_ms": {
            "mean": round(statistics.fmean(ordered), 2) if ordered else 0.0,
            "p50": percentile(0.50),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["statuses"].get("0", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
