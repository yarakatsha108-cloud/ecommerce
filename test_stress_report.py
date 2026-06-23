import os
import sys
import django
import threading
import time
import random
import statistics
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ecommerce.settings')
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from django.contrib.auth.models import User
from core.models import Product

BASE_URL = "http://127.0.0.1:8000"
API_PREFIX = "/api"
LOGIN_PATH = "/api/login/"
AUTH_HEADER_SCHEME = "Bearer"


def api(path: str) -> str:
    return f"{BASE_URL}{API_PREFIX}{path}"


def divider(char="=", n=70):
    print(char * n)


def section(title):
    print()
    divider()
    print(f"  {title}")
    divider()


def classify_status(code):
    if code is None:
        return "error"
    if 200 <= code < 300:
        return "success"
    if code == 503:
        return "rejected"
    if code in (408, 504):
        return "timeout"
    if 400 <= code < 500:
        return "client_error"
    return "error"


def new_results():
    return defaultdict(lambda: {"outcomes": defaultdict(int), "response_times": []})


def record(results, lock, op_name, status_code, elapsed):
    outcome = classify_status(status_code)
    with lock:
        results[op_name]["outcomes"][outcome] += 1
        results[op_name]["response_times"].append(elapsed)


FIXTURE_PASSWORD = "stress123"


def setup_fixtures(num_products=5, stock_per_product=999999, num_users=100):
    users_creds = []
    for i in range(num_users):
        username = f"stress_user_{i+1}"
        user, _ = User.objects.get_or_create(
            username=username,
            defaults={"email": f"stress{i+1}@test.com"}
        )
        user.set_password(FIXTURE_PASSWORD)
        user.save()
        users_creds.append((username, FIXTURE_PASSWORD))

    products = []
    for i in range(num_products):
        p, _ = Product.objects.get_or_create(
            name=f"StressProduct_{i+1}",
            defaults={"stock": stock_per_product, "price": 50.00 + i * 10}
        )
        Product.objects.filter(id=p.id).update(stock=stock_per_product)
        products.append(p)

    return users_creds, products


_debug_login_printed = threading.Event()


def login_user(session, username, password):
    try:
        r = session.post(
            f"{BASE_URL}{LOGIN_PATH}",
            json={"username": username, "password": password},
            timeout=60
        )
    except requests.exceptions.RequestException as e:
        if not _debug_login_printed.is_set():
            _debug_login_printed.set()
            print(f"\n  [DEBUG] Login connection error: {e}\n")
        return False, None

    if r.status_code != 200:
        if not _debug_login_printed.is_set():
            _debug_login_printed.set()
            print(f"\n  [DEBUG] First login failure — status={r.status_code}")
            print(f"  [DEBUG] Full response: {r.text[:500]}\n")
        return False, r.status_code

    body = r.json()
    token = body.get("token") or body.get("access")
    if token:
        session.headers.update({"Authorization": f"{AUTH_HEADER_SCHEME} {token}"})
        return True, r.status_code
    return False, r.status_code


def op_register(session, username):
    t0 = time.time()
    try:
        r = session.post(api("/register/"), json={
            "username": username,
            "email": f"{username}@test.com",
            "password": FIXTURE_PASSWORD,
        }, timeout=60)
        return r.status_code, time.time() - t0
    except requests.exceptions.RequestException:
        return None, time.time() - t0


def op_browse_products(session):
    t0 = time.time()
    try:
        r = session.get(api("/products/"), timeout=60)
        products = r.json() if r.status_code == 200 else []
        return r.status_code, time.time() - t0, products
    except requests.exceptions.RequestException:
        return None, time.time() - t0, []


def op_view_product(session, product_id):
    t0 = time.time()
    try:
        r = session.get(api(f"/products/{product_id}/"), timeout=60)
        return r.status_code, time.time() - t0
    except requests.exceptions.RequestException:
        return None, time.time() - t0


def op_create_order(session, product_id, quantity=1):
    t0 = time.time()
    try:
        r = session.post(api("/orders/"), json={
            "product_id": product_id, "quantity": quantity
        }, timeout=60)
        order_id = r.json().get("order_id") if r.status_code == 201 else None
        return r.status_code, time.time() - t0, order_id
    except requests.exceptions.RequestException:
        return None, time.time() - t0, None


def op_pay_order(session, order_id):
    t0 = time.time()
    try:
        r = session.post(api(f"/orders/{order_id}/pay/"), timeout=60)
        return r.status_code, time.time() - t0
    except requests.exceptions.RequestException:
        return None, time.time() - t0


