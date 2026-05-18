import threading
import time
import random
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional



class Server:
    def __init__(self, server_id: int, max_concurrent: int = 5):
        # بنشئ خادم جديد برقم معين
        self.server_id = server_id
        self.max_concurrent = max_concurrent
        
        # قفل عشان ما يصير تضارب بين طلبين بنفس الوقت
        self.lock = threading.Lock()
        self.active_requests = 0      
        self.total_requests = 0       
        self.request_history = []     
        
        # صحة الخادم
        self.is_healthy = True
        self.last_heartbeat = time.time()
        self.health_check_lock = threading.Lock()
    
    def get_active_requests(self) -> int:
        
        with self.lock:
            return self.active_requests
    
    def get_total_requests(self) -> int:
       
        with self.lock:
            return self.total_requests
    
    def health_check(self) -> bool:
    
        with self.health_check_lock:
            current_time = time.time()
            print(f"[HEALTH] Checking health for Server {self.server_id}")
            if current_time - self.last_heartbeat > 5:
                self.is_healthy = False
            return self.is_healthy
    
    def heartbeat(self):
        
        with self.health_check_lock:
            self.last_heartbeat = time.time()
            self.is_healthy = True
    
    def handle_request(self, request_id: int) -> Dict[str, Any]:
    
        
        # بنزيد عدد الطلبات الشغالة  يصير تضارب
        with self.lock:
            self.active_requests += 1
            self.total_requests += 1
            start_time = time.time()
        
        # وقت المعالجة مختلف لكل طلب و عشوائي بين 0.05 و 0.8 ثانية
        processing_time = random.uniform(0.05, 0.8)
        time.sleep(processing_time)
       
        with self.lock:
            self.active_requests -= 1
            self.request_history.append({
                'request_id': request_id,
                'time': datetime.now().strftime("%H:%M:%S.%f")[:-3],
                'duration': round(processing_time, 3)
            })
        
        # بنرجع النتيجة
        return {
            'server': self.server_id,
            'request': request_id,
            'duration': round(processing_time, 3),
            'status': 'success'
        }



class LoadBalancer:
    def __init__(self, initial_servers: int = 3):
       
        
        self.servers: List[Server] = []
        self.server_lock = threading.Lock()
        
      
        for i in range(initial_servers):
            self.servers.append(Server(i+1))
        
    
        self.health_check_active = True
        self.health_thread = threading.Thread(target=self._health_check_loop, daemon=True)
        self.health_thread.start()
        
        
        self.auto_scaling_active = True
        self.scaling_thread = threading.Thread(target=self._auto_scaling_loop, daemon=True)
        self.scaling_thread.start()
    
    def _health_check_loop(self):

        while self.health_check_active:
            time.sleep(2)  
            
            with self.server_lock:
                for server in self.servers:
                    was_healthy = server.is_healthy
                    server.heartbeat()
                    
                  
                    if not was_healthy and server.is_healthy:
                        print(f"[HEALTH] Server {server.server_id} is BACK ONLINE")
                    
                    elif was_healthy and not server.is_healthy:
                        print(f"[HEALTH] Server {server.server_id} is UNHEALTHY")
    
    def _auto_scaling_loop(self):
        
        last_scale_up_time = 0      
        last_scale_down_time = 0  
        
        while self.auto_scaling_active:
            time.sleep(5)  
            
            with self.server_lock:
               
                healthy_servers = [s for s in self.servers if s.is_healthy]
                if not healthy_servers:
                    continue
                
                # نحسب متوسط الحمل كم طلب شغال بالخادم
                total_load = sum(s.get_active_requests() for s in healthy_servers)
                avg_load = total_load / len(healthy_servers)
                
                
                if avg_load > 3 and len(self.servers) < 10:
                    current_time = time.time()
                    if current_time - last_scale_up_time > 10:
                        new_id = len(self.servers) + 1
                        self.servers.append(Server(new_id))
                        print(f"[AUTO-SCALE] Added Server {new_id} (avg load={avg_load:.1f})")
                        last_scale_up_time = current_time
    
                elif avg_load < 1 and len(self.servers) > 3:
                    current_time = time.time()
                    if current_time - last_scale_down_time > 15:
                        removed = self.servers.pop()
                        print(f"[AUTO-SCALE] Removed Server {removed.server_id} (avg load={avg_load:.1f})")
                        last_scale_down_time = current_time
    
    def get_least_loaded_healthy_server(self) -> Optional[Server]:
    
        with self.server_lock:
          
            healthy_servers = [s for s in self.servers if s.health_check()]
            if not healthy_servers:
                return None
            
        
            return min(healthy_servers, key=lambda s: s.get_active_requests())
    
    def dispatch(self, request_id: int) -> Dict[str, Any]:
       
        selected_server = self.get_least_loaded_healthy_server()
        
        if selected_server is None:
            
            return {
                'request': request_id,
                'status': 'rejected',
                'reason': 'no_healthy_servers_available'
            }
        
       
        return selected_server.handle_request(request_id)
    
    def get_statistics(self) -> Dict[str, Any]:
       
        
        with self.server_lock:
            stats = {
                'total_servers': len(self.servers),
                'healthy_servers': sum(1 for s in self.servers if s.health_check()),
                'servers': []
            }
            
            total_requests = 0
            for server in self.servers:
                requests = server.get_total_requests()
                total_requests += requests
                stats['servers'].append({
                    'server_id': server.server_id,
                    'total_requests': requests,
                    'current_load': server.get_active_requests(),
                    'is_healthy': server.health_check(),
                    'percentage': 0
                })
            
            
                for s in stats['servers']:
                    s['percentage'] = round(s['total_requests'] / total_requests * 100, 1)
            
            return stats
    
    def shutdown(self):
        
        self.health_check_active = False
        self.auto_scaling_active = False


def run_simulation():

    
    print("\n" + "="*70)
    print("LOAD DISTRIBUTION SIMULATION - LEAST CONNECTIONS STRATEGY")
    print("="*70)
    print("\n[SYSTEM] Initializing Load Balancer...")
    lb = LoadBalancer(initial_servers=3)
    
    print("[SYSTEM] Sending 30 concurrent requests...\n")
    
    start_time = time.time()
    
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(lb.dispatch, i): i for i in range(1, 31)}
        
        for future in as_completed(futures):
            result = future.result()
            if result.get('status') == 'success':
                print(f"Request {result['request']} -> Server {result['server']} ({result['duration']}s)")
            else:
                print(f"Request {result.get('request')} -> {result.get('status').upper()}: {result.get('reason')}")
    
    total_time = time.time() - start_time
    stats = lb.get_statistics()
    
   
    print("SYSTEM STATISTICS")
    print("-"*20)
    print(f"\nTotal processing time: {total_time:.2f} seconds")
    print(f"Total servers: {stats['total_servers']}")
    print(f"Healthy servers: {stats['healthy_servers']}")
    
    print("\nServer distribution:")
    for s in stats['servers']:
        health = "HEALTHY" if s['is_healthy'] else "UNHEALTHY"
        print(f"\n   Server {s['server_id']} [{health}]:")
        print(f"   - Requests: {s['total_requests']} ({s.get('percentage', 0)}%)")
        print(f"   - Current load: {s['current_load']}")
    
    print("-"*20)
    print("CONCEPTS APPLIED")
    print("-"*20)
   
    
    lb.shutdown()


if __name__ == "__main__":
    run_simulation()