from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from .async_tasks import get_task_queue
        from .task_handlers import (
            handle_send_notification,
            handle_generate_invoice,
            handle_order_confirmation,
            handle_payment_receipt,
            handle_cancellation_notice,
        )

        queue = get_task_queue()

        queue.register_handler('send_notification', handle_send_notification)
        queue.register_handler('generate_invoice', handle_generate_invoice)
        queue.register_handler('order_confirmation', handle_order_confirmation)
        queue.register_handler('payment_receipt', handle_payment_receipt)
        queue.register_handler('cancellation_notice', handle_cancellation_notice)

        queue.start_workers()
        logger.info("Async task queue initialized with handlers")