def op_complete_order(session, order_id):
    t0 = time.time()
    try:
        r = session.post(api(f"/orders/{order_id}/complete/"), timeout=60)
        return r.status_code, time.time() - t0
    except requests.exceptions.RequestException:
        return None, time.time() - t0


def op_cancel_order(session, order_id):
    t0 = time.time()
    try:
        r = session.post(api(f"/orders/{order_id}/cancel/"), timeout=60)
        return r.status_code, time.time() - t0
    except requests.exceptions.RequestException:
        return None, time.time() - t0


def op_my_orders(session):
    t0 = time.time()
    try:
        r = session.get(api("/orders/"), timeout=60)
        return r.status_code, time.time() - t0
    except requests.exceptions.RequestException:
        return None, time.time() - t0


def simulate_user_journey(username, password, products, results, lock, register_first=False):
    session = requests.Session()
    # session.proxies = {'http': None, 'https': None}

    if register_first:
        status, elapsed = op_register(session, username)
        record(results, lock, "register", status, elapsed)

    t0 = time.time()
    ok, status = login_user(session, username, password)
    record(results, lock, "login", status if ok else (status or 0), time.time() - t0)
    if not ok:
        return

    status, elapsed, product_list = op_browse_products(session)
    record(results, lock, "browse_products", status, elapsed)

    candidates = product_list if product_list else [{"id": p.id} for p in products]
    chosen = random.choice(candidates)
    product_id = chosen.get("id") or chosen.get("pk")

    status, elapsed = op_view_product(session, product_id)
    record(results, lock, "view_product", status, elapsed)

    status, elapsed, order_id = op_create_order(session, product_id, quantity=1)
    record(results, lock, "create_order", status, elapsed)

    if order_id:
        if random.random() < 0.8:
            status, elapsed = op_pay_order(session, order_id)
            record(results, lock, "pay_order", status, elapsed)

            if status == 200:
                status, elapsed = op_complete_order(session, order_id)
                record(results, lock, "complete_order", status, elapsed)
        else:
            status, elapsed = op_cancel_order(session, order_id)
            record(results, lock, "cancel_order", status, elapsed)

    status, elapsed = op_my_orders(session)
    record(results, lock, "my_orders", status, elapsed)


def sample_admin_endpoints(snapshots, stop_event, interval=2):
    session = requests.Session()
    while not stop_event.is_set():
        snap = {"time": time.time()}
        for key, path in [
            ("capacity", "/admin/capacity/"),
            ("resources", "/admin/resources/"),
            ("async_queue", "/admin/async-queue/"),
        ]:
            try:
                r = session.get(f"{BASE_URL}{API_PREFIX}{path}", timeout=5)
                snap[key] = r.json() if r.status_code == 200 else {"error": r.status_code}
            except requests.exceptions.RequestException as e:
                snap[key] = {"error": str(e)}
        snapshots.append(snap)
        time.sleep(interval)


def test_concurrent_spike(users_creds, products, num_requests=100, max_workers=5):
    section("TEST 1 — Concurrent Spike ")
    print(f"  Requests (Full Journeys) : {num_requests}")
    print(f"  Workers                  : {max_workers}")
    print()

    results = new_results()
    lock = threading.Lock()

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = []
        for i in range(num_requests):
            username, password = users_creds[i % len(users_creds)]
            futures.append(pool.submit(
                simulate_user_journey, username, password, products, results, lock
            ))
        for f in as_completed(futures):
            pass
    elapsed = round(time.time() - t0, 2)

    print_per_operation_report(results, elapsed, label="Concurrent Spike")
    return results, elapsed


def test_sustained_load(users_creds, products, duration_sec=30, num_workers=5):
    section("TEST 2 — Sustained Load ")
    print(f"  Duration : {duration_sec}s   |   Workers: {num_workers}")
    print()

    results = new_results()
    lock = threading.Lock()
    stop = threading.Event()

    def worker(wid):
        idx = 0
        while not stop.is_set():
            username, password = users_creds[(wid + idx) % len(users_creds)]
            simulate_user_journey(username, password, products, results, lock)
            idx += 1

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(num_workers)]
    t0 = time.time()
    for t in threads:
        t.start()

    for sec in range(0, duration_sec, 5):
        time.sleep(5)
        total_done = sum(sum(op["outcomes"].values()) for op in results.values())
        print(f"  t+{sec+5:2d}s  |  Total operations so far = {total_done}")

    stop.set()
    for t in threads:
        t.join(timeout=3)
    elapsed = round(time.time() - t0, 2)

    print_per_operation_report(results, elapsed, label="Sustained Load")
    return results, elapsed


