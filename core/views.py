from django.shortcuts import render
from rest_framework.viewsets import ModelViewSet
from .batch_processor import BatchProcessor
from .models import DailySalesReport, Product , Order , OrderItem
from .serializers import DailySalesReportSerializer, ProductSerializer , OrderSerializer , OrderItemSerializer , CreateOrderSerializer
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .serializers import RegisterSerializer
from rest_framework.views import APIView
from rest_framework import status
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated , IsAdminUser
from datetime import datetime, timedelta
from django.db import transaction
from django.db.models import F, Count, Sum , Avg
from .async_tasks import get_task_queue
import logging
from concurrent.futures import TimeoutError as FutureTimeoutError


from .capacity_controller import get_capacity_controller, ThreadPoolFullError

logger = logging.getLogger(__name__)


@api_view(['POST'])
def register(request):
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response({"message": "User created successfully"})


class ProductListCreateAPIView(APIView):
    def get(self, request):
        products = Product.objects.all()
        serializer = ProductSerializer(products, many=True)
        return Response(serializer.data)

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
            with transaction.atomic():
                
                updated = Product.objects.filter(
                    id=product_id,
                    stock__gte=quantity
                ).update(stock=F('stock') - quantity)

            if not updated:
                raise ValueError("Not enough stock")

            order = Order.objects.create(user=request.user)
            OrderItem.objects.create(
                order=order,
                product_id=product_id,
                quantity=quantity
            )
            return order.id

        try:
            
            future = controller.submit(process_order, block=False)
            
            order_id = future.result(timeout=5)

            
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
                status=status.HTTP_503_SERVICE_UNAVAILABLE
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
        item = order.orderitem_set.first()

        with transaction.atomic():
            item = order.orderitem_set.select_for_update().first()
            product = item.product

        new_quantity = request.data.get('quantity')
        if not new_quantity:
            return Response({"error": "Quantity required"}, status=400)

        new_quantity = int(new_quantity)
        product = item.product
        diff = new_quantity - item.quantity

        if diff > 0 and product.stock < diff:
            return Response({"error": "Not enough stock"}, status=400)

        product.stock -= diff
        product.save()
        item.quantity = new_quantity
        item.save()

        return Response({"message": "Order updated"})

    def delete(self, request, id):
        order = get_object_or_404(Order, id=id, user=request.user)
        item = order.orderitem_set.first()
        product = item.product

        with transaction.atomic():
            item = order.orderitem_set.select_for_update().first()
            product = item.product

        product.stock += item.quantity
        product.save()
        order.delete()

        return Response({"message": "Order deleted"}, status=204)


class PayOrderAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, id):
        order = get_object_or_404(Order, id=id, user=request.user)
        if order.status != 'PENDING':
            return Response({"error": "Order already processed"}, status=400)

        order.status = 'PAID'
        order.save()

        queue = get_task_queue()
        queue.enqueue('generate_invoice', order.id)
        queue.enqueue('payment_receipt', order.id)
        queue.enqueue('send_notification', request.user.id,
                        f"Order #{order.id} paid successfully")

        return Response({"message": "Payment successful"})


class CompleteOrderAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, id):
        order = get_object_or_404(Order, id=id, user=request.user)
        if order.status != 'PAID':
            return Response({"error": "Order not paid"}, status=400)

        order.status = 'COMPLETED'
        order.save()

        queue = get_task_queue()
        queue.enqueue('send_notification', request.user.id,
                        f"Order #{order.id} completed")

        return Response({"message": "Order completed"})


class CancelOrderAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, id):
        order = get_object_or_404(Order, id=id, user=request.user)
        if order.status == 'CANCELLED':
            return Response({"error": "Already cancelled"}, status=400)

        item = order.orderitem_set.first()
        product = item.product
        product.stock += item.quantity
        product.save()

        order.status = 'CANCELLED'
        order.save()

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