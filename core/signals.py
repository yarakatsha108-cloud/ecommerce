"""
core/signals.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ليش Signals ومو مسح يدوي في الـ views؟

لو مسحنا يدوي في الـ view:
    def put(self, request, id):
        ...
        product.save()
        cache.delete(...)  # ← لازم تتذكر تحطه بكل view

مع Signals:
    أي كود يعمل product.save() في أي مكان
    (admin, view, management command, test)
    → الـ signal بيمسح الكاش تلقائياً بدون ما تتذكر شي
"""

import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.models import Order, Product
from core.cache_manager import invalidate_dashboard, invalidate_product

from django.apps import AppConfig

logger = logging.getLogger('core.signals')


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Product Signals
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@receiver(post_save, sender=Product)
def on_product_saved(sender, instance, created, **kwargs):
    """
    بيشتغل بعد كل product.save() أو Product.objects.create()
    created=True  → منتج جديد أُضيف
    created=False → منتج موجود اتعدّل
    """
    action = "created" if created else "updated"
    logger.debug("[Signal] Product %s (id=%s) → invalidating cache", action, instance.pk)
    invalidate_product(instance.pk)


@receiver(post_delete, sender=Product)
def on_product_deleted(sender, instance, **kwargs):
    """بيشتغل بعد product.delete()"""
    logger.debug("[Signal] Product deleted (id=%s) → invalidating cache", instance.pk)
    invalidate_product(instance.pk)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Order Signals
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@receiver(post_save, sender=Order)
def on_order_saved(sender, instance, created, **kwargs):
    """
    لما طلب يتغير (status: PENDING→PAID→COMPLETED)
    → إحصائيات الداشبورد تتغير → امسح كاشها
    """
    logger.debug("[Signal] Order saved (id=%s) → invalidating dashboard", instance.pk)
    invalidate_dashboard()


@receiver(post_delete, sender=Order)
def on_order_deleted(sender, instance, **kwargs):
    logger.debug("[Signal] Order deleted (id=%s) → invalidating dashboard", instance.pk)
    invalidate_dashboard()

class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
 
    def ready(self):
        import core.signals 