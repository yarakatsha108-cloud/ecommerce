
import os
import sys
import django
import threading
import time
import statistics
import psutil
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

# ── Django Setup ──────────────────────────────────────────────────────────────
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ecommerce.settings')
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import F
from core.models import Product, Order, OrderItem
from core.capacity_controller import get_capacity_controller
try:
    from core.capacity_controller import ThreadPoolFullError
except ImportError:
    class ThreadPoolFullError(Exception):
        pass
from core.async_tasks import get_task_queue
from concurrent.futures import TimeoutError as FutureTimeoutError


# ── Helpers ──

def divider(char="=", n=65):
    print(char * n)

def section(title):
    print()
    divider()
    print(f"  {title}")
    divider()

def cpu_ram():
    proc = psutil.Process()
    return {
        "cpu"    : psutil.cpu_percent(interval=0.3),
        "ram_pct": psutil.virtual_memory().percent,
        "ram_mb" : round(psutil.virtual_memory().used / 1024**2, 1),
    }


# ── Fixture Setup ──

def setup_fixtures(num_products=5, stock_per_product=99999):
    """Creates a test user and products; resets stock."""
    user, _ = User.objects.get_or_create(
        username="stress_user",
        defaults={"email": "stress@test.com"}
    )
    user.set_password("stress123")
    user.save()

    products = []
    for i in range(num_products):
        p, _ = Product.objects.get_or_create(
            name=f"StressProduct_{i+1}",
            defaults={"stock": stock_per_product, "price": 50.00 + i * 10}
        )
        # Reset stock each run
        Product.objects.filter(id=p.id).update(stock=stock_per_product)
        products.append(p)

    return user, products


# ── Order Simulation ───

def simulate_order(user, product_id, quantity, controller, results, lock):
    """Simulates a single order through the capacity controller (same logic as views.py)."""
    start = time.time()

    def process_order():
        with transaction.atomic():
            updated = Product.objects.filter(
                id=product_id,
                stock__gte=quantity
            ).update(stock=F('stock') - quantity)
        if not updated:
            raise ValueError("Not enough stock")
        order = Order.objects.create(user=user)
        OrderItem.objects.create(order=order, product_id=product_id, quantity=quantity)
        return order.id

    outcome = "unknown"
    try:
        future = controller.submit(process_order, block=False)
        future.result(timeout=5)
        outcome = "success"
    except ThreadPoolFullError:
        outcome = "rejected"
    except FutureTimeoutError:
        outcome = "timeout"
    except ValueError:
        outcome = "no_stock"
    except Exception:
        outcome = "error"

    elapsed = round(time.time() - start, 4)

    with lock:
        results["outcomes"][outcome] += 1
        results["response_times"].append(elapsed)



#  TEST 1 — Concurrent Spike (اختبار الذروة المتزامنة)


def test_concurrent_spike(user, products, controller, num_requests=150, max_workers=50):
    section("TEST 1 — Concurrent Spike  (اختبار الذروة المتزامنة)")
    print(f"  Requests  : {num_requests}")
    print(f"  Workers   : {max_workers}")
    print(f"  Pool size : {controller.max_concurrent} workers / queue {controller.max_queue_size}")
    print()

    results = {"outcomes": defaultdict(int), "response_times": []}
    lock = threading.Lock()

    snap_before = cpu_ram()
    print(f"  [Before]  CPU {snap_before['cpu']}%  |  RAM {snap_before['ram_mb']} MB")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(
                simulate_order,
                user, products[i % len(products)].id, 1,
                controller, results, lock
            )
            for i in range(num_requests)
        ]
        for f in as_completed(futures):
            pass
    elapsed = round(time.time() - t0, 2)

    snap_after = cpu_ram()
    print(f"  [After]   CPU {snap_after['cpu']}%  |  RAM {snap_after['ram_mb']} MB")
    print(f"  CPU delta : {round(snap_after['cpu'] - snap_before['cpu'], 1)}%")
    print(f"  RAM delta : {round(snap_after['ram_mb'] - snap_before['ram_mb'], 1)} MB")
    print()

    _print_results(results, elapsed, label="Concurrent Spike")
    return results, elapsed



#  TEST 2 — Sustained Load (اختبار الحمل المستمر)

