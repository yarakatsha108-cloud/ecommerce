import threading, requests

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzgxNjAzMjE5LCJpYXQiOjE3ODE1OTk2MTksImp0aSI6IjJlNTlkM2UzYjVhNDRhNTZiOGM0N2VhNTk2YWQxMDJjIiwidXNlcl9pZCI6IjQifQ.9frrDEb75p9qLdOsN-koRfJqwamOL30Vlz7I0hdUli0"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
URL = "http://localhost:8000/api/orders/"

results = []
lock = threading.Lock()

def create_order():
    r = requests.post(URL, json={"product_id": 6, "quantity": 1}, headers=HEADERS)  # ← quantity: 1
    with lock:
        results.append(r.json())

threads = [threading.Thread(target=create_order) for _ in range(10)]
for t in threads: t.start()
for t in threads: t.join()

for r in results:
    print(r)