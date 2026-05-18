import logging
import time
from datetime import datetime, timedelta
from django.utils import timezone
from typing import List, Dict

from django.db.models import (
    Sum, Count, Q, F,

    #ExpressionWrapper للقيام بعمليات حسابية ضمن ال DB, DecimalField لتحديد نوع الحقل الناتج من العملية الحسابية
    ExpressionWrapper, DecimalField
)
from django.db import transaction

from .models import Order, OrderItem, Product, DailySalesReport
# من أجل مراقبة صحة النظام أثناء المعالجة
from .resource_manager import get_monitor

logger = logging.getLogger(__name__)


class BatchProcessor:
    def __init__(self, batch_size: int = 100):

        self.batch_size = batch_size
        self.monitor = get_monitor()
        self.processing_stats = {
            'total_processed': 0,
            'chunks_processed': 0,
            'start_time': None,
            'end_time': None,
        }

    def process_daily_sales(self, date: datetime.date = None) -> DailySalesReport:
        #اذا لم يتم تحديد تاريخ، نستخدم تاريخ أمس (افتراضي)
        if date is None:
            date = (datetime.now() - timedelta(days=1)).date()

        logger.info(f" بدء معالجة المبيعات لتاريخ: {date}")
        # self.processing_stats['start_time'] = time.time()
        self.processing_stats = {
            'total_processed': 0,
            'chunks_processed': 0,
            'start_time': time.time(),   
            'end_time': None,
        }
        
        #  update_or_create بدل try/except — آمن لو شغّلته مرتين (idempotent)
        #يتم منع ال duplicate من خلال unique=True في حقل date في نموذج DailySalesReport
        #ان created بتكون True إذا تم إنشاء تقرير جديد، False إذا تم تحديث تقرير موجود
        report, created = DailySalesReport.objects.update_or_create(
            date=date,
            defaults={
                'status': 'PROCESSING',
                'total_orders': 0,
                'completed_orders': 0,
                'cancelled_orders': 0,
                'pending_orders': 0,
                'total_items_sold': 0,
                'total_revenue': 0,
                'chunks_processed': 0,
            }
        )
        action = "جديد" if created else "إعادة معالجة"
        logger.info(f" تقرير {action} لتاريخ: {date}")
        #
        try:
            # الخطوة 1: جلب الطلبات
            orders = self._fetch_orders_for_date(date)
            total_orders = orders.count()
            logger.info(f" وجدت {total_orders} طلب لمعالجتها")

            # الخطوة 2: معالجة الطلبات على دفعات
            self._process_orders_in_batches(orders, report)

            # الخطوة 3: حساب الإحصائيات العامة بـ aggregate
            self._calculate_statistics(date, report)

            # إنهاء المعالجة
            #بسجل وقت الانتهاء في processing_stats، ثم نحسب وقت المعالجة الكلي ونحدث التقرير
            self.processing_stats['end_time'] = time.time()
            processing_time = self.processing_stats['end_time'] - self.processing_stats['start_time']
            #يتم تغيير الحالة ال Completed
            #update_fields لتحديث الحقول المحددة فقطء أكثر كفاءة من save() الكامل
            report.status = 'COMPLETED'
            report.processing_time_seconds = processing_time
            report.save(update_fields=['status', 'processing_time_seconds'])
            #طباعة إحصائيات المعالجة في اللوج لتحليل الأداء
            logger.info(f"   اكتملت المعالجة في {processing_time:.2f} ثانية")
            logger.info(f"   تم معالجة {self.processing_stats['chunks_processed']} دفعة")

            return report
        # في حالة حدوث أي خطأ أثناء المعالجة، يتم تسجيل الخطأ وتحديث حالة التقرير إلى 'FAILED'
        except Exception as e:
            logger.error(f"  خطأ في المعالجة: {str(e)}")
            report.status = 'FAILED'
            report.save(update_fields=['status'])
            raise

    def _fetch_orders_for_date(self, date: datetime.date):
        # جلب الطلبات لتاريخ محدد باستخدام نطاق وقت واعٍ بالـ timezone
        # نستخدم نطاق [start, end) لتفادي مشاكل الاختلاف في المناطق الزمنية
        #قمت بتعديل الاستعلام ليكون أكثر دقة في التعامل مع المناطق الزمنية، حيث يتم تحديد بداية اليوم ونهايته باستخدام timezone-aware datetimes، مما يضمن أن جميع الطلبات التي تم إنشاؤها في ذلك اليوم (بغض النظر عن المنطقة الزمنية) سيتم تضمينها بشكل صحيح في التقرير.
        start = timezone.make_aware(datetime.combine(date, datetime.min.time()))
        end = start + timedelta(days=1)
        return Order.objects.filter(
            created_at__gte=start,
            created_at__lt=end
        ).select_related('user').order_by('id')

    def _process_orders_in_batches(self, orders_queryset, report: DailySalesReport):
        total_orders = orders_queryset.count()
        #معالجة عدم وجود طلبات — لا داعي للمعالجة أو إنشاء دفعات
        if total_orders == 0:
            logger.info("لا توجد طلبات لمعالجتها")
            return

        # حساب عدد الدفعات
        # هنا يتم حساب عدد الدفعات بناءً على إجمالي الطلبات وحجم الدفعة المحدد، مع ضمان معالجة أي طلبات متبقية في دفعة إضافية إذا لم يكن العدد قابلاً للقسمة تمامًا
        #المعادلة (total_orders + batch_size - 1) // batch_size هي طريقة شائعة لحساب عدد الدفعات المطلوبة لمعالجة جميع الطلبات، حيث يتم إضافة (batch_size - 1) لضمان أن أي طلبات متبقية بعد القسمة الصحيحة ستؤدي إلى إنشاء دفعة إضافية
        #تم استخدام // بدلاً من / للحصول على نتيجة صحيحة (عدد صحيح) بدلاً من عدد عشري
        num_chunks = (total_orders + self.batch_size - 1) // self.batch_size
        # logger.info(f" سيتم معالجة {num_chunks} دفعة (حجم الدفعة: {self.batch_size})")

        #    نجلب الـ IDs فقط ونقسمها — أكفأ من slicing على queryset 
        # اخترنا انها ترجع list of ids لان اخف من جلب كل ال objects في الذاكرة دفعة واحدة، وبعدين نستخدمها لجلب ال objects المطلوبة لكل chunk داخل ال transaction
        #استخدمنا ال Slicing مباشرة لان بيكون اخف 
        order_ids = list(orders_queryset.values_list('id', flat=True))
        #loop على كل chunk من ال IDs، جلب ال objects المطلوبة لل chunk داخل transaction مستقلة، ثم معالجة كل chunk وتحديث التقرير بعد كل chunk
        #كل chunk_index عبارة عن رقم الدفعة الحالية (0-based index)، start_idx هو بداية ال IDs للدفعة الحالية، end_idx هو نهاية ال IDs للدفعة الحالية، chunk_ids هي قائمة ال IDs للدفعة الحالية
        for chunk_index in range(num_chunks):
            start_idx = chunk_index * self.batch_size
            end_idx = start_idx + self.batch_size
            #قمنا بتقسيم ال ids لتصبح اسرع واخف ولا تستهلك Ram كتير
            chunk_ids = order_ids[start_idx:end_idx]

            # جلب الطلبات الكاملة للـ chunk
            chunk = list(Order.objects.filter(id__in=chunk_ids))

            #    كل chunk داخل transaction مستقلة
            # هنا قمنا باستخدام ال transaction.atomic() حتى لا تحفظ بعض البيانات وبعضها لا
            with transaction.atomic():
                self._process_chunk(chunk, report)
            # تحديث إحصائيات المعالجة بعد كل chunk  هذا يسمح لنا بتتبع التقدم في الوقت الحقيقي في حالة وجود الكثير من الطلبات
            #أي هون بيتم حساب كم chunk تعالج
            self.processing_stats['chunks_processed'] += 1
            #اما هون يعني كم سجل تعالج 
            self.processing_stats['total_processed'] += len(chunk_ids)

            #    save واحدة بعد الـ transaction بـ update_fields فقط
            report.refresh_from_db()  # نجلب القيم المحدّثة بـ F() من الـ DB
            report.chunks_processed = self.processing_stats['chunks_processed']
            report.batch_size = self.batch_size
            report.save(update_fields=[
                'chunks_processed',
                'batch_size'
            ])

            # تقدم المعالجة
            progress = ((chunk_index + 1) / num_chunks) * 100
            logger.info(
                f" تقدم المعالجة: {progress:.1f}% "
                f"({self.processing_stats['total_processed']}/{total_orders})"
            )

            # فحص صحة النظام
            if not self.monitor.is_healthy(max_cpu=85, max_memory=85):
                logger.warning(" تحذير: الموارد عالية، قد يكون هناك بطء")

    # هنا نعالج chunks من ال order  ونحسب الاحصائيات ونخزن النتائج داخل DaiylSalesReport
    def _process_chunk(self, chunk: List[Order], report: DailySalesReport):
        #تحول قائمة الطلبات الى قائمة ids
        chunk_ids = [order.id for order in chunk]

        #  استعلام واحد لكل الـ items في الـ chunk — حل N+1
        # بدل المرور على كل item لحال واحد، بنستخدم aggregate لحساب مجموع الكميات لكل الطلبات في ال chunk في استعلام واحد، هذا يقلل بشكل كبير من عدد الاستعلامات ويزيد الأداء بشكل كبير خاصة مع عدد كبير من الطلبات
        items = OrderItem.objects.filter(
            order_id__in=chunk_ids
        ).aggregate(total=Sum('quantity'))
 
        total_qty = items['total'] or 0

        DailySalesReport.objects.filter(id=report.id).update(
            total_items_sold=F('total_items_sold') + total_qty
        )

         # حساب عدد الطلبات حسب الحالة
        completed = sum(1 for o in chunk if o.status == 'COMPLETED')
        cancelled = sum(1 for o in chunk if o.status == 'CANCELLED')
        pending   = sum(1 for o in chunk if o.status == 'PENDING')

        DailySalesReport.objects.filter(id=report.id).update(
            completed_orders=F('completed_orders') + completed,
            cancelled_orders=F('cancelled_orders') + cancelled,
            pending_orders=F('pending_orders')   + pending,
        )


        #  لا يوجد save هنا — الـ save تصير في _process_orders_in_batches

    # حساب الإحصائيات العامة
    def _calculate_statistics(self, date, report: DailySalesReport):
        # Use timezone-aware range to count orders for the given date
        start = timezone.make_aware(datetime.combine(date, datetime.min.time()))
        end = start + timedelta(days=1)
        daily_orders = Order.objects.filter(created_at__gte=start, created_at__lt=end)

        order_stats = daily_orders.aggregate(
            total=Count('id'),
            completed=Count('id', filter=Q(status='COMPLETED'))
        )

        report.total_orders = order_stats['total']
        completed_count = order_stats['completed']

        # unique customers عن طريق distinct على حقل user
        # يعطي صورة أوضح عن قاعدة العملاء النشطة في ذلك اليوم
        report.unique_customers = daily_orders.values('user').distinct().count()

        # استخدمنا فكرة ال unique customers عن طريق distinct على حقل user في جدول الطلبات، هذا يسمح لنا بحساب عدد العملاء الفريدين الذين قاموا بوضع طلبات في ذلك اليوم، بدلاً من حساب عدد الطلبات فقط، مما يعطي صورة أوضح عن قاعدة العملاء النشطة في ذلك اليوم.

        #  استعلام واحد بدل loop على كل الطلبات
        total_revenue = OrderItem.objects.filter(
            order__created_at__gte=start,
            order__created_at__lt=end,
            order__status='COMPLETED'
        ).aggregate(
            total=Sum(
                ExpressionWrapper(
                    F('quantity') * F('product__price'),
                    output_field=DecimalField()
                )
            )
        )['total'] or 0

        report.total_revenue = total_revenue

        if completed_count > 0:
            report.average_order_value = total_revenue / completed_count
 
        report.save(update_fields=[
            'total_orders', 'unique_customers',
            'total_revenue', 'average_order_value'
        ])
 
        logger.info(f" الإيرادات: {total_revenue}")
        logger.info(f" العملاء الفريدين: {report.unique_customers}")

    #هنا نقوم بتلخيص عملية المعالجة في حالة الحاجة لعرض إحصائيات المعالجة في الوقت الحقيقي أو لأغراض المراقبة والأداء
    def get_processing_stats(self) -> Dict:
        
        end = self.processing_stats.get('end_time') or time.time()
        start = self.processing_stats['start_time'] or time.time()
        return {
            'total_processed': self.processing_stats['total_processed'],
            'chunks_processed': self.processing_stats['chunks_processed'],
            'batch_size': self.batch_size,
            'processing_time': end - start,
        }