def test_sustained_load(user, products, controller, duration_sec=30, num_workers=20):
    section("TEST 2 — Sustained Load  (اختبار الحمل المستمر)")
    print(f"  Duration  : {duration_sec}s")
    print(f"  Workers   : {num_workers} continuous threads")
    print()

    results = {"outcomes": defaultdict(int), "response_times": []}
    lock = threading.Lock()
    stop = threading.Event()

    snapshots = []  # (timestamp, cpu, ram_mb)

    def worker(wid):
        idx = 0
        while not stop.is_set():
            simulate_order(
                user, products[idx % len(products)].id, 1,
                controller, results, lock
            )
            idx += 1

    def sampler():
        while not stop.is_set():
            s = cpu_ram()
            snapshots.append((time.time(), s["cpu"], s["ram_mb"]))
            time.sleep(2)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(num_workers)]
    sampler_thread = threading.Thread(target=sampler, daemon=True)

    t0 = time.time()
    sampler_thread.start()
    for t in threads:
        t.start()

    # Progress bar
    for sec in range(0, duration_sec, 5):
        time.sleep(5)
        done = sum(results["outcomes"].values())
        ok   = results["outcomes"]["success"]
        print(f"  t+{sec+5:2d}s  |  total={done}  success={ok}  rejected={results['outcomes']['rejected']}")

    stop.set()
    for t in threads:
        t.join(timeout=3)
    elapsed = round(time.time() - t0, 2)

    # Resource summary from samples
    if snapshots:
        cpus = [s[1] for s in snapshots]
        rams = [s[2] for s in snapshots]
        print()
        print(f"  Resource samples ({len(snapshots)}):")
        print(f"    CPU  — avg {round(statistics.mean(cpus),1)}%  max {max(cpus)}%")
        print(f"    RAM  — avg {round(statistics.mean(rams),1)} MB  max {max(rams)} MB")
    print()

    _print_results(results, elapsed, label="Sustained Load")
    return results, elapsed



#  TEST 3 — Recovery (اختبار الانتعاش بعد الإغراق)


def test_recovery(user, products, controller):
    section("TEST 3 — Recovery After Flood  (اختبار الانتعاش)")
    print("  Phase A: flood the system with 200 requests in 2 seconds")
    print("  Phase B: wait 5 seconds (cool-down)")
    print("  Phase C: send 20 normal requests — expect near-100% success")
    print()

    lock = threading.Lock()

    # Phase A — flood
    flood_results = {"outcomes": defaultdict(int), "response_times": []}
    with ThreadPoolExecutor(max_workers=80) as pool:
        futs = [
            pool.submit(simulate_order, user, products[i % len(products)].id, 1,
                        controller, flood_results, lock)
            for i in range(200)
        ]
        for f in as_completed(futs):
            pass

    flood_success  = flood_results["outcomes"]["success"]
    flood_rejected = flood_results["outcomes"]["rejected"]
    print(f"  [Phase A] success={flood_success}  rejected={flood_rejected}  "
          f"timeout={flood_results['outcomes']['timeout']}")

    # Phase B — cool-down
    print("  [Phase B] Cooling down 5 seconds...")
    time.sleep(5)
    status = controller.get_status()
    print(f"  [Phase B] Queue size after cool-down: {status['queue_size']}")

    # Phase C — normal requests
    normal_results = {"outcomes": defaultdict(int), "response_times": []}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = [
            pool.submit(simulate_order, user, products[i % len(products)].id, 1,
                        controller, normal_results, lock)
            for i in range(20)
        ]
        for f in as_completed(futs):
            pass
    elapsed = round(time.time() - t0, 2)

    normal_success = normal_results["outcomes"]["success"]
    recovery_rate  = round(normal_success / 20 * 100, 1)
    print(f"  [Phase C] success={normal_success}/20  recovery_rate={recovery_rate}%")
    print()

    recovered = recovery_rate >= 90
    print(f"  Recovery verdict: {'PASS — system recovered successfully' if recovered else 'FAIL — system did NOT recover'}")
    return flood_results, normal_results, recovery_rate



#  TEST 4 — Async Queue Under Pressure


def test_async_queue_pressure(queue_obj, num_tasks=500):
    section("TEST 4 — Async Queue Under Pressure  (ضغط طابور المهام)")
    print(f"  Enqueueing {num_tasks} notification tasks rapidly...")
    print()

    accepted = 0
    dropped  = 0
    t0 = time.time()

    for i in range(num_tasks):
        ok = queue_obj.enqueue('send_notification', 1, f"stress_msg_{i}")
        if ok:
            accepted += 1
        else:
            dropped += 1

    elapsed = round(time.time() - t0, 3)
    status  = queue_obj.get_status()

    print(f"  Enqueue time   : {elapsed}s  ({round(num_tasks/elapsed) if elapsed > 0 else '∞'} tasks/sec)")
    print(f"  Accepted       : {accepted}")
    print(f"  Dropped (full) : {dropped}")
    print(f"  Queue size now : {status['queue_size']}")
    print(f"  Active workers : {status['active_workers']}/{queue_obj.num_workers}")

    # Wait for workers to drain
    print()
    print("  Waiting for workers to drain queue...")
    wait_start = time.time()
    for _ in range(60):
        time.sleep(1)
        if queue_obj.queue.qsize() == 0:
            break
    drain_time = round(time.time() - wait_start, 1)
    print(f"  Queue drained in {drain_time}s")
    print(f"  Drop rate      : {round(dropped/num_tasks*100, 1)}%")



