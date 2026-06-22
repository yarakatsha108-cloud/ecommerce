import threading, requests

TOKEN ="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzgyMTU5MzM1LCJpYXQiOjE3ODIxNTU3MzYsImp0aSI6IjY3YzUzZDhlZDQxYzRjNjhiMTE0NTc1YjdmNjc4YzRmIiwidXNlcl9pZCI6IjIifQ.d8uHGGcrao9xeFEmbHHLNgPCgX4PRbzuOx1VrxivUHg"
URL = "http://localhost:8000/api/orders/"

results = []
lock = threading.Lock()

def create_order():
    r = requests.post(URL, json={"product_id": 7, "quantity": 1}, headers=HEADERS)  # ← quantity: 1
    with lock:
        results.append(r.json())

threads = [threading.Thread(target=create_order) for _ in range(10)]
for t in threads: t.start()
for t in threads: t.join()

for r in results:
    print(r)