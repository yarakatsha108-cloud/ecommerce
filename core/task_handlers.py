import time
import logging
from .models import Order

logger = logging.getLogger(__name__)


def handle_send_notification(user_id: int, message: str):

    logger.info(f"NOTIFICATION to user#{user_id}: {message}")
    time.sleep(2)
    logger.info(f"Notification sent to user#{user_id}")


def handle_generate_invoice(order_id: int):

    logger.info(f"INVOICE: Generating invoice for order#{order_id}")
    time.sleep(3)
    logger.info(f"INVOICE: Invoice ready for order#{order_id}")


def handle_order_confirmation(order_id: int):

    logger.info(f"CONFIRMATION: Sending order#{order_id} confirmation")
    time.sleep(1.5)
    logger.info(f"CONFIRMATION: Order#{order_id} confirmed")


def handle_payment_receipt(order_id: int):

    logger.info(f"RECEIPT: Generating payment receipt for order#{order_id}")
    time.sleep(2.5)
    logger.info(f"RECEIPT: Receipt sent for order#{order_id}")


def handle_cancellation_notice(order_id: int):

    logger.info(f"CANCEL: Processing cancellation for order#{order_id}")
    time.sleep(1)
    logger.info(f"CANCEL: Cancellation confirmed for order#{order_id}")