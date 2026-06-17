import os
import django

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "ecommerce.settings"
)

django.setup()
from django.test import TestCase
from django.core.cache import cache
from unittest.mock import patch


from core.cache_manager import (
    cache_aside,
    CacheKeys,
    invalidate_product,
    invalidate_dashboard,
)

from core.models import Product, Order
from django.contrib.auth.models import User


class CacheAsideTests(TestCase):

    def setUp(self):
        cache.clear()

    def test_cache_miss_then_hit(self):

        calls = {"count": 0}

        def fetch_fn():
            calls["count"] += 1
            return {"value": 123}

        result1 = cache_aside(
            "test:key",
            fetch_fn,
            ttl=60
        )

        result2 = cache_aside(
            "test:key",
            fetch_fn,
            ttl=60
        )

        self.assertEqual(result1, {"value": 123})
        self.assertEqual(result2, {"value": 123})

        # fetch_fn لازم تنفذ مرة واحدة فقط
        self.assertEqual(calls["count"], 1)

    def test_cache_none_not_cached(self):

        calls = {"count": 0}

        def fetch_fn():
            calls["count"] += 1
            return None

        cache_aside("none:key", fetch_fn, 60)
        cache_aside("none:key", fetch_fn, 60)

        # لأن None لا يتم تخزينه
        self.assertEqual(calls["count"], 2)


class CacheKeyTests(TestCase):

    def test_product_list_key(self):
        self.assertEqual(
            CacheKeys.product_list(),
            "product:list"
        )

    def test_product_detail_key(self):
        self.assertEqual(
            CacheKeys.product_detail(5),
            "product:5"
        )

    def test_dashboard_key(self):
        self.assertEqual(
            CacheKeys.dashboard_stats(),
            "dashboard:stats"
        )

    def test_order_stats_key(self):
        self.assertEqual(
            CacheKeys.order_stats(),
            "order:stats"
        )


class CacheInvalidationTests(TestCase):

    def setUp(self):
        cache.clear()

    def test_invalidate_product(self):

        cache.set("product:1", {"id": 1}, 60)
        cache.set("product:list", [{"id": 1}], 60)

        invalidate_product(1)

        self.assertIsNone(
            cache.get("product:1")
        )

        self.assertIsNone(
            cache.get("product:list")
        )

    def test_invalidate_dashboard(self):

        cache.set("dashboard:stats", {"x": 1}, 60)
        cache.set("order:stats", {"y": 1}, 60)

        invalidate_dashboard()

        self.assertIsNone(
            cache.get("dashboard:stats")
        )

        self.assertIsNone(
            cache.get("order:stats")
        )


class ProductSignalTests(TestCase):

    def setUp(self):
        cache.clear()

    @patch("core.signals.invalidate_product")
    def test_product_create_triggers_invalidation(
        self,
        mock_invalidate
    ):

        product = Product.objects.create(
            name="Phone",
            stock=10,
            price=100
        )

        mock_invalidate.assert_called_once_with(
            product.id
        )

    @patch("core.signals.invalidate_product")
    def test_product_update_triggers_invalidation(
        self,
        mock_invalidate
    ):

        product = Product.objects.create(
            name="Phone",
            stock=10,
            price=100
        )

        mock_invalidate.reset_mock()

        product.stock = 20
        product.save()

        mock_invalidate.assert_called_once_with(
            product.id
        )

    @patch("core.signals.invalidate_product")
    def test_product_delete_triggers_invalidation(
        self,
        mock_invalidate
    ):

        product = Product.objects.create(
            name="Phone",
            stock=10,
            price=100
        )

        product_id = product.id

        mock_invalidate.reset_mock()

        product.delete()

        mock_invalidate.assert_called_once_with(
            product_id
        )


class OrderSignalTests(TestCase):

    def setUp(self):

        cache.clear()

        self.user = User.objects.create_user(
            username="testuser",
            password="123456"
        )

    @patch("core.signals.invalidate_dashboard")
    def test_order_create_triggers_dashboard_invalidation(
        self,
        mock_invalidate
    ):

        Order.objects.create(
            user=self.user
        )

        mock_invalidate.assert_called_once()

    @patch("core.signals.invalidate_dashboard")
    def test_order_update_triggers_dashboard_invalidation(
        self,
        mock_invalidate
    ):

        order = Order.objects.create(
            user=self.user
        )

        mock_invalidate.reset_mock()

        order.status = "PAID"
        order.save()

        mock_invalidate.assert_called_once()

    @patch("core.signals.invalidate_dashboard")
    def test_order_delete_triggers_dashboard_invalidation(
        self,
        mock_invalidate
    ):

        order = Order.objects.create(
            user=self.user
        )

        mock_invalidate.reset_mock()

        order.delete()

        mock_invalidate.assert_called_once()


class RedisCacheTests(TestCase):

    def setUp(self):
        cache.clear()

    def test_redis_set_get(self):

        cache.set(
            "redis:test",
            {"status": "ok"},
            timeout=60
        )

        value = cache.get(
            "redis:test"
        )

        self.assertEqual(
            value["status"],
            "ok"
        )

    def test_redis_delete(self):

        cache.set(
            "redis:test",
            "hello",
            timeout=60
        )

        cache.delete(
            "redis:test"
        )

        self.assertIsNone(
            cache.get("redis:test")
        )