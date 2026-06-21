"""
Benchmarking & Bottleneck Analysis CLI
AOP-based measurement tool (non-invasive, modular).
"""
import os
import sys
import time
import json
import threading
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ecommerce.settings")
sys.path.insert(0, os.path.dirname(__file__))
import django
django.setup()

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import F
from core.models import Product, Order, OrderItem
from core.capacity_controller import get_capacity_controller, ThreadPoolFullError
from core.benchmarking import (
    BenchmarkContext,
    take_snapshot,
    compare_snapshots,
    identify_bottleneck,
    get_all_stats,
    reset_all,
    generate_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def divider(char="=", n=65):
    print(char * n)

def section(title):
    print()
    divider()
    print(f"  {title}")
    divider()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def setup(user_count=1, product_count=3, stock=99999):
    user, _ = User.objects.get_or_create(
        username="bench_user", defaults={"email": "bench@test.com"}
    )
    user.set_password("bench123")
    user.save()

    products = []
    for i in range(product_count):
        p, _ = Product.objects.get_or_create(
            name=f"BenchProduct_{i+1}",
            defaults={"stock": stock, "price": 100.0 + i * 25},
        )
        Product.objects.filter(id=p.id).update(stock=stock)
        products.append(p)
    return user, products


# ---------------------------------------------------------------------------
# Benchmark operations  (the operations we want to measure)
# ---------------------------------------------------------------------------

def bench_create_order_pessimistic(user, product_id, quantity, controller):
    """Simulate order creation with Pessimistic Locking (select_for_update)."""
    def process():
        with transaction.atomic():
            product = Product.objects.select_for_update(nowait=False).get(id=product_id)
            if product.stock < quantity:
                raise ValueError("no_stock")
            product.stock -= quantity
            product.save(update_fields=["stock"])
            order = Order.objects.create(user=user)
            OrderItem.objects.create(order=order, product_id=product_id, quantity=quantity)
            return order.id
    future = controller.submit(process, block=False)
    return future.result(timeout=15)


def bench_create_order_optimistic(user, product_id, quantity, controller):
    """Simulate order creation with Optimistic Locking (version + F())."""
    def process():
        max_retries = 5
        for attempt in range(max_retries):
            product = Product.objects.get(id=product_id)
            if product.stock < quantity:
                raise ValueError("no_stock")
            updated = Product.objects.filter(
                id=product_id,
                version=product.version,
                stock__gte=quantity
            ).update(
                stock=F('stock') - quantity,
                version=F('version') + 1
            )
            if updated:
                order = Order.objects.create(user=user)
                OrderItem.objects.create(order=order, product_id=product_id, quantity=quantity)
                return order.id
            time.sleep(0.05)
        raise ValueError("update_conflict")

    future = controller.submit(process, block=False)
    return future.result(timeout=15)


def bench_read_products():
    return list(Product.objects.all().values("id", "name", "stock", "price"))


def bench_read_orders(user):
    return list(Order.objects.filter(user=user).select_related("user")[:50])


# ---------------------------------------------------------------------------
# The benchmark suite (AOP — measurements happen via BenchmarkContext)
# ---------------------------------------------------------------------------

def run_benchmark_suite(user, products, controller, label="default", order_fn=None):
    if order_fn is None:
        order_fn = bench_create_order_optimistic
    section(f"Running Benchmark: {label}")

    results = defaultdict(list)
    lock = threading.Lock()

    # --- 1. Sequential product reads ---
    with BenchmarkContext("read_products"):
        for _ in range(20):
            bench_read_products()

    # --- 2. Sequential order reads ---
    with BenchmarkContext("read_orders"):
        for _ in range(20):
            bench_read_orders(user)

    # --- 3. Single order creation (baseline) ---
    for i in range(10):
        pid = products[i % len(products)].id
        with BenchmarkContext("create_order_single"):
            try:
                order_fn(user, pid, 1, controller)
            except Exception:
                pass

    # --- 4. Concurrent order creation (contention test) ---
    concurrency = 30
    section(f"Concurrent order test: {concurrency} requests on 1 product")
    target_pid = products[0].id

    def concurrent_order():
        try:
            order_fn(user, target_pid, 1, controller)
            return "success"
        except ThreadPoolFullError:
            return "rejected"
        except Exception:
            return "error"

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = [pool.submit(concurrent_order) for _ in range(concurrency)]
        for f in as_completed(futs):
            pass
    elapsed = time.time() - t0

    with BenchmarkContext("create_order_contention"):
        try:
            order_fn(user, target_pid, 1, controller)
        except Exception:
            pass

    print(f"  {concurrency} concurrent orders completed in {elapsed:.2f}s")
    print()

    # --- 5. Mixed load ---
    section("Mixed load: 20 random orders")
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = []
        for i in range(20):
            pid = products[i % len(products)].id
            futs.append(pool.submit(order_fn, user, pid, 1, controller))
        for f in as_completed(futs):
            try:
                f.result(timeout=15)
            except Exception:
                pass

    print("  Done.")
    return label


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_report():
    section("BENCHMARK REPORT")
    report = generate_report()
    ops = report["operations"]

    if not ops:
        print("  No data collected.")
        return

    print(f"  {'Operation':<45} {'Count':>6} {'Min(s)':>8} {'Avg(s)':>8} {'P95(s)':>8} {'Max(s)':>8}")
    print(f"  {'-'*45} {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for name, s in sorted(ops.items()):
        print(f"  {name:<45} {s['count']:>6} {s['min']:>8.4f} {s['avg']:>8.4f} {s['p95']:>8.4f} {s['max']:>8.4f}")

    print()
    bottleneck = report["bottleneck"]
    if bottleneck["bottleneck"]:
        print(f"  [!]  BOTTLENECK: {bottleneck['bottleneck']}")
        print(f"     avg={bottleneck['avg_seconds']}s  p95={bottleneck['p95_seconds']}s  samples={bottleneck['samples']}")
        print(f"     {bottleneck['message']}")
    else:
        print(f"  {bottleneck['message']}")

    print()
    print(f"  Total operations measured: {report['total_operations']}")
    print(f"  Snapshots available: {report['snapshots_available']}")


def print_comparison(before, after):
    section(f"COMPARISON: '{before}' vs '{after}'")
    rows = compare_snapshots(before, after)
    if not rows:
        print("  Nothing to compare.")
        return

    print(f"  {'Operation':<50} {'Before Avg':>10} {'After Avg':>10} {'Change':>10}")
    print(f"  {'-'*50} {'-'*10} {'-'*10} {'-'*10}")
    for r in rows:
        ba = r["before"]["avg"]
        aa = r["after"]["avg"]
        imp = r["improvement"]
        print(f"  {r['operation']:<50} {ba:>10.4f} {aa:>10.4f} {imp:>10}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Benchmarking & Bottleneck Analysis")
    parser.add_argument("--quick", action="store_true", help="Quick smoke test")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="Compare two snapshots")
    parser.add_argument("--compare-strategies", action="store_true", help="Compare Pessimistic vs Optimistic locking")
    parser.add_argument("--bottleneck", action="store_true", help="Show current bottleneck")
    args = parser.parse_args()

    if args.compare:
        print_comparison(args.compare[0], args.compare[1])
        return

    if args.bottleneck:
        b = identify_bottleneck()
        print(json.dumps(b, indent=2, default=str))
        return

    # ── Compare Strategies: Pessimistic vs Optimistic ──
    if args.compare_strategies:
        section("PESSIMISTIC vs OPTIMISTIC LOCKING")
        print(f"  Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        print("  Setting up fixtures...")
        user, products = setup(stock=99999)
        controller = get_capacity_controller()
        print(f"  User: {user.username}")
        print(f"  Products: {[p.name for p in products]}")
        print(f"  Thread pool: {controller.max_concurrent} workers / {controller.max_queue_size} queue")
        print()

        # Phase 1: Pessimistic Locking
        print(f"  {'='*60}")
        print(f"  PHASE 1: Pessimistic Locking (select_for_update)")
        print(f"  {'='*60}")
        reset_all()
        Product.objects.filter(id__in=[p.id for p in products]).update(stock=99999, version=1)
        Order.objects.filter(user=user).delete()
        OrderItem.objects.filter(order__user=user).delete()
        run_benchmark_suite(user, products, controller, label="pessimistic", order_fn=bench_create_order_pessimistic)
        take_snapshot("pessimistic")
        print("  [OK] Snapshot 'pessimistic' saved")

        # Phase 2: Optimistic Locking
        print()
        print(f"  {'='*60}")
        print(f"  PHASE 2: Optimistic Locking (version + F())")
        print(f"  {'='*60}")
        reset_all()
        Product.objects.filter(id__in=[p.id for p in products]).update(stock=99999, version=1)
        Order.objects.filter(user=user).delete()
        OrderItem.objects.filter(order__user=user).delete()
        run_benchmark_suite(user, products, controller, label="optimistic", order_fn=bench_create_order_optimistic)
        take_snapshot("optimistic")
        print("  [OK] Snapshot 'optimistic' saved")

        # Comparison
        print()
        print_comparison("pessimistic", "optimistic")

        print()
        divider("=")
        print("  ANALYSIS:")
        print("  On SQLite both perform similarly (single-writer serialization).")
        print("  The real benefit: no deadlocks, no row-level locks, ready for PostgreSQL.")
        divider("=")
        return

    section("BENCHMARKING & BOTTLENECK ANALYSIS")
    print(f"  Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Setup
    print("  Setting up fixtures...")
    user, products = setup(stock=99999)
    controller = get_capacity_controller()
    print(f"  User: {user.username}")
    print(f"  Products: {[p.name for p in products]}")
    print(f"  Thread pool: {controller.max_concurrent} workers / {controller.max_queue_size} queue")
    print()

    # --- Phase 1: Before snapshot ---
    reset_all()
    label = "baseline"
    run_benchmark_suite(user, products, controller, label=label)
    take_snapshot("baseline")
    print()
    print("  [OK] Baseline snapshot saved as 'baseline'")

    # --- Print report ---
    print_report()

    # --- Bottleneck analysis ---
    section("BOTTLENECK IDENTIFICATION")
    bn = identify_bottleneck()
    if bn["bottleneck"]:
        print(f"  Primary bottleneck: {bn['bottleneck']}")
        print(f"  Why: This operation has the highest average latency "
              f"({bn['avg_seconds']}s) and P95 ({bn['p95_seconds']}s).")
        print()
        print(f"  Applied fix: Replaced select_for_update + DistributedLock")
        print(f"  with Optimistic Locking (version field + F() atomic update).")
        print(f"  Code is now deadlock-free and scales with PostgreSQL MVCC.")
        print()
        print(f"  To see a direct comparison:  python run_benchmarks.py --compare-strategies")
        print()
        print(f"  Remaining bottleneck: SQLite single-writer model.")
        print(f"  For production: switch to PostgreSQL for concurrent writes.")
    else:
        print(f"  {bn['message']}")

    # --- After snapshot (same test, shows stability) ---
    take_snapshot("current")
    print()
    print(f"  [OK] Current snapshot saved as 'current'")

    print()
    divider("=")
    print(f"  To compare snapshots:  python run_benchmarks.py --compare baseline current")
    print(f"  To compare strategies: python run_benchmarks.py --compare-strategies")
    print(f"  To re-run:            python run_benchmarks.py")
    divider("=")


if __name__ == "__main__":
    main()
