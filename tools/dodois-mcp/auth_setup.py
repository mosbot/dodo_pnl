"""
Первичная авторизация в Dodo IS (OAuth2 Authorization Code + PKCE).

Запуск:  python auth_setup.py
1. Скрипт поднимет локальный сервер на http://localhost:8400/callback
   (этот redirect URI должен быть зарегистрирован в вашем приложении Dodo IS).
2. Откроет браузер со страницей входа Dodo IS — войдите под учёткой,
   у которой есть доступ к нужным заведениям.
3. Токены сохранятся в tokens.json. Дальше server.py обновляет их сам
   по refresh_token (нужен scope offline_access).
"""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

AUTH_URL = os.getenv("DODO_AUTH_URL", "https://auth.dodois.io")
CLIENT_ID = os.environ["DODO_CLIENT_ID"]
CLIENT_SECRET = os.getenv("DODO_CLIENT_SECRET", "")
SCOPES = os.getenv("DODO_SCOPES", "openid offline_access")
REDIRECT_URI = os.getenv("DODO_REDIRECT_URI", "https://localhost:5001")
TOKENS_FILE = Path(os.getenv("DODO_TOKENS_FILE", Path(__file__).parent / "tokens.json"))

code_holder: dict = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        if "code" in params:
            code_holder["code"] = params["code"][0]
            body = "<h2>Готово! Можно закрыть вкладку.</h2>".encode()
        else:
            body = f"<h2>Ошибка: {qs}</h2>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()


    auth_request = (
        f"{AUTH_URL}/connect/authorize?"
        + urllib.parse.urlencode(
            {
                "client_id": CLIENT_ID,
                "response_type": "code",
                "redirect_uri": REDIRECT_URI,
                "scope": SCOPES,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": secrets.token_urlsafe(16),
            }
        )
    )
    print("1) Откройте этот URL в браузере и войдите в Dodo IS:")
    print()
    print(auth_request)
    print()
    print("2) После входа браузер уйдёт на https://localhost:5001/?code=...")
    print("   Страница НЕ откроется — это нормально. Скопируйте ПОЛНЫЙ адрес")
    print("   из адресной строки браузера и вставьте сюда.")
    pasted = input("Вставьте URL: ").strip()
    q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
    if "code" not in q:
        raise SystemExit(f"В URL нет ?code=. Получено: {pasted[:120]}")
    code_holder["code"] = q["code"][0]

    print("Код получен, обмениваю на токены...")
    data = {
        "grant_type": "authorization_code",
        "code": code_holder["code"],
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    }
    if CLIENT_SECRET:
        data["client_secret"] = CLIENT_SECRET
    resp = httpx.post(f"{AUTH_URL}/connect/token", data=data, timeout=30)
    resp.raise_for_status()
    tokens = resp.json()
    tokens["obtained_at"] = int(time.time())
    TOKENS_FILE.write_text(json.dumps(tokens, ensure_ascii=False, indent=2))
    print(f"Токены сохранены в {TOKENS_FILE}")
    if "refresh_token" not in tokens:
        print("ВНИМАНИЕ: refresh_token не выдан — добавьте scope offline_access "
              "в DODO_SCOPES и в настройки приложения.")


if __name__ == "__main__":
    main()
