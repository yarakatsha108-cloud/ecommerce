
import os
import sys
import django
import time
import psutil
from datetime import datetime, timedelta, timezone as dt_timezone
from django.utils import timezone

# Django setup
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ecommerce.settings')
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from django.contrib.auth.models import User
from core.models import Product, Order, OrderItem, DailySalesReport
from core.batch_processor import BatchProcessor



# Helper: System Monitor for CPU and Memory usage

class SystemMonitor:
    "Monitors CPU and Memory usage before and after each batch run"
    #لمراقبة عمل المعالج والذاكرة قبل وبعد كل تشغيل للمعالجة
    #snapshot() : لالتقاط لقطة من استخدام المعالج والذاكرة الحالية
    @staticmethod
    def snapshot() -> dict:
        "Take a snapshot of current CPU and RAM usage"
        return {
            'cpu_percent' : psutil.cpu_percent(interval=0.5),
            'ram_percent' : psutil.virtual_memory().percent,
            # تحويل الاستخدام من بايت إلى ميجابايت مع تقريب لأقرب 2 منزلة عشرية لسهولة القراءة
            'ram_used_mb' : round(psutil.virtual_memory().used / (1024 ** 2), 2),
            'ram_total_mb': round(psutil.virtual_memory().total / (1024 ** 2), 2),
        }
    #يطبع لقطة من الاستخدام الحالي للمعالج والذاكرة بشكل منسق
    @staticmethod
    def print_snapshot(label: str, snap: dict):
        "Print a formatted snapshot"
        print(f"   [{label}]")
        print(f"      CPU  : {snap['cpu_percent']}%")
        print(f"      RAM  : {snap['ram_percent']}%  "
              f"({snap['ram_used_mb']} MB / {snap['ram_total_mb']} MB)")
    # للمقارنة 
    @staticmethod
    def print_diff(before: dict, after: dict):
        "Print the difference between two snapshots"
        cpu_diff = round(after['cpu_percent'] - before['cpu_percent'], 2)
        ram_diff = round(after['ram_used_mb'] - before['ram_used_mb'], 2)
        cpu_sign = "+" if cpu_diff >= 0 else ""
        ram_sign = "+" if ram_diff >= 0 else ""
        print(f"   [Delta]")
        print(f"      CPU change : {cpu_sign}{cpu_diff}%")
        print(f"      RAM change : {ram_sign}{ram_diff} MB")


# Main Tester 

