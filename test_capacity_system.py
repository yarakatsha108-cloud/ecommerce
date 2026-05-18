#!/usr/bin/env python
"""
Comprehensive test for Resource Management & Capacity Control System
Modified to work with AdvancedThreadPoolController (no resource_manager required)
"""

import threading
import requests
import time
from concurrent.futures import ThreadPoolExecutor
import statistics

# Test configuration
URL_BASE = "http://127.0.0.1:8000"
API_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzc5MDc5ODYwLCJpYXQiOjE3NzkwNzYyNjAsImp0aSI6ImJjNzkwZTAzZjYwYTQ3Njg5NmQ3MWM0NjZjOTkzMmY1IiwidXNlcl9pZCI6IjMifQ.j4yVZzl-y799M5ulsHe8Bn5969SsDqEnAgE2Ml9YBRo"

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}",
    "Content-Type": "application/json"
}

class CapacityTester:
    def __init__(self):
        self.results = {
            'successful': 0,
            'rejected': 0,
            'timeout': 0,
            'error': 0,
            'response_times': [],
            'status_codes': {}
        }
        self.lock = threading.Lock()

    def get_capacity_status(self):
        """Fetch the current capacity status from /api/admin/capacity/"""
        try:
            response = requests.get(
                f"{URL_BASE}/api/admin/capacity/",
                headers=HEADERS,
                timeout=10
            )
            data = response.json()
            if 'data' in data:
                return data['data']
            return None
        except Exception as e:
            print(f"Error fetching capacity status: {e}")
            return None

    def make_order_request(self, product_id=4, quantity=1):
        """Send a sample order request to /api/orders/"""
        start_time = time.time()
        try:
            response = requests.post(
                f"{URL_BASE}/api/orders/",
                json={"product_id": product_id, "quantity": quantity},
                headers=HEADERS,
                timeout=30,
                proxies={"http": None, "https": None}
            )
            response_time = time.time() - start_time
            status_code = response.status_code

            with self.lock:
                self.results['response_times'].append(response_time)
                self.results['status_codes'][status_code] = \
                    self.results['status_codes'].get(status_code, 0) + 1

                if 200 <= status_code < 300:
                    self.results['successful'] += 1
                elif status_code in (429, 503):
                    self.results['rejected'] += 1
                else:
                    self.results['error'] += 1

            return status_code

        except requests.Timeout:
            with self.lock:
                self.results['timeout'] += 1
        except Exception as e:
            with self.lock:
                self.results['error'] += 1
            print(f"Error: {e}")

    def run_concurrent_test(self, num_requests=100, max_workers=20):
        print(f"{'='*60}")
        print(f" Concurrent test: {num_requests} requests")
        print(f"{'='*60}")

        print("Initial capacity status:")
        self._print_capacity_status()

        print(f"  Sending {num_requests} requests with {max_workers} concurrent workers...")

        start_time = time.time()
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self.make_order_request, 4, 1) for _ in range(num_requests)]
            for i, future in enumerate(futures):
                try:
                    future.result(timeout=35)
                except Exception:
                    pass
                if (i + 1) % 20 == 0:
                    print(f"  ✓ Processed {i + 1}/{num_requests} requests")

        total_time = time.time() - start_time

        print(f"{'='*60}")
        print(" Test results:")
        print(f"{'='*60}")
        self._print_results(total_time)

    def stress_test(self, duration=30, num_workers=50):
        print(f"{'='*60}")
        print(f" Stress test: {duration} seconds with {num_workers} workers")
        print(f"{'='*60}")

        print(" Initial capacity status:")
        self._print_capacity_status()

        print(f" Sending continuous requests for {duration} seconds...")

        start_time = time.time()
        stop_flag = threading.Event()

        def worker():
            while not stop_flag.is_set():
                self.make_order_request(4, 1)
                time.sleep(0.1)

        threads = []
        for _ in range(num_workers):
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            threads.append(t)

        elapsed = 0
        while elapsed < duration:
            time.sleep(5)
            elapsed = time.time() - start_time
            print(f"   {elapsed:.0f}/{duration}s - successful: {self.results['successful']}, rejected: {self.results['rejected']}")

        stop_flag.set()
        total_time = time.time() - start_time

        print(f"{'='*60}")
        print(" Stress test results:")
        print(f"{'='*60}")
        self._print_results(total_time)

    def _print_capacity_status(self):
        """Print only capacity status (no resource_manager)"""
        cap = self.get_capacity_status()
        if cap:
            print(f"   Active workers: {cap.get('active_workers', '?')}/{cap.get('max_workers', '?')}")
            print(f"   Queue size: {cap.get('queue_size', '?')}/{cap.get('max_queue_size', '?')}")
            print(f"   Total rejected: {cap.get('rejected_count', '?')}")
            print(f"   Completed: {cap.get('completed_count', '?')}")
            # Calculate capacity level manually
            workers_ratio = cap.get('active_workers', 0) / max(1, cap.get('max_workers', 1))
            queue_ratio = cap.get('queue_size', 0) / max(1, cap.get('max_queue_size', 1))
            level = "HIGH" if max(workers_ratio, queue_ratio) > 0.7 else "NORMAL"
            print(f"   Capacity Level: {level}")
        else:
            print("  Could not fetch capacity status.")

    def _print_results(self, total_time):
        total = sum([
            self.results['successful'],
            self.results['rejected'],
            self.results['timeout'],
            self.results['error']
        ])

        print(f"  Successful requests: {self.results['successful']}")
        print(f"  Rejected requests: {self.results['rejected']}")
        print(f"  Timeout requests: {self.results['timeout']}")
        print(f"  Error requests: {self.results['error']}")
        print(f"  Total requests: {total}")

        print(f"\n  Total elapsed time: {total_time:.2f} seconds")
        if total_time > 0:
            print(f"  Request rate: {total/total_time:.2f} req/sec")

        if self.results['response_times']:
            print(f"\n  Response times:")
            print(f"  - Fastest: {min(self.results['response_times']):.3f}s")
            print(f"  - Slowest: {max(self.results['response_times']):.3f}s")
            print(f"  - Average: {statistics.mean(self.results['response_times']):.3f}s")
            if len(self.results['response_times']) > 1:
                print(f"  - Stddev: {statistics.stdev(self.results['response_times']):.3f}s")

        print(f"\n Status code distribution:")
        for code, count in sorted(self.results['status_codes'].items()):
            percentage = (count / total * 100) if total > 0 else 0
            print(f"  {code}: {count} ({percentage:.1f}%)")

        print(f"\n Final capacity status:")
        self._print_capacity_status()

        print(f"\n  Recommendations:")
        success_rate = (self.results['successful'] / total * 100) if total > 0 else 0
        if success_rate > 95:
            print(f"   Excellent performance - success rate: {success_rate:.1f}%")
        elif success_rate > 80:
            print(f"    Good performance - success rate: {success_rate:.1f}%")
        else:
            print(f"    Poor performance - success rate: {success_rate:.1f}%")
            print(f"     Consider increasing capacity or reducing load")

def main():
    tester = CapacityTester()
    print( "="*60)
    print(" Resource Management & Capacity Control Test (HTTP)")
    print("="*60)
    print("Choose a test option:")
    print("  1  Concurrent test (5 requests, low concurrency)")
    print("  2  Stress test (30 seconds, 50 workers)")
    print("  3  Display current capacity status")
    print("  4  Run all tests")

    choice = input("Your choice (1-4): ").strip()

    if choice == "1":
        tester.run_concurrent_test(num_requests=5, max_workers=2)
    elif choice == "2":
        tester.stress_test(duration=30, num_workers=50)
    elif choice == "3":
        print(" Current capacity status:")
        tester._print_capacity_status()
    elif choice == "4":
        tester.run_concurrent_test(num_requests=50, max_workers=10)
        time.sleep(5)
        tester = CapacityTester()
        tester.stress_test(duration=20, num_workers=30)
    else:
        print("Invalid choice")

if __name__ == "__main__":
    main()