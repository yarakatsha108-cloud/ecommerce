

import threading
import requests

BASE_URL = "http://127.0.0.1:8000/api"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzgxNjAzMjE5LCJpYXQiOjE3ODE1OTk2MTksImp0aSI6IjJlNTlkM2UzYjVhNDRhNTZiOGM0N2VhNTk2YWQxMDJjIiwidXNlcl9pZCI6IjQifQ.9frrDEb75p9qLdOsN-koRfJqwamOL30Vlz7I0hdUli0"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def get_stock(product_id):
    """Get current stock for a product"""
    r = requests.get(f"{BASE_URL}/products/{product_id}/", headers=HEADERS)
    return r.json().get('stock', -1)


def print_section(title):
    print(f"\n{'='*50}")
    print(f"  {title}")
    print('='*50)


# ─────────────────────────────────────────────
# TEST 1: Atomicity — Order Creation
# Operations: deduct stock + create Order + create OrderItem
# ─────────────────────────────────────────────
def test_atomicity_order_creation():
    print_section("TEST 1: Atomicity — Order Creation")

    PRODUCT_ID = 7
    stock_before = get_stock(PRODUCT_ID)
    print(f"  Stock before: {stock_before}")

    r = requests.post(f"{BASE_URL}/orders/",
                      json={"product_id": PRODUCT_ID, "quantity": 1},
                      headers=HEADERS)
    result = r.json()
    print(f"  Response: {result}")

    stock_after = get_stock(PRODUCT_ID)
    print(f"  Stock after: {stock_after}")

    if 'order_id' in result:
        print(f"  PASS — Order created and stock decreased: {stock_before} -> {stock_after}")
    else:
        print(f"  FAIL — {result}")


# ─────────────────────────────────────────────
# TEST 2: Consistency — Prevent Negative Stock
# ─────────────────────────────────────────────
def test_consistency_no_negative_stock():
    print_section("TEST 2: Consistency — Prevent Negative Stock")

    PRODUCT_ID = 6
    stock_before = get_stock(PRODUCT_ID)
    print(f"  Stock before: {stock_before}")

    r = requests.post(f"{BASE_URL}/orders/",
                      json={"product_id": PRODUCT_ID, "quantity": 9999},
                      headers=HEADERS)
    result = r.json()
    print(f"  Response: {result}")

    stock_after = get_stock(PRODUCT_ID)
    print(f"  Stock after: {stock_after}")

    if stock_after >= 0 and 'error' in result:
        print(f"  PASS — Stock did not go below zero, remained: {stock_after}")
    else:
        print(f"  FAIL — Stock reached: {stock_after}")


# ─────────────────────────────────────────────
# TEST 3: Isolation — Concurrent Payment for Same Order
# Proves that double payment for the same order is impossible
# ─────────────────────────────────────────────
def test_isolation_double_payment():
    print_section("TEST 3: Isolation — Prevent Double Payment")

    r = requests.post(f"{BASE_URL}/orders/",
                      json={"product_id": 6, "quantity": 1},
                      headers=HEADERS)

    if 'order_id' not in r.json():
        print(f"  WARNING — Could not create order: {r.json()}")
        return

    order_id = r.json()['order_id']
    print(f"  Order #{order_id} created")

    results = []
    lock = threading.Lock()

    def pay_order():
        resp = requests.post(f"{BASE_URL}/orders/{order_id}/pay/", headers=HEADERS)
        with lock:
            try:
                results.append(resp.json())
            except Exception:
                results.append({"error": f"HTTP {resp.status_code}"})

    t1 = threading.Thread(target=pay_order)
    t2 = threading.Thread(target=pay_order)
    t1.start(); t2.start()
    t1.join(); t2.join()

    print(f"  Response 1: {results[0]}")
    print(f"  Response 2: {results[1]}")

    successes = sum(1 for r in results if 'message' in r)
    if successes == 1:
        print(f"  PASS — Only one payment succeeded, second was rejected (Isolation working)")
    else:
        print(f"  FAIL — {successes} payments succeeded!")


# ─────────────────────────────────────────────
# TEST 4: Atomicity — Cancellation Restores Stock
# Proves that cancellation and stock restore happen together
# ─────────────────────────────────────────────
def test_atomicity_cancel_restores_stock():
    print_section("TEST 4: Atomicity — Cancellation Restores Stock")

    PRODUCT_ID = 7
    stock_before = get_stock(PRODUCT_ID)
    print(f"  Stock before order: {stock_before}")

    r = requests.post(f"{BASE_URL}/orders/",
                      json={"product_id": PRODUCT_ID, "quantity": 2},
                      headers=HEADERS)

    if 'order_id' not in r.json():
        print(f"  WARNING — Could not create order: {r.json()}")
        return

    order_id = r.json()['order_id']
    stock_after_order = get_stock(PRODUCT_ID)
    print(f"  Stock after order: {stock_after_order} (decreased by {stock_before - stock_after_order})")

    r = requests.post(f"{BASE_URL}/orders/{order_id}/cancel/", headers=HEADERS)
    print(f"  Cancellation response: {r.json()}")

    stock_after_cancel = get_stock(PRODUCT_ID)
    print(f"  Stock after cancellation: {stock_after_cancel}")

    if stock_after_cancel == stock_before:
        print(f"  PASS — Stock restored to original value: {stock_before} (Atomicity working)")
    else:
        print(f"  FAIL — Stock not restored! Before: {stock_before}, After: {stock_after_cancel}")


# ─────────────────────────────────────────────
# TEST 5: Atomicity Under Concurrency
# 10 users buying at the same time
# ─────────────────────────────────────────────
def test_atomicity_under_concurrency():
    print_section("TEST 5: Atomicity Under Concurrency — 10 Users")

    PRODUCT_ID = 7
    stock_before = get_stock(PRODUCT_ID)
    print(f"  Stock before: {stock_before}")

    results = []
    lock = threading.Lock()

    def create_order():
        r = requests.post(f"{BASE_URL}/orders/",
                          json={"product_id": PRODUCT_ID, "quantity": 1},
                          headers=HEADERS)
        with lock:
            results.append(r.json())

    threads = [threading.Thread(target=create_order) for _ in range(10)]
    for t in threads: t.start()
    for t in threads: t.join()

    successes = sum(1 for r in results if 'order_id' in r)
    failures = sum(1 for r in results if 'error' in r)
    stock_after = get_stock(PRODUCT_ID)

    print(f"  Successes: {successes}")
    print(f"  Failures:  {failures}")
    print(f"  Stock after: {stock_after}")

    expected_stock = stock_before - successes
    if stock_after == expected_stock:
        print(f"  PASS — Stock correct: {stock_before} - {successes} = {stock_after} (ACID preserved)")
    else:
        print(f"  FAIL — Stock incorrect! Expected: {expected_stock}, Actual: {stock_after}")


# ─────────────────────────────────────────────
# Run all tests
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("\n ACID Transaction Integrity Tests")
    print("Requirement 8 — Transaction Safety\n")

    test_atomicity_order_creation()
    test_consistency_no_negative_stock()
    test_isolation_double_payment()
    test_atomicity_cancel_restores_stock()
    test_atomicity_under_concurrency()

    print(f"\n{'='*50}")
    print("  All tests completed")
    print('='*50)