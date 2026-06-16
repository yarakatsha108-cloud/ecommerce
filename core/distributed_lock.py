import threading
import time
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class LockAcquisitionError(Exception):
    pass


# ✅ Distributed Lock في الذاكرة بدل DB
# يمنع Race Condition بين الـ threads داخل نفس الـ process
_locks = {}
_locks_mutex = threading.Lock()


class DistributedLock:
    def __init__(self, resource_name, timeout=10.0, retry_interval=0.05):
        self.resource_name = resource_name
        self.timeout = timeout
        self.retry_interval = retry_interval
        self._acquired = False

    def acquire(self):
        deadline = time.monotonic() + self.timeout
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            with _locks_mutex:
                if self.resource_name not in _locks:
                    _locks[self.resource_name] = threading.Lock()
                lock = _locks[self.resource_name]

            if lock.acquire(blocking=False):
                self._acquired = True
                logger.debug(f"[DistributedLock] ✅ قفل: {self.resource_name} | attempt={attempt}")
                return True

            logger.debug(f"[DistributedLock] ⏳ انتظار: {self.resource_name} | attempt={attempt}")
            time.sleep(self.retry_interval)

        raise LockAcquisitionError(
            f"فشل الحصول على القفل '{self.resource_name}' خلال {self.timeout}ث"
        )

    def release(self):
        if not self._acquired:
            return
        with _locks_mutex:
            lock = _locks.get(self.resource_name)
            if lock:
                try:
                    lock.release()
                    logger.debug(f"[DistributedLock] 🔓 تحرير: {self.resource_name}")
                except RuntimeError:
                    pass
        self._acquired = False


@contextmanager
def acquire_product_lock(product_id: int, timeout: float = 10.0):
    lock = DistributedLock(
        resource_name=f"product_lock_{product_id}",
        timeout=timeout,
        retry_interval=0.05,
    )
    lock.acquire()
    try:
        logger.info(f"[ProductLock] 🔒 قفل المنتج #{product_id}")
        yield lock
    finally:
        lock.release()
        logger.info(f"[ProductLock] 🔓 تحرير المنتج #{product_id}")