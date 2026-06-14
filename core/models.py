from django.db import models
from django.contrib.auth.models import User 

class Product(models.Model):
    name = models.CharField(max_length=225)
    stock = models.IntegerField()
    price = models.DecimalField(max_digits=10 , decimal_places=2)
    
    
class Order(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('PAID', 'Paid'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]
    user = models.ForeignKey(User , on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
        
class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE)
    product = models.ForeignKey(Product , on_delete=models.CASCADE)
    quantity = models.IntegerField()
    
class DailySalesReport(models.Model):    
    date = models.DateField(unique=True)
    total_orders = models.IntegerField(default=0)
    completed_orders = models.IntegerField(default=0)
    cancelled_orders = models.IntegerField(default=0)
    pending_orders = models.IntegerField(default=0)
    
    #  البيانات المالية
    #total_revenue هو مجموع الارباح
    total_revenue = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    # average_order_value هو متوسط قيمة الطلب الواحد
    average_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    total_items_sold = models.IntegerField(default=0)
    unique_customers = models.IntegerField(default=0)
    
    status = models.CharField(
        max_length=20,
        choices=[
            ('PENDING', 'قيد المعالجة'),
            ('PROCESSING', 'جاري المعالجة'),
            ('COMPLETED', 'مكتمل'),
            ('FAILED', 'فشل'),
        ],
        default='PENDING'
    )
    
    chunks_processed = models.IntegerField(default=0)
    batch_size = models.IntegerField(default=100)
    processing_time_seconds = models.FloatField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-date']
        verbose_name = "تقرير المبيعات اليومي"
        verbose_name_plural = "تقارير المبيعات اليومية"
    
    def __str__(self):
        return f"تقرير {self.date} - {self.status}"
            

            
