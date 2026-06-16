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
from concurrent.futures import TimeoutError as FutureTimeoutError
<<<<<<< HEAD
from core.cache_manager import get_dashboard_stats, get_cache_info, get_product_list
=======
>>>>>>> origin/main

from .capacity_controller import get_capacity_controller, ThreadPoolFullError
from .distributed_lock import acquire_product_lock, LockAcquisitionError

logger = logging.getLogger(__name__)


@api_view(['POST'])
def register(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response({"message": "User created successfully"})


class ProductListCreateAPIView(APIView):
    def get(self, request):
        data = get_product_list()      # ← من الكاش
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
        product = self.get_object(id)
        if not product:
            return Response({"error": "Not found"}, status=404)
        serializer = ProductSerializer(product)
        return Response(serializer.data)

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

    def post(self, request):
        serializer = CreateOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product_id = serializer.validated_data['product_id']
        quantity = serializer.validated_data['quantity']

        if quantity <= 0:
            return Response({"error": "Invalid quantity"}, status=400)

        controller = get_capacity_controller()

        def process_order():
           
            with acquire_product_lock(product_id, timeout=10.0):

                # ── transaction.atomic: يضمن ACID على العمليات الثلاث ──
                with transaction.atomic():

                    # العملية 1: قراءة المنتج مع قفل DB (Isolation)
                    try:
                        product = Product.objects.select_for_update(
                            nowait=False
                        ).get(id=product_id)
                    except Product.DoesNotExist:
                        raise ValueError(f"Product #{product_id} not found")

                    # Consistency check: فحص المخزون قبل أي تعديل
                    if product.stock < quantity:
                        raise ValueError(
                            f"Insufficient stock — available: {product.stock}, requested: {quantity}"
                        )

                    # العملية 2: خصم المخزون (Atomicity)
                    product.stock -= quantity
                    product.save(update_fields=['stock'])

                    # العملية 3: إنشاء الطلب (Atomicity)
                    order = Order.objects.create(user=request.user)

                    # العملية 4: إنشاء OrderItem (Atomicity)
                    # لو فشلت هاي → rollback كل شي فوق معها
                    OrderItem.objects.create(
                        order=order,
                        product_id=product_id,
                        quantity=quantity
                    )

                    logger.info(
                        f"[ACID] ✅ Transaction committed — "
                        f"order #{order.id} | product #{product_id} | "
                        f"stock: {product.stock + quantity} → {product.stock}"
                    )

                    return order.id

        try:
            future = controller.submit(process_order, block=False)
            order_id = future.result(timeout=15)

            queue = get_task_queue()
            queue.enqueue('order_confirmation', order_id)
            queue.enqueue('send_notification', request.user.id,
                          f"Order #{order_id} created successfully")

            return Response({"message": "Order created", "order_id": order_id}, status=201)

        except LockAcquisitionError as e:
            logger.warning(f"[Locking] Lock failed for user {request.user.id}: {e}")
            return Response(
                {"error": "System is busy, please try again later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
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
        last_30_days = (datetime.now() - timedelta(days=30)).date()

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

        if not stats['count']:
            return Response({
                'message': 'No completed reports found',
                'total_reports': 0,
                'stats': {}
            })

        return Response({
            'period': f'Last 30 days (from {last_30_days} to now)',
            'total_reports': stats['count'],
            'stats': {
                'total_revenue': float(stats['total_revenue'] or 0),
                'average_daily_revenue': float(stats['avg_revenue'] or 0),
                'total_orders': stats['total_orders'] or 0,
                'average_orders_per_day': float(stats['avg_orders'] or 0),
                'total_items_sold': stats['total_items'] or 0,
            }
        }, status=status.HTTP_200_OK)
class DashboardStatsAPIView(APIView):
    def get(self, request):
        data = get_dashboard_stats()
        return Response(data)


class CacheDiagnosticsAPIView(APIView):
    def get(self, request):
        return Response({
            'cache_info': get_cache_info(),
        })