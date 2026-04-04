"""Authentication module: Google OAuth, email/password, session cookies, trial gating."""

from __future__ import annotations

import os
import uuid
import secrets

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import bcrypt as _bcrypt
import httpx

import database as db

router = APIRouter()

# ── Config ───────────────────────────────────────────────────────────

SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

MAX_TRIAL_ANALYSES = 2
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 days
TRIAL_MAX_AGE = 90 * 24 * 3600    # 90 days

_serializer = URLSafeTimedSerializer(SECRET_KEY)


# ── Session helpers (used by app.py too) ─────────────────────────────

def get_current_user(request: Request) -> dict | None:
    """Read session cookie → return user dict or None."""
    token = request.cookies.get("session")
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return db.get_user(data.get("uid", ""))


def get_trial_id(request: Request) -> str | None:
    """Read trial_id cookie."""
    return request.cookies.get("trial_id")


def get_trial_remaining(request: Request) -> int:
    """How many trial analyses remain for this browser."""
    tid = get_trial_id(request)
    if not tid:
        return MAX_TRIAL_ANALYSES
    trial = db.get_trial(tid)
    if not trial:
        return MAX_TRIAL_ANALYSES
    return max(0, MAX_TRIAL_ANALYSES - trial["analyses_count"])


def check_auth_or_trial(request: Request) -> tuple[dict | None, int]:
    """Returns (user, trial_remaining). If user is set, trial doesn't matter."""
    user = get_current_user(request)
    if user:
        return user, 0
    return None, get_trial_remaining(request)


def use_trial(request: Request):
    """Consume one trial analysis."""
    tid = get_trial_id(request)
    if tid:
        db.increment_trial(tid)


def _set_session_cookie(response, user_id: str):
    token = _serializer.dumps({"uid": user_id})
    response.set_cookie("session", token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")


# ── Routes: /api/me ──────────────────────────────────────────────────

@router.get("/api/me")
async def me(request: Request):
    user = get_current_user(request)
    if user:
        return JSONResponse({
            "authenticated": True,
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "avatar_url": user.get("avatar_url", ""),
            "plan": user.get("plan", "free"),
            "analyses_this_month": user.get("analyses_this_month", 0),
        })

    # Ensure trial_id cookie exists
    tid = get_trial_id(request)
    remaining = MAX_TRIAL_ANALYSES
    resp_data = {"authenticated": False}

    if tid:
        trial = db.get_trial(tid)
        if trial:
            remaining = max(0, MAX_TRIAL_ANALYSES - trial["analyses_count"])
    else:
        tid = str(uuid.uuid4())
        db.create_trial(tid)

    resp_data["trial"] = remaining > 0
    resp_data["remaining"] = remaining

    response = JSONResponse(resp_data)
    # Always set/refresh cookie
    response.set_cookie("trial_id", tid, max_age=TRIAL_MAX_AGE, httponly=True, samesite="lax")
    return response


# ── Routes: Google OAuth ─────────────────────────────────────────────

@router.get("/auth/google")
async def google_login(request: Request):
    if not GOOGLE_CLIENT_ID:
        return JSONResponse({"error": "Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."}, status_code=500)

    redirect_uri = BASE_URL + "/auth/google/callback"
    params = (
        f"client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        "&scope=openid+email+profile"
        "&access_type=offline"
        "&prompt=consent"
    )
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@router.get("/auth/google/callback")
async def google_callback(code: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse("/auth.html?error=oauth_denied")

    redirect_uri = BASE_URL + "/auth/google/callback"

    async with httpx.AsyncClient() as client:
        # Exchange code for tokens
        token_resp = await client.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        if token_resp.status_code != 200:
            return RedirectResponse("/auth.html?error=oauth_failed")
        tokens = token_resp.json()

        # Get user info
        info_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        if info_resp.status_code != 200:
            return RedirectResponse("/auth.html?error=oauth_failed")
        info = info_resp.json()

    google_id = info["id"]
    email = info.get("email", "")
    name = info.get("name", "")
    avatar = info.get("picture", "")

    # Find or create user
    user = db.get_user_by_google_id(google_id)
    if not user:
        user = db.get_user_by_email(email)
        if user:
            db.link_google_id(user["id"], google_id)
        else:
            user_id = str(uuid.uuid4())
            db.create_user(user_id, email, name, avatar, google_id=google_id)
            user = db.get_user(user_id)

    response = RedirectResponse("/app.html")
    _set_session_cookie(response, user["id"])
    return response


# ── Routes: Email / Password ─────────────────────────────────────────

@router.post("/auth/register")
async def register(email: str = Form(...), password: str = Form(...), name: str = Form("")):
    if not email or not password:
        return JSONResponse({"error": "Email and password are required."}, status_code=400)
    if len(password) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters."}, status_code=400)

    existing = db.get_user_by_email(email)
    if existing:
        return JSONResponse({"error": "An account with this email already exists."}, status_code=409)

    user_id = str(uuid.uuid4())
    pw_hash = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()
    db.create_user(user_id, email, name or email.split("@")[0], password_hash=pw_hash)

    resp = JSONResponse({"ok": True, "redirect": "/app.html"})
    _set_session_cookie(resp, user_id)
    return resp


@router.post("/auth/login")
async def login(email: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_email(email)
    if not user or not user.get("password_hash"):
        return JSONResponse({"error": "Invalid email or password."}, status_code=401)

    if not _bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return JSONResponse({"error": "Invalid email or password."}, status_code=401)

    resp = JSONResponse({"ok": True, "redirect": "/app.html"})
    _set_session_cookie(resp, user["id"])
    return resp


@router.post("/auth/logout")
async def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("session")
    return resp
