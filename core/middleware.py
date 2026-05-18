import logging
import time
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from rest_framework import status

from .capacity_controller import get_capacity_controller

logger = logging.getLogger(__name__)


class ThreadPoolControlMiddleware(MiddlewareMixin):
    """Middleware that rejects requests when capacity is exceeded."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.thread_pool = get_capacity_controller()
        super().__init__(get_response)

    def process_request(self, request):
        """رفض الطلبات الحساسة فوراً إذا كان النظام محملاً."""
        if not self._is_critical_operation(request):
            return None

        status_data = self.thread_pool.get_status()
        queue_size = status_data['queue_size']
        max_queue = status_data['max_queue_size']


        if max_queue == 0:
            return None


        if queue_size / max_queue >= 0.9:
            logger.warning(f"Queue full ({queue_size}/{max_queue}), rejecting {request.path}")
            return self._get_capacity_exceeded_response()


        active_workers = status_data['active_workers']
        max_workers = status_data['max_workers']
        if active_workers >= max_workers and queue_size >= max_queue * 0.8:
            logger.warning(f"Workers saturated ({active_workers}/{max_workers}) and queue pressure ({queue_size}/{max_queue})")
            return self._get_capacity_exceeded_response()

        return None

    def process_response(self, request, response):

        try:
            status_data = self.thread_pool.get_status()
            response['X-Active-Workers'] = str(status_data['active_workers'])
            response['X-Queue-Length'] = str(status_data['queue_size'])
            response['X-Rejected-Count'] = str(status_data['rejected_count'])
            response['X-Max-Workers'] = str(status_data['max_workers'])
            response['X-Max-Queue'] = str(status_data['max_queue_size'])
        except Exception:
            pass
        return response

    @staticmethod
    def _is_critical_operation(request) -> bool:
        critical_paths = ['/api/orders/', '/api/checkout/', '/api/payment/']
        for path in critical_paths:
            if request.path.startswith(path) and request.method in ['POST', 'PUT', 'DELETE']:
                return True
        return False

    @staticmethod
    def _get_capacity_exceeded_response():
        return JsonResponse(
            {'error': 'System at capacity. Try again later.', 'status': 'SERVICE_OVERLOADED'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE
        )


class ThrottlingMiddleware(MiddlewareMixin):
    """Rate limiting using token bucket with dynamic rate based on system load."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.thread_pool = get_capacity_controller()
        self._ip_tokens = {}
        import threading
        self._lock = threading.Lock() 
        super().__init__(get_response)

    def process_request(self, request):
        client_ip = self._get_client_ip(request)
        current_time = time.time()

        with self._lock:
            status_data = self.thread_pool.get_status()
            active_ratio = status_data['active_workers'] / max(1, status_data['max_workers'])
            queue_ratio = status_data['queue_size'] / max(1, status_data['max_queue_size'])
            load_factor = max(active_ratio, queue_ratio)

            max_requests_per_minute = int(1000 * (1 - load_factor) + 50)

            tokens, last = self._ip_tokens.get(client_ip, (max_requests_per_minute, current_time))
            elapsed = current_time - last
            refill_rate = max_requests_per_minute / 60.0
            tokens = min(max_requests_per_minute, tokens + elapsed * refill_rate)

            if tokens >= 1:
                tokens -= 1
                self._ip_tokens[client_ip] = (tokens, current_time)
                return None
            else:
                retry_after = int((1 - tokens) / refill_rate) + 1
                logger.warning(f"Rate limit exceeded for {client_ip}")
                return JsonResponse(
                    {'error': 'Rate limit exceeded', 'retry_after': retry_after},
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )

    @staticmethod
    def _get_client_ip(request):
        x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        return x_forwarded.split(',')[0] if x_forwarded else request.META.get('REMOTE_ADDR')


class RequestLoggingMiddleware(MiddlewareMixin):

    def __init__(self, get_response):
        self.get_response = get_response
        super().__init__(get_response)

    def process_request(self, request):
        request.start_time = time.time()
        return None

    def process_response(self, request, response):
        duration = time.time() - request.start_time
        if duration > 1.0:
            logger.warning(f"Slow request: {request.method} {request.path} took {duration:.2f}s")
        return response