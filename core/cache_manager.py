"""
core/cache_manager.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Cache-Aside Pattern (Lazy Loading) فوق موديلاتك الحالية.

الفكرة الأساسية:
    1. دور في Redis أولاً
    2. إذا موجود (HIT)  → ارجعه فوراً، بدون DB
    3. إذا مش موجود (MISS) → اجلبه من DB
    4. خزّنه في Redis مع TTL
    5. ارجعه

كل الـ views بتمر من هون — ما حدا بيكلم DB مباشرة للقراءة.
"""

import logging
import time
from typing import Optional

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger('core.cache_manager')


#  TTL helper — يجيب الوقت من settings بدل ما يكون hardcoded

def _ttl(name: str) -> int:
    return settings.CACHE_TTL.get(name, 60 * 10)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  الدالة الأساسية — Cache-Aside Pattern
#  كل الدوال التانية بتمر من هون
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def cache_aside(cache_key: str, fetch_fn, ttl: int):
    """
    Generic Cache-Aside implementation.

    cache_key : المفتاح في Redis
    fetch_fn  : دالة تجيب البيانات من DB لما يكون MISS
    ttl       : وقت الصلاحية بالثواني
    """

    # ── Step 1: دور في Redis ─────────────────────────────────
    cached = cache.get(cache_key)
    if cached is not None:
        logger.debug("✅ CACHE HIT  | key='%s'", cache_key)
        return cached

    # ── Step 2: MISS → اجلب من DB ───────────────────────────
    logger.debug("❌ CACHE MISS | key='%s' → going to DB", cache_key)
    t0 = time.perf_counter()
    result = fetch_fn()
    ms = (time.perf_counter() - t0) * 1000
    logger.debug("   DB took %.2f ms", ms)

    # ── Step 3: خزّن في Redis ────────────────────────────────
    if result is not None:
        cache.set(cache_key, result, timeout=ttl)
        logger.debug("   Stored in Redis TTL=%ds | key='%s'", ttl, cache_key)

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  KEY BUILDERS — مفاتيح واضحة وموحّدة
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class CacheKeys:
    @staticmethod
    def product_list() -> str:
        return "product:list"

    @staticmethod
    def product_detail(product_id: int) -> str:
        return f"product:{product_id}"

    @staticmethod
    def dashboard_stats() -> str:
        return "dashboard:stats"

    @staticmethod
    def order_stats() -> str:
        return "order:stats"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. قائمة المنتجات
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_product_list() -> list:
    """
    بتجيب كل المنتجات.
    TTL: 10 دقائق — القائمة بتتغير لما يُضاف أو يُحذف منتج،
    والـ signal بيمسح الكاش فوراً عند أي تعديل.
    """
    from core.models import Product

    def _fetch():
        products = Product.objects.all().values(
            'id', 'name', 'stock', 'price'
        )
        return list(products)

    return cache_aside(
        CacheKeys.product_list(),
        _fetch,
        _ttl('PRODUCT_LIST')
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. تفاصيل منتج واحد
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_product_detail(product_id: int) -> Optional[dict]:
    """
    TTL: 30 دقيقة — المنتج الواحد نادراً يتغير.
    لما يتغير، الـ signal بيمسح مفتاحه تحديداً.
    """
    from core.models import Product

    def _fetch():
        try:
            p = Product.objects.get(id=product_id)
            return {
                'id':    p.id,
                'name':  p.name,
                'stock': p.stock,
                'price': float(p.price),
            }
        except Product.DoesNotExist:
            return None

    return cache_aside(
        CacheKeys.product_detail(product_id),
        _fetch,
        _ttl('PRODUCT_DETAIL')
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. إحصائيات الداشبورد
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_dashboard_stats() -> dict:
    """
    ليش 5 دقائق بس؟
    لأن الداشبورد بيحتاج يكون شبه حديث — المدير بده يشوف
    أرقام قريبة من الواقع، مش أرقام من ساعة.
    """
    from core.models import Product, Order
    from django.db.models import Sum, Count

    def _fetch():
        product_stats = Product.objects.aggregate(
            total_products=Count('id'),
            low_stock=Count('id', filter=__import__(
                'django.db.models', fromlist=['Q']
            ).Q(stock__lte=10))
        )

        order_stats = Order.objects.aggregate(
            total_orders=Count('id'),
            pending=Count('id', filter=__import__(
                'django.db.models', fromlist=['Q']
            ).Q(status='PENDING')),
            paid=Count('id', filter=__import__(
                'django.db.models', fromlist=['Q']
            ).Q(status='PAID')),
            completed=Count('id', filter=__import__(
                'django.db.models', fromlist=['Q']
            ).Q(status='COMPLETED')),
        )

        return {
            'products': product_stats,
            'orders':   order_stats,
        }

    return cache_aside(
        CacheKeys.dashboard_stats(),
        _fetch,
        _ttl('DASHBOARD_STATS')
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. إحصائيات الطلبات (للـ monitoring)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_order_stats() -> dict:
    """
    مشابه للداشبورد بس مخصص لصفحة الـ monitoring.
    TTL: 5 دقائق.
    """
    from core.models import Order
    from django.db.models import Count
    from django.utils import timezone

    def _fetch():
        today = timezone.now().date()
        return {
            'total':     Order.objects.count(),
            'today':     Order.objects.filter(created_at__date=today).count(),
            'pending':   Order.objects.filter(status='PENDING').count(),
            'paid':      Order.objects.filter(status='PAID').count(),
            'completed': Order.objects.filter(status='COMPLETED').count(),
            'cancelled': Order.objects.filter(status='CANCELLED').count(),
        }

    return cache_aside(
        CacheKeys.order_stats(),
        _fetch,
        _ttl('ORDER_STATS')
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  INVALIDATION FUNCTIONS — بتنادي عليها الـ signals
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def invalidate_product(product_id: int) -> None:
    """
    لما منتج يتغير، امسح:
    - مفتاحه هو (product:{id})
    - قائمة كل المنتجات (product:list)
    ليش القائمة كمان؟ لأنها بتحتوي على بياناته.
    """
    keys = [
        CacheKeys.product_detail(product_id),
        CacheKeys.product_list(),
    ]
    cache.delete_many(keys)
    logger.debug("🗑  Invalidated: %s", keys)


def invalidate_dashboard() -> None:
    """لما طلب يتغير → إحصائيات الداشبورد بتتغير."""
    cache.delete(CacheKeys.dashboard_stats())
    cache.delete(CacheKeys.order_stats())
    logger.debug("🗑  Invalidated dashboard + order stats")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DIAGNOSTICS — لصفحة الـ monitoring
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_cache_info() -> dict:
    """
    بتجيب معلومات Redis Server:
    - كم HIT وكم MISS
    - نسبة الـ hit rate
    - كمية الذاكرة المستخدمة
    """
    try:
        from django_redis import get_redis_connection
        info  = get_redis_connection("default").info()
        hits   = info.get('keyspace_hits', 0)
        misses = info.get('keyspace_misses', 0)
        total  = hits + misses
        return {
            'status':            'connected',
            'used_memory':       info.get('used_memory_human'),
            'connected_clients': info.get('connected_clients'),
            'keyspace_hits':     hits,
            'keyspace_misses':   misses,
            'hit_rate_pct':      round(hits / max(total, 1) * 100, 2),
        }
    except Exception as exc:
        return {'status': 'error', 'detail': str(exc)}