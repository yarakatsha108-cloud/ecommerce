from django.shortcuts import render
from rest_framework.viewsets import ModelViewSet
from .batch_processor import BatchProcessor
from .models import DailySalesReport, Product, Order, OrderItem
from .serializers import (
    DailySalesReportSerializer, ProductSerializer,
    OrderSerializer, OrderItemSerializer, CreateOrderSerializer
)
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .serializers import RegisterSerializer
from rest_framework.views import APIView
from rest_framework import status
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from datetime import datetime, timedelta
from django.db import transaction
from django.db.models import F, Count, Sum, Avg
from .async_tasks import get_task_queue
import logging
import time as _time
from concurrent.futures import TimeoutError as FutureTimeoutError
from core.cache_manager import (
    get_dashboard_stats,
    get_cache_info,
    get_product_list,
    get_product_detail,
    get_order_stats,
    get_sales_stats,
)

from .capacity_controller import get_capacity_controller, ThreadPoolFullError
from .distributed_lock import acquire_product_lock, LockAcquisitionError
from .benchmarking import benchmark

logger = logging.getLogger(__name__)


@api_view(['POST'])
def register(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response({"message": "User created successfully"})


class ProductListCreateAPIView(APIView):
    @benchmark('ProductListCreateAPIView.get')
    def get(self, request):
        data = get_product_list()      
        if not data:
            return Response({"error": "Not found"}, status=404)
        return Response(data)

    def post(self, request):
        serializer = ProductSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ProductDetailAPIView(APIView):
    def get_object(self, id):
        try:
            return Product.objects.get(id=id)
        except Product.DoesNotExist:
            return None

    def get(self, request, id):
        product = get_product_detail(id)

        if not product:
            return Response({"error": "Not found"}, status=404)

        return Response(product)

    def put(self, request, id):
        product = self.get_object(id)
        if not product:
            return Response({"error": "Not found"}, status=404)
        serializer = ProductSerializer(product, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=400)

    def delete(self, request, id):
        product = self.get_object(id)
        if not product:
            return Response({"error": "Not found"}, status=404)
        product.delete()
        return Response({"message": "Deleted successfully"}, status=204)


class OrderListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        orders = Order.objects.filter(user=request.user)
        serializer = OrderSerializer(orders, many=True)
        return Response(serializer.data)

    @benchmark('OrderListCreateAPIView.post')
    def post(self, request):
        serializer = CreateOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product_id = serializer.validated_data['product_id']
        quantity = serializer.validated_data['quantity']

        if quantity <= 0:
            return Response({"error": "Invalid quantity"}, status=400)

        controller = get_capacity_controller()

        def process_order():
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    product = Product.objects.get(id=product_id)
                except Product.DoesNotExist:
                    raise ValueError(f"Product #{product_id} not found")

                if product.stock < quantity:
                    raise ValueError(
                        f"Insufficient stock — available: {product.stock}, requested: {quantity}"
                    )

                updated = Product.objects.filter(
                    id=product_id,
                    version=product.version,
                    stock__gte=quantity
                ).update(
                    stock=F('stock') - quantity,
                    version=F('version') + 1
                )

                if updated:
                    with transaction.atomic():
                        order = Order.objects.create(user=request.user)
                        OrderItem.objects.create(
                            order=order,
                            product_id=product_id,
                            quantity=quantity
                        )

                    logger.info(
                        f"[OptimisticLock] order #{order.id} | product #{product_id} | "
                        f"version {product.version} -> {product.version + 1}"
                    )
                    return order.id

                _time.sleep(0.05)

            raise ValueError("System busy due to concurrent updates, please try again")

        try:
            future = controller.submit(process_order, block=False)
            order_id = future.result(timeout=15)

            queue = get_task_queue()
            queue.enqueue('order_confirmation', order_id)
            queue.enqueue('send_notification', request.user.id,
                          f"Order #{order_id} created successfully")

            return Response({"message": "Order created", "order_id": order_id}, status=201)

        except ThreadPoolFullError:
            logger.warning(f"Order creation rejected due to capacity: user {request.user.id}")
            return Response(
                {"error": "System is overloaded. Please try again later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except FutureTimeoutError:
            logger.error(f"Order processing timeout for user {request.user.id}")
            return Response(
                {"error": "Order processing timed out. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception(f"Unexpected error in order creation: {e}")
            return Response(
                {"error": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class OrderDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, id):
        order = get_object_or_404(Order, id=id, user=request.user)
        serializer = OrderSerializer(order)
        return Response(serializer.data)

    @benchmark('OrderDetailAPIView.put')
    def put(self, request, id):
        order = get_object_or_404(Order, id=id, user=request.user)

        new_quantity = request.data.get('quantity')
        if not new_quantity:
            return Response({"error": "Quantity required"}, status=400)

        new_quantity = int(new_quantity)

        # ✅ ACID: تحديث الكمية + المخزون معاً أو لا شي
        with transaction.atomic():
            item = order.orderitem_set.select_for_update().first()
            if not item:
                return Response({"error": "No items in order"}, status=400)

            product = Product.objects.select_for_update().get(id=item.product_id)
            diff = new_quantity - item.quantity

            if diff > 0 and product.stock < diff:
                return Response({"error": "Not enough stock"}, status=400)

            # العمليتان تنجحان معاً أو تفشلان معاً (Atomicity)
            product.stock -= diff
            product.save(update_fields=['stock'])

            item.quantity = new_quantity
            item.save()

        return Response({"message": "Order updated"})

    @benchmark('OrderDetailAPIView.delete')
    def delete(self, request, id):
        order = get_object_or_404(Order, id=id, user=request.user)

        # ✅ ACID: حذف الطلب + إرجاع المخزون معاً أو لا شي
        with transaction.atomic():
            item = order.orderitem_set.select_for_update().first()
            if not item:
                order.delete()
                return Response({"message": "Order deleted"}, status=204)

            product = Product.objects.select_for_update().get(id=item.product_id)
            product.stock += item.quantity
            product.save(update_fields=['stock'])

            order.delete()

        return Response({"message": "Order deleted"}, status=204)


class PayOrderAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @benchmark('PayOrderAPIView.post')
    def post(self, request, id):
        
        with transaction.atomic():
            # select_for_update يمنع thread آخر من يدفع نفس الطلب بنفس الوقت
            order = Order.objects.select_for_update().get(
                id=id, user=request.user
            ) if Order.objects.filter(id=id, user=request.user).exists() else None

            if not order:
                return Response({"error": "Order not found"}, status=404)

            # Consistency check
            if order.status != 'PENDING':
                return Response({"error": "Order already processed"}, status=400)

            # Atomicity: تغيير الحالة
            order.status = 'PAID'
            order.save(update_fields=['status'])

            logger.info(f"[ACID] ✅ Payment committed — order #{order.id} → PAID")

        # المهام الـ async خارج الـ transaction (لأنها لا تحتاج rollback)
        queue = get_task_queue()
        queue.enqueue('generate_invoice', order.id)
        queue.enqueue('payment_receipt', order.id)
        queue.enqueue('send_notification', request.user.id,
                      f"Order #{order.id} paid successfully")

        return Response({"message": "Payment successful"})


class CompleteOrderAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @benchmark('CompleteOrderAPIView.post')
    def post(self, request, id):
        """
        ✅ ACID Transaction على عملية الإكمال
        """
        with transaction.atomic():
            order = get_object_or_404(Order, id=id, user=request.user)

            if order.status != 'PAID':
                return Response({"error": "Order not paid"}, status=400)

            order.status = 'COMPLETED'
            order.save(update_fields=['status'])

            logger.info(f"[ACID] ✅ Order #{order.id} → COMPLETED")

        queue = get_task_queue()
        queue.enqueue('send_notification', request.user.id,
                      f"Order #{order.id} completed")

        return Response({"message": "Order completed"})


class CancelOrderAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @benchmark('CancelOrderAPIView.post')
    def post(self, request, id):
        
        with transaction.atomic():
            order = get_object_or_404(Order, id=id, user=request.user)

            if order.status == 'CANCELLED':
                return Response({"error": "Already cancelled"}, status=400)

            # العملية 1: إرجاع المخزون (Atomicity)
            item = order.orderitem_set.select_for_update().first()
            if item:
                product = Product.objects.select_for_update().get(id=item.product_id)
                product.stock += item.quantity
                product.save(update_fields=['stock'])

            # العملية 2: تغيير الحالة (Atomicity)
            order.status = 'CANCELLED'
            order.save(update_fields=['status'])

            logger.info(
                f"[ACID] ✅ Cancellation committed — order #{order.id} → CANCELLED | "
                f"stock restored: +{item.quantity if item else 0}"
            )

        queue = get_task_queue()
        queue.enqueue('cancellation_notice', order.id)
        queue.enqueue('send_notification', request.user.id,
                      f"Order #{order.id} cancelled")

        return Response({"message": "Order cancelled"})


class DailySalesReportListAPIView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        reports = DailySalesReport.objects.all()[:30]
        serializer = DailySalesReportSerializer(reports, many=True)
        return Response(serializer.data)


class DailySalesReportDetailAPIView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, date):
        try:
            report = DailySalesReport.objects.get(date=date)
            serializer = DailySalesReportSerializer(report)
            return Response(serializer.data)
        except DailySalesReport.DoesNotExist:
            return Response(
                {"error": f"No report found for {date}"},
                status=status.HTTP_404_NOT_FOUND
            )


class ProcessDailySalesAPIView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        date_str = request.data.get('date')
        batch_size = request.data.get('batch_size', 100)

        try:
            batch_size = int(batch_size)
        except (ValueError, TypeError):
            batch_size = 100

        if not (10 <= batch_size <= 1000):
            return Response(
                {"error": "batch_size must be between 10 and 1000"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            if date_str:
                date = datetime.strptime(date_str, '%Y-%m-%d').date()
            else:
                date = (datetime.now() - timedelta(days=1)).date()

            processor = BatchProcessor(batch_size=batch_size)
            report = processor.process_daily_sales(date)

            serializer = DailySalesReportSerializer(report)
            return Response({
                'message': f'Sales processed successfully for {date}',
                'report': serializer.data,
                'stats': processor.get_processing_stats()
            }, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response(
                {"error": f"Date format error: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"error": f"Error processing sales: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SalesReportStatsAPIView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):

        from core.cache_manager import get_sales_stats

        stats = get_sales_stats()

        if not stats['count']:
            return Response({
                'message': 'No completed reports found',
                'total_reports': 0,
                'stats': {}
            })

        return Response({
            'total_reports': stats['count'],
            'stats': stats
        })


class DashboardStatsAPIView(APIView):
    def get(self, request):
        data = get_dashboard_stats()
        return Response(data)

class OrderStatsAPIView(APIView):

    def get(self, request):
        return Response(get_order_stats())

class CacheDiagnosticsAPIView(APIView):
    def get(self, request):
        return Response({
            'cache_info': get_cache_info(),
        })
    
