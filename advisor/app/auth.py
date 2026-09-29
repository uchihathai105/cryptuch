"""Simple single-password login (optional).

Set a password and the whole dashboard and API require it. Leave it unset and nothing changes, which is
the right default when the app only listens on your own computer.

Where to set it (the environment variable wins):
  - environment variable ADVISOR_PASSWORD
  - "password" in config.local.json

A successful login sets a signed cookie that lasts 30 days. Changing the password logs everyone out.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import time

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import config as C

COOKIE = "advisor_session"
MAX_AGE = 30 * 24 * 3600
MAX_FAILS, FAIL_WINDOW = 5, 600          # 5 wrong passwords per 10 minutes, per client address
_fails: dict[str, list[float]] = {}
_KEY_FILE = C.DATA_DIR / "session.key"


def password() -> str:
    return os.environ.get("ADVISOR_PASSWORD") or str(C.local_config().get("password") or "")


def enabled() -> bool:
    return bool(password())


def _key() -> bytes:
    """Random signing key, created once and kept in data/ (never committed)."""
    try:
        return _KEY_FILE.read_bytes()
    except FileNotFoundError:
        key = secrets.token_bytes(32)
        _KEY_FILE.write_bytes(key)
        try:
            os.chmod(_KEY_FILE, 0o600)
        except OSError:
            pass
        return key


def _sign(expires: int) -> str:
    # the password is part of the key, so changing it invalidates every existing session
    key = _key() + password().encode()
    return hmac.new(key, str(expires).encode(), hashlib.sha256).hexdigest()


def make_token() -> str:
    exp = int(time.time()) + MAX_AGE
    return f"{exp}.{_sign(exp)}"


def valid_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    exp, sig = token.split(".", 1)
    try:
        expires = int(exp)
    except ValueError:
        return False
    return expires > time.time() and hmac.compare_digest(sig, _sign(expires))


def _client(request: Request) -> str:
    return request.client.host if request.client else "?"


def _locked(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
    _fails[ip] = recent
    return len(recent) >= MAX_FAILS


def is_secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


async def guard(request: Request, call_next):
    """Middleware: let logged-in visitors through, send everyone else to the login page."""
    if not enabled() or request.url.path in ("/login", "/logout"):
        return await call_next(request)
    if valid_token(request.cookies.get(COOKIE)):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Login required"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def login_page() -> HTMLResponse:
    return HTMLResponse(_PAGE)


async def login(request: Request) -> JSONResponse:
    ip = _client(request)
    if _locked(ip):
        return JSONResponse({"detail": "Too many attempts. Wait 10 minutes and try again."}, status_code=429)
    try:
        body = await request.json()
    except Exception:
        body = {}
    given = str(body.get("password", "")) if isinstance(body, dict) else ""
    if not enabled() or not hmac.compare_digest(given.encode(), password().encode()):
        _fails.setdefault(ip, []).append(time.time())
        await asyncio.sleep(1)           # slows down guessing
        return JSONResponse({"detail": "Wrong password"}, status_code=401)
    _fails.pop(ip, None)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, make_token(), max_age=MAX_AGE, httponly=True, samesite="lax",
                    secure=is_secure(request), path="/")
    return resp


def logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --text:#15181d; --muted:#667085; --line:#d5d9e0; --accent:#2563eb; --bad:#c0392b; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0e1116; --card:#171b22; --text:#e6e9ee; --muted:#9aa4b2; --line:#2a303a; --accent:#4c8dff; --bad:#ff6b5e; }
  }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; background:var(--bg); color:var(--text);
         font:16px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; padding:16px; }
  form { width:100%; max-width:340px; background:var(--card); border:1px solid var(--line); border-radius:12px; padding:24px; }
  h1 { font-size:18px; margin:0 0 16px; }
  input, button { width:100%; font:inherit; padding:10px 12px; border-radius:8px; }
  input { border:1px solid var(--line); background:transparent; color:inherit; }
  input:focus-visible, button:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  button { margin-top:12px; border:0; background:var(--accent); color:#fff; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.6; cursor:default; }
  #msg { min-height:1.4em; margin:10px 0 0; font-size:14px; color:var(--bad); }
</style></head>
<body>
<form id="f">
  <h1>Crypto Trading Advisor</h1>
  <label for="p" style="font-size:14px;color:var(--muted)">Password</label>
  <input id="p" type="password" autocomplete="current-password" autofocus required>
  <button id="b" type="submit">Sign in</button>
  <p id="msg" role="alert"></p>
</form>
<script>
  const f = document.getElementById("f"), msg = document.getElementById("msg"), b = document.getElementById("b");
  f.addEventListener("submit", async (e) => {
    e.preventDefault(); msg.textContent = ""; b.disabled = true;
    try {
      const r = await fetch("/login", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: document.getElementById("p").value }) });
      if (r.ok) { location.href = "/"; return; }
      const d = await r.json().catch(() => ({}));
      msg.textContent = d.detail || "Could not sign in";
    } catch { msg.textContent = "Can't reach the server"; }
    b.disabled = false;
  });
</script>
</body></html>
"""
