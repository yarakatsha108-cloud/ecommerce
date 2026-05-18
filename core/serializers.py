from rest_framework import serializers
from .models import Product , OrderItem , Order ,  DailySalesReport
from django.contrib.auth.models import User

class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=False, allow_blank=True)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password']

    def create(self, validated_data):
        email = validated_data.get('email', '')
        user = User.objects.create_user(
            username=validated_data['username'],
            email=email,
            password=validated_data['password']
        )
        user.is_active = True
        user.save()
        return user
    
    
class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = '__all__'



class OrderItemSerializer(serializers.ModelSerializer):
    product = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'quantity']


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, source='orderitem_set')

    class Meta:
        model = Order
        fields = ['id', 'user', 'created_at', 'items']
        
        
class CreateOrderSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField()        

class DailySalesReportSerializer(serializers.ModelSerializer):
    processing_status_display = serializers.CharField(
        source='get_status_display', 
        read_only=True
    )
    
    class Meta:
        model = DailySalesReport
        fields = [
            'id',
            'date',
            'total_orders',
            'completed_orders',
            'cancelled_orders',
            'pending_orders',
            'total_revenue',
            'average_order_value',
            'total_items_sold',
            'unique_customers',
            'status',
            'processing_status_display',
            'chunks_processed',
            'batch_size',
            'processing_time_seconds',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'total_orders',
            'total_revenue',
            'total_items_sold',
            'unique_customers',
            'created_at',
            'updated_at',
        ]
