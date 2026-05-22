import requests, json

BASE_URL = "http://localhost:8000"

def login(username, password):
    resp = requests.post(f"{BASE_URL}/api/auth/token", data={"username": username, "password": password})
    resp.raise_for_status()
    return resp.json()["access_token"]

def get_org_tree(token):
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{BASE_URL}/api/org/tree", headers=headers)
    resp.raise_for_status()
    return resp.json()

def get_org_context(token, view="default"):
    headers = {"Authorization": f"Bearer {token}"}
    params = {"view": view}
    resp = requests.get(f"{BASE_URL}/api/org/context", headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()

if __name__ == "__main__":
    token = login("ceo@proxdeep.com", "ceo2026")
    print("Login token obtained")
    tree = get_org_tree(token)
    print("--- Org Tree ---")
    print(json.dumps(tree, indent=2, ensure_ascii=False))
    context = get_org_context(token, view="default")
    print("--- Org Context ---")
    print(json.dumps(context, indent=2, ensure_ascii=False))