class BatchProcessingTester:
    "Full test suite for the Batch Processing system"

    def __init__(self):
        self.test_results = []
        self.monitor = SystemMonitor()
        self.today = datetime.now().date()

    # Test Data
    # نقوم بإنشاء بيانات اختبارية من الطلبات والمنتجات والمستخدمين لتاريخ اليوم حتى يتمكن المعالج من العثور عليها ومعالجتها
    def create_test_data(self, num_orders: int = 10000):
        print("Creating test data...")

        # تنظيف البيانات القديمة أولاً
        start = timezone.make_aware(
            datetime.combine(self.today, datetime.min.time()),
            timezone=dt_timezone.utc
        )

        end = start + timedelta(days=1)

        OrderItem.objects.filter(
            order__created_at__gte=start,
            order__created_at__lt=end
        ).delete()

        Order.objects.filter(
            created_at__gte=start,
            created_at__lt=end
        ).delete()

        DailySalesReport.objects.filter(date=self.today).delete()

        # Test user
        user, _ = User.objects.get_or_create(
            username='testuser',
            defaults={'email': 'test@example.com'}
        )

        # Test products
        products = []
        for i in range(5):
            product, _ = Product.objects.get_or_create(
                name=f'Test Product {i + 1}',
                defaults={
                    'stock': 1000,
                    'price': 100.00 + (i * 50),
                }
            )
            products.append(product)

        # إنشاء الطلبات
        for i in range(num_orders):
            order = Order.objects.create(
                user=user,
                status=['COMPLETED', 'PENDING', 'CANCELLED'][i % 3],
            )

            intended_created_at = timezone.make_aware(
                datetime.combine(self.today, datetime.min.time()),
                timezone=dt_timezone.utc
            )

            Order.objects.filter(id=order.id).update(
                created_at=intended_created_at
            )

            OrderItem.objects.create(
                order=order,
                product=products[i % len(products)],
                quantity=(i % 10) + 1,
            )

        print(f"Created {num_orders} test orders for date: {self.today}")

        return products        

    # Test 1: Basic Batch Processor

    def test_batch_processor(self, batch_size: int = 50):
        "Test the BatchProcessor with CPU and RAM monitoring"
        print("\n" + "=" * 60)
        print("TEST 1: Batch Processor")
        print("=" * 60)
        print(f"Date       : {self.today}")
        print(f"Batch size : {batch_size}")
        print()

        # Snapshot before
        before = self.monitor.snapshot()
        self.monitor.print_snapshot("Before", before)
        print()

        # Run processor
        start_time = time.time()
        processor = BatchProcessor(batch_size=batch_size)
        report = processor.process_daily_sales(self.today)
        end_time = time.time()

        # Snapshot after
        after = self.monitor.snapshot()
        self.monitor.print_snapshot("After", after)
        print()
        self.monitor.print_diff(before, after)

        # Results
        print()
        print("Results:")
        print(f"   Total orders       : {report.total_orders}")
        print(f"   Completed orders   : {report.completed_orders}")
        print(f"   Cancelled orders   : {report.cancelled_orders}")
        print(f"   Pending orders     : {report.pending_orders}")
        print(f"   Total revenue      : {report.total_revenue}")
        print(f"   Avg order value    : {report.average_order_value}")
        print(f"   Total items sold   : {report.total_items_sold}")
        print(f"   Unique customers   : {report.unique_customers}")
        print(f"   Chunks processed   : {report.chunks_processed}")
        print(f"   Processing time    : {report.processing_time_seconds:.2f} sec")
        print(f"   Status             : {report.status}")

        stats = {
            'total_time'   : round(end_time - start_time, 2),
            'total_orders' : report.total_orders,
            'chunks'       : report.chunks_processed,
            'cpu_before'   : before['cpu_percent'],
            'cpu_after'    : after['cpu_percent'],
            'ram_before_mb': before['ram_used_mb'],
            'ram_after_mb' : after['ram_used_mb'],
        }
        self.test_results.append(('Batch Processor', stats))
        return report

    # Test 2: Performance Comparison

    def test_performance_comparison(self):
        "Compare performance across different batch sizes with resource monitoring"
        print("\n" + "=" * 60)
        print("TEST 2: Performance Comparison (different batch sizes)")
        print("=" * 60)

        batch_sizes = [10, 50, 100, 200]
        results = {}

        for batch_size in batch_sizes:
            # Clean previous report for this date
            DailySalesReport.objects.filter(date=self.today).delete()

            print(f"\nBatch size = {batch_size}:")

            # Snapshot before
            before = self.monitor.snapshot()
            self.monitor.print_snapshot("Before", before)

            # Run processor
            start = time.time()
            processor = BatchProcessor(batch_size=batch_size)
            report = processor.process_daily_sales(self.today)
            end = time.time()

            # Snapshot after
            after = self.monitor.snapshot()
            self.monitor.print_snapshot("After", after)
            self.monitor.print_diff(before, after)

            duration = round(end - start, 2)
            results[batch_size] = {
                'time'     : duration,
                'chunks'   : report.chunks_processed,
                'orders'   : report.total_orders,
                'cpu_delta': round(after['cpu_percent'] - before['cpu_percent'], 2),
                'ram_delta': round(after['ram_used_mb'] - before['ram_used_mb'], 2),
            }

            print(f"   Time     : {duration:.2f} sec")
            print(f"   Orders   : {report.total_orders}")
            print(f"   Chunks   : {report.chunks_processed}")

        # Summary table
        print("\n")
        print("Performance Summary:")
        print("┌─────────────┬────────────┬──────────┬───────────┬────────────┐")
        print("│  Batch Size │  Time (s)  │  Chunks  │ CPU Delta │  RAM Delta │")
        print("├─────────────┼────────────┼──────────┼───────────┼────────────┤")
        for bs, r in results.items():
            cpu_sign = "+" if r['cpu_delta'] >= 0 else ""
            ram_sign = "+" if r['ram_delta'] >= 0 else ""
            print(
                f"│  {bs:^11} │  {r['time']:^8.2f}  │  {r['chunks']:^6}  │"
                f"  {cpu_sign}{r['cpu_delta']:^5}%   │  {ram_sign}{r['ram_delta']:^6} MB  │"
            )
        print("└─────────────┴────────────┴──────────┴───────────┴────────────┘")

        self.test_results.append(('Performance Comparison', results))

    # Test 3: API Endpoints

    def test_api_endpoints(self):
        """Print available API endpoints for manual testing"""
        print("\n" + "=" * 60)
        print("TEST 3: API Endpoints (manual testing)")
        print("=" * 60)
        print(f"""
  Use curl or Postman to test these endpoints:

  1) GET all reports:
     GET /api/reports/

  2) GET specific report:
     GET /api/reports/{self.today}/

  3) Process sales for a date:
     POST /api/reports/process/
     Body: {{"date": "{self.today}", "batch_size": 100}}

  4) Process yesterday's sales:
     POST /api/reports/process-last-day/

  5) Get 30-day stats:
     GET /api/reports/stats/
        """)
        self.test_results.append(('API Endpoints', {'status': 'manual_test_required'}))

    # Summary

    def print_summary(self):
        "Print a full summary of all test results"
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)

        for test_name, result in self.test_results:
            print(f"\n[{test_name}]")
            if isinstance(result, dict):
                for key, value in result.items():
                    if isinstance(value, float):
                        print(f"   {key}: {value:.2f}")
                    elif isinstance(value, dict):
                        print(f"   {key}:")
                        for k2, v2 in value.items():
                            print(f"      {k2}: {v2}")
                    else:
                        print(f"   {key}: {value}")

    # Run All

    def run_all_tests(self):
        "Run all tests in order"
        print("\n")
        print("=" * 60)
        print("Batch Processing System — Full Test Suite")
        print("=" * 60)

        # System info at start
        print("\nSystem Info:")
        snap = self.monitor.snapshot()
        print(f"   CPU  : {snap['cpu_percent']}%")
        print(f"   RAM  : {snap['ram_percent']}%  "
              f"({snap['ram_used_mb']} MB / {snap['ram_total_mb']} MB)")
        print(f"   Date : {self.today}")

        # Create test data
        print()
        self.create_test_data(num_orders=1000)

        # Run tests
        self.test_batch_processor(batch_size=50)
        self.test_performance_comparison()
        self.test_api_endpoints()

        # Summary
        self.print_summary()

        print("All tests completed!")


# Entry Point

if __name__ == '__main__':
    tester = BatchProcessingTester()
    tester.run_all_tests()