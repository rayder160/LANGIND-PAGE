import requests, json, sys
url = 'http://127.0.0.1:8000/api/auth/token'
payload = {
    'username': 'ceo@proxdeep.com',
    'password': 'ceo2026'
}
headers = {'Content-Type': 'application/x-www-form-urlencoded'}
resp = requests.post(url, data=payload, headers=headers)
print('Status code:', resp.status_code)
print('Response body:', resp.text)
