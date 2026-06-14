from rest_framework.routers import DefaultRouter
from .views import register
from django.urls import path
from .views import DashboardStatsAPIView, CacheDiagnosticsAPIView
from .views import (
    ProductListCreateAPIView, ProductDetailAPIView, OrderDetailAPIView, OrderListCreateAPIView,
    PayOrderAPIView, CancelOrderAPIView, CompleteOrderAPIView,
    DailySalesReportListAPIView, DailySalesReportDetailAPIView,
    ProcessDailySalesAPIView, SalesReportStatsAPIView,
    DashboardStatsAPIView, CacheDiagnosticsAPIView,  
    register,
)
from .monitoring_views import (
    resource_status_view,
    capacity_status_view,
    system_health_view,
    reset_capacity_stats_view,
    async_queue_status_view,
)

urlpatterns = [
    path('products/', ProductListCreateAPIView.as_view()),
    path('products/<int:id>/', ProductDetailAPIView.as_view()),
    path('register/', register),
    path('orders/', OrderListCreateAPIView.as_view()),
    path('orders/<int:id>/', OrderDetailAPIView.as_view()),
    path('orders/<int:id>/pay/', PayOrderAPIView.as_view()),
    path('orders/<int:id>/complete/', CompleteOrderAPIView.as_view()),
    path('orders/<int:id>/cancel/', CancelOrderAPIView.as_view()),
    
    path('reports/', DailySalesReportListAPIView.as_view()),
    path('reports/<str:date>/', DailySalesReportDetailAPIView.as_view()),
    path('reports/process/', ProcessDailySalesAPIView.as_view()),
    path('reports/stats/', SalesReportStatsAPIView.as_view()),
    
    path('admin/resources/', resource_status_view),
    path('admin/capacity/', capacity_status_view),
    path('admin/health/', system_health_view),
    path('admin/capacity/reset/', reset_capacity_stats_view),
    path('admin/async-queue/', async_queue_status_view),

    path('dashboard/stats/',              DashboardStatsAPIView.as_view()),
    path('admin/cache/',                  CacheDiagnosticsAPIView.as_view()),
]
    