import json
import requests
import urllib.parse
import websocket

# ======================
# 🔧 Configuratie
# ======================
TRACCAR_URL = "1.lifeguardtracking.nl:8082"
EMAIL = "django-Cellular"
PASSWORD = "django-Cellular"

# ======================
# 🔑 Haal sessiecookie op
# ======================
def get_session_key(email, password, url):
    login_url = f'http://{url}/api/session'
    params = urllib.parse.urlencode({'email': email, 'password': password})
    headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}

    with requests.Session() as session:
        response = session.post(login_url, data=params, headers=headers)
        if response.status_code == 200:
            cookies = session.cookies.get_dict()
            return cookies.get('JSESSIONID')
        print(f"[ERROR] Login mislukt: {response.status_code} - {response.text}")
        return None

# ======================
# 🌐 WebSocket handlers
# ======================
def on_open(ws):
    print("✅ WebSocket verbonden")

def on_close(ws, code, msg):
    print(f"❌ WebSocket verbroken: {code} - {msg}")

def on_error(ws, err):
    print(f"[WebSocket Fout]: {err}")

def on_message(ws, message):
    try:
        data = json.loads(message)
        print()
        for key, items in data.items():
            if isinstance(items, list):
                for item in items:
                    print(f"📦 {key}:", json.dumps(item))
            else:
                print("    ↳ (geen lijst)")
    except Exception as e:
        print(f"[JSON Fout]: {e} - Inhoud: {message}")


# ======================
# ▶️ Start
# ======================
if __name__ == "__main__":
    session_key = get_session_key(EMAIL, PASSWORD, TRACCAR_URL)
    if not session_key:
        exit(1)

    ws_url = f"ws://{TRACCAR_URL}/api/socket"
    headers = {'Cookie': f'JSESSIONID={session_key}'}

    ws = websocket.WebSocketApp(
        ws_url,
        header=headers,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    print("🚀 Verbinden met WebSocket...")
    ws.run_forever()