def test_recovery(users_creds, products):
    section("TEST 3 — Recovery After Flood ")
    print("  Phase A: Flood 200 full user journeys with max concurrency")
    print("  Phase B: Wait 5 seconds (cooldown)")
    print("  Phase C: 20 normal journeys — expecting near-full success")
    print()

    lock = threading.Lock()

    flood_results = new_results()
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = [
            pool.submit(simulate_user_journey,
                        *users_creds[i % len(users_creds)], products,
                        flood_results, lock)
            for i in range(200)
        ]
        for f in as_completed(futs):
            pass

    flood_total = sum(sum(op["outcomes"].values()) for op in flood_results.values())
    flood_ok = sum(op["outcomes"]["success"] for op in flood_results.values())
    print(f"  [Phase A] Total operations: {flood_total}  |  Succeeded: {flood_ok}")

    print("  [Phase B] Cooldown 5 seconds...")
    time.sleep(5)

    normal_results = new_results()
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = [
            pool.submit(simulate_user_journey,
                        *users_creds[i % len(users_creds)], products,
                        normal_results, lock)
            for i in range(20)
        ]
        for f in as_completed(futs):
            pass
    elapsed = round(time.time() - t0, 2)

    normal_total = sum(sum(op["outcomes"].values()) for op in normal_results.values())
    normal_ok = sum(op["outcomes"]["success"] for op in normal_results.values())
    recovery_rate = round(normal_ok / normal_total * 100, 1) if normal_total else 0
    print(f"  [Phase C] Succeeded {normal_ok}/{normal_total} operations  →  recovery_rate = {recovery_rate}%")

    recovered = recovery_rate >= 90
    print(f"  Verdict: {'PASS — System recovered successfully' if recovered else 'FAIL — System did not recover'}")
    return flood_results, normal_results, recovery_rate


def print_per_operation_report(results, elapsed, label=""):
    print(f"  -- {label} — Per-operation breakdown --")
    print(f"  {'Operation':<18}{'Total':>7}{'OK%':>8}{'503':>7}{'Err':>6}{'Avg(s)':>9}{'P95(s)':>9}")
    print(f"  {'-'*18}{'-'*7}{'-'*8}{'-'*7}{'-'*6}{'-'*9}{'-'*9}")

    worst_op, worst_p95 = None, -1
    for op_name, data in sorted(results.items()):
        outcomes = data["outcomes"]
        times = data["response_times"]
        total = sum(outcomes.values())
        ok_pct = round(outcomes["success"] / total * 100, 1) if total else 0
        rejected = outcomes["rejected"]
        errs = outcomes.get("error", 0) + outcomes.get("client_error", 0) + outcomes.get("timeout", 0)
        avg_t = round(statistics.mean(times), 3) if times else 0
        p95_t = round(sorted(times)[int(len(times) * 0.95)], 3) if times else 0

        if p95_t > worst_p95:
            worst_p95, worst_op = p95_t, op_name

        print(f"  {op_name:<18}{total:>7}{ok_pct:>7}%{rejected:>7}{errs:>6}{avg_t:>9}{p95_t:>9}")

    print()
    print(f"  Elapsed: {elapsed}s")
    if worst_op:
        print(f"  Slowest operation (p95): {worst_op}  ({worst_p95}s)  → Candidate for bottleneck analysis (Req 10)")
    print()


if __name__ == "__main__":
    section("Full-System Stress Test — Initializing")
    print(f"  Target server: {BASE_URL}{API_PREFIX}")
    print("  Make sure the server is running (python manage.py runserver) before continuing")
    print()

    print("  Setting up fixtures (100 users + 5 products)  .......")
    users_creds, products = setup_fixtures(num_products=5, num_users=100)
    print(f"  Users    : {len(users_creds)}")
    print(f"  Products : {[p.name for p in products]}")

    snapshots = []
    stop_sampler = threading.Event()
    sampler_thread = threading.Thread(
        target=sample_admin_endpoints, args=(snapshots, stop_sampler), daemon=True
    )
    sampler_thread.start()

    spike_res, spike_t = test_concurrent_spike(users_creds, products, num_requests=100, max_workers=5)
    sustained_res, sustained_t = test_sustained_load(users_creds, products, duration_sec=30, num_workers=5)
    #flood_res, normal_res, recovery_rate = test_recovery(users_creds, products)

    stop_sampler.set()
    sampler_thread.join(timeout=3)

    section("FINAL REPORT")
    print(f"  Collected monitoring samples from admin endpoints: {len(snapshots)}")
    print(f"  (Check snapshots for capacity/resources/async_queue over time)")
    print(f"  Tested at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    divider()
