import logging
import time
from typing import Optional

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger('core.cache_manager')


# THe maximum allowed caching time for each cache key, in seconds "Time TO Live"
def _ttl(name: str) -> int:
    return settings.CACHE_TTL.get(name, 60 * 10)

# Cache Aside Pattern
def cache_aside(cache_key: str, fetch_fn, ttl: int):
   

    # ── Step 1: دور في Redis على النتيجة 
    cached = cache.get(cache_key)
    if cached is not None:
        logger.debug(" CACHE HIT  | key='%s'", cache_key)
        return cached

    # ── Step 2: MISS → اجلب من DB ───────────────────────────
    logger.debug(" CACHE MISS | key='%s' → going to DB", cache_key)
    t0 = time.perf_counter()
    result = fetch_fn()
    ms = (time.perf_counter() - t0) * 1000
    logger.debug("   DB took %.2f ms", ms)

    # ── Step 3: خزّن في Redis ────────────────────────────────
    if result is not None:
        cache.set(cache_key, result, timeout=ttl)
        logger.debug("   Stored in Redis TTL=%ds | key='%s'", ttl, cache_key)

    return result


#  KEY BUILDERS — مفاتيح واضحة وموحّدة
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


#  1. قائمة المنتجات
def get_product_list() -> list:
    
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


#  2. تفاصيل منتج واحد
def get_product_detail(product_id: int) -> Optional[dict]:
    
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


#  3. إحصائيات الداشبورد
def get_dashboard_stats() -> dict:
    from core.models import Product, Order
    from django.db.models import Count, Q   # ← أضف Q هون

    def _fetch():
        product_stats = Product.objects.aggregate(
            total_products=Count('id'),
            low_stock=Count('id', filter=Q(stock__lte=10))   # ← نظيف
        )
        order_stats = Order.objects.aggregate(
            total_orders=Count('id'),
            pending=Count('id',   filter=Q(status='PENDING')),
            paid=Count('id',      filter=Q(status='PAID')),
            completed=Count('id', filter=Q(status='COMPLETED')),
        )
        return {'products': product_stats, 'orders': order_stats}

    return cache_aside(CacheKeys.dashboard_stats(), _fetch, _ttl('DASHBOARD_STATS'))



#  4. إحصائيات الطلبات (للـ monitoring)

def get_order_stats() -> dict:
    
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


#  INVALIDATION FUNCTIONS — بتنادي عليها الـ signals
def invalidate_product(product_id: int) -> None:

    keys = [
        CacheKeys.product_detail(product_id),
        CacheKeys.product_list(),
        CacheKeys.dashboard_stats(),
    ]

    cache.delete_many(keys)

    logger.debug("Invalidated: %s", keys)



def invalidate_dashboard() -> None:
    cache.delete(CacheKeys.dashboard_stats())
    cache.delete(CacheKeys.order_stats())
    logger.debug("  Invalidated dashboard + order stats")


#  DIAGNOSTICS — لصفحة الـ monitoring
def get_cache_info() -> dict:
   
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
    
def get_sales_stats():

    from core.models import DailySalesReport
    from django.db.models import Sum, Avg, Count
    from datetime import datetime, timedelta

    def _fetch():

        last_30_days = (
            datetime.now() - timedelta(days=30)
        ).date()

        stats = DailySalesReport.objects.filter(
            date__gte=last_30_days,
            status='COMPLETED'
        ).aggregate(
            total_revenue=Sum('total_revenue'),
            total_orders=Sum('total_orders'),
            total_items=Sum('total_items_sold'),
            avg_revenue=Avg('total_revenue'),
            avg_orders=Avg('total_orders'),
            count=Count('id')
        )

        return stats

    return cache_aside(
        "sales:stats",
        _fetch,
        300
    )