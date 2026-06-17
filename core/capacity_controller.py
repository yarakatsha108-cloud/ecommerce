"""
Capacity Controller - التحكم في الطاقة الاستيعابية والعمليات المتوازية
"""

import threading
import time
import queue
from typing import Callable, Any, Optional
from concurrent.futures import Future
from enum import Enum
import logging
from functools import wraps

from .resource_manager import get_monitor

logger = logging.getLogger(__name__)


class ThreadPoolFullError(Exception):
    """استثناء عند امتلاء الـ thread pool"""
    pass


class CapacityLevel(Enum):
    """مستويات الطاقة الاستيعابية"""
    OPTIMAL  = "optimal"    # < 50% resources
    NORMAL   = "normal"     # 50-70% resources
    HIGH     = "high"       # 70-85% resources
    CRITICAL = "critical"   # > 85% resources


class CapacityController:
    """التحكم في الطاقة الاستيعابية والعمليات المتوازية"""

    def __init__(self,
                 max_concurrent_operations: int = 50,
                 max_queue_size: int = 1000):
        self.max_concurrent   = max_concurrent_operations
        self.max_queue_size   = max_queue_size
        self.active_operations = 0
        self.pending_queue    = queue.Queue(maxsize=max_queue_size)
        self.lock             = threading.RLock()
        self.semaphore        = threading.Semaphore(max_concurrent_operations)
        self.operation_counter = 0
        self.rejected_count   = 0
        self.queued_count     = 0

        # Internal thread pool for submit()
        self._executor_lock   = threading.Lock()
        self._futures_queue: queue.Queue = queue.Queue(maxsize=max_queue_size)

    # ── Capacity level ─────────────────────────────────────────────────────

    def get_capacity_level(self) -> CapacityLevel:
        """تحديد مستوى الطاقة الحالي"""
        metrics = get_monitor().get_metrics()
        if metrics.cpu_percent > 85 or metrics.memory_percent > 85:
            return CapacityLevel.CRITICAL
        elif metrics.cpu_percent > 70 or metrics.memory_percent > 70:
            return CapacityLevel.HIGH
        elif metrics.cpu_percent > 50 or metrics.memory_percent > 50:
            return CapacityLevel.NORMAL
        else:
            return CapacityLevel.OPTIMAL

    def get_max_concurrent_allowed(self) -> int:
        """الحصول على الحد الأقصى المسموح للعمليات المتوازية بناءً على الموارد"""
        level    = self.get_capacity_level()
        base_max = self.max_concurrent
        if level == CapacityLevel.CRITICAL:
            return max(1, int(base_max * 0.3))
        elif level == CapacityLevel.HIGH:
            return max(5, int(base_max * 0.6))
        elif level == CapacityLevel.NORMAL:
            return max(10, int(base_max * 0.8))
        else:
            return base_max

    def can_accept_operation(self) -> bool:
        """التحقق من إمكانية قبول عملية جديدة"""
        level = self.get_capacity_level()
        if level == CapacityLevel.CRITICAL:
            return False
        allowed_concurrent = self.get_max_concurrent_allowed()
        return self.active_operations < allowed_concurrent

    # ── Semaphore acquire / release ────────────────────────────────────────

    def acquire(self, timeout: Optional[float] = 5.0) -> bool:
        """الحصول على حق تنفيذ عملية"""
        level = self.get_capacity_level()
        if level == CapacityLevel.CRITICAL:
            if not self.semaphore.acquire(blocking=False):
                self.rejected_count += 1
                return False
        else:
            if not self.semaphore.acquire(timeout=timeout):
                self.rejected_count += 1
                return False
        with self.lock:
            self.active_operations  += 1
            self.operation_counter  += 1
        return True

    def release(self):
        """تحرير حق تنفيذ العملية"""
        with self.lock:
            self.active_operations = max(0, self.active_operations - 1)
        self.semaphore.release()

    # ── submit() — used by simulate_order ─────────────────────────────────

    def submit(self, fn: Callable, *args, block: bool = True, **kwargs) -> Future:
        """
        Submit a callable to run under capacity control.

        Parameters
        ----------
        fn    : callable to execute
        *args : positional arguments forwarded to fn
        block : if False, raises ThreadPoolFullError immediately when the
                system cannot accept the operation instead of waiting.

        Returns
        -------
        concurrent.futures.Future  — call .result(timeout=N) to get the value.

        Raises
        ------
        ThreadPoolFullError  when block=False and capacity is exhausted.
        """
        timeout = 0 if not block else 5.0

        # Check capacity
        if not block and not self.can_accept_operation():
            with self.lock:
                self.rejected_count += 1
            raise ThreadPoolFullError("Capacity exhausted — request rejected (503)")

        future: Future = Future()

        def _run():
            if not self.acquire(timeout=timeout):
                with self.lock:
                    self.rejected_count += 1
                future.set_exception(
                    ThreadPoolFullError("Capacity exhausted — request rejected (503)")
                )
                return
            try:
                result = fn(*args, **kwargs)
                future.set_result(result)
            except Exception as exc:
                future.set_exception(exc)
            finally:
                self.release()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return future

    # ── Legacy queue helpers ───────────────────────────────────────────────

    def queue_operation(self, operation_id: str, data: dict) -> bool:
        """إضافة عملية إلى قائمة الانتظار"""
        try:
            self.pending_queue.put_nowait({
                'id'       : operation_id,
                'data'     : data,
                'timestamp': time.time()
            })
            self.queued_count += 1
            return True
        except queue.Full:
            logger.warning(f"Queue is full. Cannot queue operation {operation_id}")
            return False

    def get_pending_operation(self, timeout: float = 1.0) -> Optional[dict]:
        """الحصول على عملية من قائمة الانتظار"""
        try:
            return self.pending_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # ── Status ────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        pending = self.pending_queue.qsize()
        return {
            'capacity_level'        : self.get_capacity_level().value,
            'active_operations'     : self.active_operations,
            'max_concurrent_allowed': self.get_max_concurrent_allowed(),
            'total_processed'       : self.operation_counter,
            'rejected_count'        : self.rejected_count,
            'queued_count'          : self.queued_count,
            'queue_size'            : pending,
            'pending_queue_size'    : pending,
            'queue_max_size'        : self.max_queue_size,
            # Keys للـ middleware
            'active_workers'        : self.active_operations,
            'max_workers'           : self.max_concurrent,
            'max_queue_size'        : self.max_queue_size,
        }

# ── Decorator ─────────────────────────────────────────────────────────────────

class CapacityDecorator:
    """ديكوريتور للتحكم في الطاقة الاستيعابية"""

    def __init__(self, controller: Optional[CapacityController] = None):
        self.controller = controller or _default_controller

    def __call__(self, func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            operation_id = f"{func.__name__}_{id(args)}"
            if not self.controller.acquire(timeout=30):
                logger.error(f"Operation {operation_id} rejected due to capacity limits")
                raise CapacityExceededError(
                    "System capacity exceeded. Please try again later."
                )
            try:
                return func(*args, **kwargs)
            finally:
                self.controller.release()
        return wrapper


class CapacityExceededError(Exception):
    """استثناء عند تجاوز السعة الاستيعابية"""
    pass


# ── Global singleton ──────────────────────────────────────────────────────────

_default_controller = CapacityController(
    max_concurrent_operations=50,
    max_queue_size=1000
)

def get_capacity_controller() -> CapacityController:
    """الحصول على مثيل التحكم في السعة العام"""
    return _default_controller
