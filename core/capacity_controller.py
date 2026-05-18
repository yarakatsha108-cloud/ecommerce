import threading
import queue
import logging
import os
from concurrent.futures import Future
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class ThreadPoolFullError(Exception):
    """Raised when the thread pool queue is full."""
    pass


class AdvancedThreadPoolController:
    """Manage execution using a fixed-size thread pool and bounded task queue."""

    def __init__(self,
                    max_workers: int = None,
                    max_queue_size: int = None,
                    worker_name_prefix: str = 'threadpool-worker'):

        self.max_workers = max_workers or int(os.getenv('THREADPOOL_MAX_WORKERS', 20))
        self.max_queue_size = max_queue_size or int(os.getenv('THREADPOOL_MAX_QUEUE_SIZE', 100))
        
        self.task_queue = queue.Queue(maxsize=self.max_queue_size)
        self.active_workers = 0
        self.total_submitted = 0
        self.rejected_count = 0
        self.completed_count = 0
        self.lock = threading.RLock()
        self.shutdown_flag = False
        self._stop_event = threading.Event() 
        self.workers = []
        self._start_worker_threads(worker_name_prefix)

    def _start_worker_threads(self, prefix: str):
        for i in range(self.max_workers):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"{prefix}-{i+1}",
                daemon=True
            )
            thread.start()
            self.workers.append(thread)

    def _worker_loop(self):
        while not self.shutdown_flag and not self._stop_event.is_set():
            try:

                func, args, kwargs, future = self.task_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            with self.lock:
                self.active_workers += 1

            try:
                result = func(*args, **kwargs)
                future.set_result(result)
            except Exception as exc:
                future.set_exception(exc)
                logger.exception("Exception in thread pool task")
            finally:
                with self.lock:
                    self.active_workers = max(0, self.active_workers - 1)
                    self.completed_count += 1
                self.task_queue.task_done()

    def submit(self,
                func: Callable,
               *args,
                block: bool = True,
                timeout: Optional[float] = None,
               **kwargs) -> Future:
        """Submit a callable to the thread pool. Returns a Future."""
        if self.shutdown_flag:
            future = Future()
            future.set_exception(RuntimeError("Thread pool is shutting down"))
            return future

        future = Future()
        task = (func, args, kwargs, future)

        try:
            self.task_queue.put(task, block=block, timeout=timeout)
        except queue.Full:
            with self.lock:
                self.rejected_count += 1
            future.set_exception(ThreadPoolFullError(
                f"Thread pool queue is full (max size={self.max_queue_size})"
            ))
            return future  

        with self.lock:
            self.total_submitted += 1
        return future

    def get_status(self) -> dict:
        """Return current thread pool status."""
        with self.lock:
            return {
                'max_workers': self.max_workers,
                'active_workers': self.active_workers,
                'queue_size': self.task_queue.qsize(),
                'max_queue_size': self.max_queue_size,
                'total_submitted': self.total_submitted,
                'completed_count': self.completed_count,
                'rejected_count': self.rejected_count,
                'is_shutdown': self.shutdown_flag,
            }

    def shutdown(self, wait: bool = True):
        """Shut down the thread pool and stop worker threads gracefully."""
        self.shutdown_flag = True
        self._stop_event.set()  
        if wait:
            for worker in self.workers:
                worker.join(timeout=1.0)

    def resize_workers(self, new_max_workers: int):

        if new_max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        with self.lock:
            current = self.max_workers
            if new_max_workers > current:

                for i in range(current, new_max_workers):
                    thread = threading.Thread(
                        target=self._worker_loop,
                        name=f"resized-worker-{i+1}",
                        daemon=True
                    )
                    thread.start()
                    self.workers.append(thread)
            elif new_max_workers < current:

                logger.warning("Reducing workers dynamically is not fully implemented; will stop after current tasks")

            self.max_workers = new_max_workers


# Global instance that reads from environment variables
_default_controller = AdvancedThreadPoolController()


def get_capacity_controller() -> AdvancedThreadPoolController:
    """Return the global thread pool controller instance."""
    return _default_controller