#  Results Printer


def _print_results(results, elapsed, label=""):
    outcomes = results["outcomes"]
    times    = results["response_times"]
    total    = sum(outcomes.values())

    success  = outcomes["success"]
    rejected = outcomes["rejected"]
    timeout  = outcomes["timeout"]
    error    = outcomes.get("error", 0) + outcomes.get("no_stock", 0)

    success_rate = round(success / total * 100, 1) if total else 0
    rps          = round(total / elapsed, 1) if elapsed else 0

    print(f"  ── {label} Results ──────────────────────────")
    print(f"  Total requests  : {total}")
    print(f"  Success         : {success}  ({success_rate}%)")
    print(f"  Rejected (503)  : {rejected}")
    print(f"  Timeout         : {timeout}")
    print(f"  Other errors    : {error}")
    print(f"  Elapsed         : {elapsed}s")
    print(f"  Throughput      : {rps} req/s")

    if times:
        print(f"  Response times  :")
        print(f"    min  {min(times):.3f}s")
        print(f"    avg  {round(statistics.mean(times), 3):.3f}s")
        print(f"    p95  {round(sorted(times)[int(len(times)*0.95)], 3):.3f}s")
        print(f"    max  {max(times):.3f}s")

    # Verdict
    if success_rate >= 80:
        verdict = "PASS"
    elif success_rate >= 60:
        verdict = "WARN"
    else:
        verdict = "FAIL"

    print(f"  Verdict         : {verdict}  (success rate {success_rate}%)")
    return success_rate



#  Final Report


def print_final_report(spike_res, spike_t, sustained_res, sustained_t,
                        flood_res, normal_res, recovery_rate):
    section("FINAL STRESS TEST REPORT  (التقرير النهائي)")

    def rate(res):
        total = sum(res["outcomes"].values())
        ok    = res["outcomes"]["success"]
        return round(ok / total * 100, 1) if total else 0

    spike_rate     = rate(spike_res)
    sustained_rate = rate(sustained_res)

    rows = [
        ("Concurrent Spike (150 req, 50 workers)", spike_rate,
         "PASS" if spike_rate >= 80 else "FAIL"),
        ("Sustained Load (30s, 20 workers)",        sustained_rate,
         "PASS" if sustained_rate >= 70 else "FAIL"),
        ("Recovery after flood",                    recovery_rate,
         "PASS" if recovery_rate >= 90 else "FAIL"),
    ]

    print(f"  {'Test':<42} {'Success%':>9}  {'Verdict':>7}")
    print(f"  {'-'*42} {'-'*9}  {'-'*7}")
    for name, rate_val, verdict in rows:
        print(f"  {name:<42} {rate_val:>8.1f}%  {verdict:>7}")

    print()
    all_pass = all(r[2] == "PASS" for r in rows)
    overall  = "SYSTEM STABLE UNDER STRESS" if all_pass else "SYSTEM NEEDS TUNING"
    print(f"  Overall: {'✓ ' if all_pass else '✗ '}{overall}")
    print()
    print(f"  Tested at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    divider()



#  Entry Point


if __name__ == "__main__":
    section("Stress Testing Suite — Initializing")

    # System info
    snap = cpu_ram()
    print(f"  CPU cores : {psutil.cpu_count()}")
    print(f"  RAM total : {round(psutil.virtual_memory().total / 1024**2)} MB")
    print(f"  CPU now   : {snap['cpu']}%")
    print(f"  RAM now   : {snap['ram_mb']} MB")

    # Setup
    print()
    print("  Setting up fixtures...")
    user, products = setup_fixtures(num_products=5, stock_per_product=999999)
    print(f"  User      : {user.username}")
    print(f"  Products  : {[p.name for p in products]}")

    controller = get_capacity_controller()
    queue_obj  = get_task_queue()
    queue_obj.start_workers()

    # Run tests
    spike_res,   spike_t   = test_concurrent_spike(user, products, controller,
                                                    num_requests=150, max_workers=50)

    sustained_res, sustained_t = test_sustained_load(user, products, controller,
                                                      duration_sec=30, num_workers=20)

    flood_res, normal_res, recovery_rate = test_recovery(user, products, controller)

    test_async_queue_pressure(queue_obj, num_tasks=500)

    # Final report
    print_final_report(spike_res, spike_t, sustained_res, sustained_t,
                       flood_res, normal_res, recovery_rate)
