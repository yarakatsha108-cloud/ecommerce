import threading
import queue
import time
import logging
from typing import Callable, Any

logger = logging.getLogger(__name__)


class AsyncTaskQueue:

    def __init__(self, maxsize: int = 1000, num_workers: int = 3):
        self.queue = queue.Queue(maxsize=maxsize)
        self.num_workers = num_workers
        self.workers: list[threading.Thread] = []
        self.running = False
        self._handler_registry: dict[str, Callable] = {}

    def register_handler(self, task_type: str, handler: Callable):
        self._handler_registry[task_type] = handler

    def enqueue(self, task_type: str, *args, **kwargs) -> bool:

        try:
            self.queue.put_nowait({
                'type': task_type,
                'args': args,
                'kwargs': kwargs,
                'timestamp': time.time(),
            })
            logger.debug(f"Task queued: {task_type}")
            return True
        except queue.Full:
            logger.warning(f"Task queue full! Dropping task: {task_type}")
            return False

    def start_workers(self):
        if self.running:
            return
        self.running = True
        for i in range(self.num_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"AsyncWorker-{i+1}",
                daemon=True
            )
            t.start()
            self.workers.append(t)
        logger.info(f"Started {self.num_workers} async workers")

    def stop_workers(self, timeout: float = 5.0):
        self.running = False
        for t in self.workers:
            t.join(timeout=timeout)
        self.workers.clear()
        logger.info("Async workers stopped")

    def _worker_loop(self):
        while self.running:
            try:
                task = self.queue.get(timeout=1.0)
                self._process_task(task)
                self.queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Worker error: {e}")

    def _process_task(self, task: dict):
        task_type = task['type']
        handler = self._handler_registry.get(task_type)
        if not handler:
            logger.error(f"No handler registered for task type: {task_type}")
            return
        try:
            logger.info(f"Processing task: {task_type}")
            handler(*task['args'], **task['kwargs'])
            logger.info(f"Task completed: {task_type}")
        except Exception as e:
            logger.error(f"Task failed: {task_type} - {e}")

    def get_status(self) -> dict:
        return {
            'queue_size': self.queue.qsize(),
            'active_workers': sum(1 for t in self.workers if t.is_alive()),
            'total_workers': self.num_workers,
            'registered_handlers': list(self._handler_registry.keys()),
        }


task_queue = AsyncTaskQueue(maxsize=1000, num_workers=3)


def get_task_queue() -> AsyncTaskQueue:
    return task_queue