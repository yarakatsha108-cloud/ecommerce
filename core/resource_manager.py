import psutil
import threading
import time
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ResourceMetrics:
    
    cpu_percent: float
    memory_percent: float
    memory_mb: float
    active_threads: int
    available_memory_mb: float
    cpu_count: int
    timestamp: float


class ResourceMonitor:
    
    
    def __init__(self):
        self.process = psutil.Process()
        self.metrics_history = []
        self.max_history = 100
        self.lock = threading.RLock()
        
    def get_metrics(self) -> ResourceMetrics:
        
        with self.lock:
            try:
                cpu_percent = self.process.cpu_percent(interval=0.1)
                memory_info = self.process.memory_info()
                memory_percent = self.process.memory_percent()
                
                virtual_memory = psutil.virtual_memory()
                available_memory_mb = virtual_memory.available / (1024 * 1024)
                memory_mb = memory_info.rss / (1024 * 1024)
                
                metrics = ResourceMetrics(
                    cpu_percent=cpu_percent,
                    memory_percent=memory_percent,
                    memory_mb=memory_mb,
                    active_threads=threading.active_count(),
                    available_memory_mb=available_memory_mb,
                    cpu_count=psutil.cpu_count(),
                    timestamp=time.time()
                )
                
                self.metrics_history.append(metrics)
                if len(self.metrics_history) > self.max_history:
                    self.metrics_history.pop(0)
                
                return metrics
            except Exception as e:
                logger.error(f"Error getting metrics: {e}")
                return self._get_default_metrics()
    
    def _get_default_metrics(self) -> ResourceMetrics:

        return ResourceMetrics(
            cpu_percent=0.0,
            memory_percent=0.0,
            memory_mb=0.0,
            active_threads=threading.active_count(),
            available_memory_mb=0.0,
            cpu_count=1,
            timestamp=time.time()
        )
    
    def get_average_metrics(self, seconds: int = 30) -> Optional[ResourceMetrics]:

        with self.lock:
            if not self.metrics_history:
                return None
            
            current_time = time.time()
            relevant_metrics = [
                m for m in self.metrics_history 
                if current_time - m.timestamp <= seconds
            ]
            
            if not relevant_metrics:
                return None
            
            avg_cpu = sum(m.cpu_percent for m in relevant_metrics) / len(relevant_metrics)
            avg_memory = sum(m.memory_percent for m in relevant_metrics) / len(relevant_metrics)
            latest = relevant_metrics[-1]
            
            return ResourceMetrics(
                cpu_percent=avg_cpu,
                memory_percent=avg_memory,
                memory_mb=latest.memory_mb,
                active_threads=latest.active_threads,
                available_memory_mb=latest.available_memory_mb,
                cpu_count=latest.cpu_count,
                timestamp=current_time
            )
    
    def is_healthy(self, 
                    max_cpu: float = 80.0,
                    max_memory: float = 85.0) -> bool:
        """التحقق من صحة الموارد"""
        metrics = self.get_metrics()
        return (metrics.cpu_percent < max_cpu and 
                metrics.memory_percent < max_memory)
    
    def get_resource_status(self) -> dict:

        metrics = self.get_metrics()
        avg_metrics = self.get_average_metrics(30)
        
        return {
            'current': {
                'cpu_percent': round(metrics.cpu_percent, 2),
                'memory_percent': round(metrics.memory_percent, 2),
                'memory_mb': round(metrics.memory_mb, 2),
                'active_threads': metrics.active_threads,
                'available_memory_mb': round(metrics.available_memory_mb, 2),
            },
            'average_30s': {
                'cpu_percent': round(avg_metrics.cpu_percent, 2) if avg_metrics else 0,
                'memory_percent': round(avg_metrics.memory_percent, 2) if avg_metrics else 0,
            },
            'system': {
                'cpu_count': metrics.cpu_count,
                'total_system_memory_gb': round(psutil.virtual_memory().total / (1024**3), 2),
            },
            'is_healthy': self.is_healthy(),
        }


# Global instance
_monitor = ResourceMonitor()

def get_monitor() -> ResourceMonitor:

    return _monitor
