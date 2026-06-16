import threading
import requests

URL = "http://127.0.0.1:8000/api/orders/"
TOKEN ="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzgxNjAzMjE5LCJpYXQiOjE3ODE1OTk2MTksImp0aSI6IjJlNTlkM2UzYjVhNDRhNTZiOGM0N2VhNTk2YWQxMDJjIiwidXNlcl9pZCI6IjQifQ.9frrDEb75p9qLdOsN-koRfJqwamOL30Vlz7I0hdUli0"
print("URL is:", URL)
def order():
    try:
        response = requests.post(
            URL,
            json={
                "product_id": 6,
                "quantity": 1
            },
            headers={
                "Authorization": f"Bearer {TOKEN}"
            },
            timeout=30,
            proxies={"http": None, "https": None}
        )
        print(response.status_code, response.text)
    except Exception as e:
        print("Error:", e)

threads = []

for _ in range(3):
    t = threading.Thread(target=order)
    t.start()
    threads.append(t)

for t in threads:
    t.join()

