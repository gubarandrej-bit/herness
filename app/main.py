"""FastAPI-приложение herness — основной модуль и маршруты."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, Depends, HTTPException, UploadFile, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
import pathlib

from . import config
from .db import db, init_db, utcnow, row_to_dict, rows_to_dicts, log_action
from .security import hash_password, verify_password, create_session, validate_session, delete_session
from . import ntd_loader

# Размещение статических файлов и шаблонов
BASE_DIR = pathlib.Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
from . import parsers
from . import check_engine
from . import report_gen

app = FastAPI(title="herness", version="1.0.0")

# --- middlewares -----------------------------------------------------
app.add_middleware(SessionMiddleware, secret_key=config.get_secret(), session_cookie=config.SESSION_COOKIE)

# Статические файлы (CSS, JS)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Шаблоны
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# --- зависимости -----------------------------------------------------

def require_admin(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user or user["role"] != "admin":
        raise HTTPException(403, "Требуются права администратора")
    return user


def get_db():
    from .db import db as ctx
    with ctx() as conn:
        yield conn


# --- helpers ---------------------------------------------------------

def now_utc_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# --- lifespan & init -------------------------------------------------

@app.on_event("startup")
def startup():
    config.ensure_dirs()
    init_db(config.DEFAULT_ADMIN_LOGIN, config.DEFAULT_ADMIN_PASSWORD)
    ntd_loader.ensure_ntd_table()


# --- health / debug --------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


# --- auth ------------------------------------------------------------

@app.get("/api/me")
def api_me(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    return {"ok": True, "user": {k: v for k, v in user.items() if k != "expires_at"}}


@app.post("/api/auth/login")
def api_login(response: Response, login: str = Form(...), password: str = Form(...)):
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE login = ? AND is_active = 1",
            (login.strip(),),
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise HTTPException(401, "Неверный логин или пароль")
        token = create_session(row["id"])
        conn.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (utcnow(), row["id"]),
        )
        log_action(conn, user_id=row["id"], login=row["login"], action="login")
    response.set_cookie(
        key=config.SESSION_COOKIE,
        value=token,
        httponly=True,
        max_age=config.SESSION_TTL_SECONDS,
        samesite="lax",
    )
    return {"ok": True, "token": token}


@app.post("/api/auth/logout")
def api_logout(response: Response, request: Request):
    token = request.cookies.get(config.SESSION_COOKIE)
    if token:
        delete_session(token)
    response.delete_cookie(config.SESSION_COOKIE)
    return {"ok": True}


# --- user management -------------------------------------------------

@app.get("/api/admin/users")
def list_users(_admin: dict = Depends(require_admin)):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, login, role, is_active, full_name, created_at, last_login_at FROM users ORDER BY id"
        ).fetchall()
        return {"users": rows_to_dicts(rows)}


@app.post("/api/admin/users")
def create_user(
    login: str = Form(...), password: str = Form(...), role: str = Form("user"),
    full_name: str = Form(""), _admin: dict = Depends(require_admin),
):
    with db() as conn:
        try:
            conn.execute(
                """INSERT INTO users (login, password_hash, role, is_active, full_name, created_at)
                   VALUES (?, ?, ?, 1, ?, ?)""",
                (login.strip(), hash_password(password), role, full_name.strip(), utcnow()),
            )
            log_action(conn, user_id=_admin["id"], login=_admin["login"], action="create_user", target=login)
        except Exception as e:
            raise HTTPException(400, f"Ошибка создания: {e}")
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, _admin: dict = Depends(require_admin)):
    with db() as conn:
        conn.execute("DELETE FROM users WHERE id = ? AND role != 'admin'", (user_id,))
        log_action(conn, user_id=_admin["id"], login=_admin["login"], action="delete_user", target=str(user_id))
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/reset-password")
def reset_password(
    user_id: int, password: str = Form(...), _admin: dict = Depends(require_admin),
):
    with db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password), user_id),
        )
        log_action(
            conn, user_id=_admin["id"], login=_admin["login"],
            action="reset_password", target=str(user_id),
        )
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/toggle-block")
def toggle_block(user_id: int, _admin: dict = Depends(require_admin)):
    with db() as conn:
        row = conn.execute("SELECT is_active FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Пользователь не найден")
        new_val = 0 if row["is_active"] else 1
        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_val, user_id))
        log_action(
            conn, user_id=_admin["id"], login=_admin["login"],
            action="toggle_block", target=str(user_id),
        )
    return {"ok": True, "is_active": new_val}


# --- NTD -------------------------------------------------------------

@app.get("/api/ntd")
def list_ntd(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401)
    with db() as conn:
        rows = conn.execute(
            "SELECT id, code, title, revision, status, superseded_by, pages, checked_at, notes, created_at FROM ntd_documents ORDER BY code"
        ).fetchall()
        return {"documents": rows_to_dicts(rows)}


@app.post("/api/ntd/{doc_id}/recheck")
def recheck_ntd(doc_id: int, request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401)
    with db() as conn:
        ntd_loader.check_currency(conn, doc_id)
        log_action(conn, user_id=user["id"], login=user["login"], action="recheck_ntd", target=str(doc_id))
    return {"ok": True}


@app.post("/api/ntd/refresh")
def refresh_ntd(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    ntd_loader.ensure_ntd_table()
    return {"ok": True}


# --- upload & check --------------------------------------------------

@app.post("/api/checks")
def create_check(
    request: Request,
    name: str = Form(...),
    system_kind: str = Form("mixed"),
    mode: str = Form("local"),
):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401)
    with db() as conn:
        cur = conn.execute(
            """INSERT INTO checks (name, mode, system_kind, status, created_by, created_at)
               VALUES (?, ?, ?, 'created', ?, ?)""",
            (name.strip(), mode, system_kind, user["id"], utcnow()),
        )
        check_id = cur.lastrowid
        log_action(conn, user_id=user["id"], login=user["login"], action="create_check", target=str(check_id))
        return {"check_id": check_id}


@app.post("/api/checks/{check_id}/upload")
async def upload_file(check_id: int, request: Request, file: UploadFile):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401)
    ext = Path(file.filename).suffix.lower() if file.filename else ".bin"
    if ext not in config.ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Формат {ext} не поддерживается")
    import hashlib

    sha = hashlib.sha256()
    content = await file.read()
    sha.update(content)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    stored_path = str(config.UPLOADS_DIR / stored_name)
    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    (config.UPLOADS_DIR / stored_name).write_bytes(content)

    kinds_map = {
        "кабель": "cable_journal", "журнал": "cable_journal",
        "спецификация": "specification", "специф": "specification",
        "план": "plan", "расчёт": "calculation", "расчет": "calculation",
        "ип": "power_supply", "блок": "power_supply",
    }
    fname_lower = (file.filename or "").lower()
    kind = "other"
    for keyword, k in kinds_map.items():
        if keyword in fname_lower:
            kind = k
            break

    with db() as conn:
        conn.execute(
            """INSERT INTO uploads (check_id, original_name, stored_path, extension, size_bytes, sha256, kind, uploaded_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (check_id, file.filename or "unnamed", stored_path, ext, len(content), sha.hexdigest(), kind, user["id"], utcnow()),
        )
        conn.execute(
            "UPDATE checks SET status = 'created' WHERE id = ? AND status = 'created'",
            (check_id,),
        )
    return {"ok": True, "stored_name": stored_name, "kind": kind}


@app.get("/api/checks")
def list_checks(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401)
    with db() as conn:
        rows = conn.execute(
            """SELECT c.*, u.login AS created_by_login
               FROM checks c LEFT JOIN users u ON u.id = c.created_by
               ORDER BY c.id DESC LIMIT 50""",
        ).fetchall()
        return {"checks": rows_to_dicts(rows)}


@app.get("/api/checks/{check_id}")
def get_check(check_id: int, request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401)
    with db() as conn:
        check = row_to_dict(conn.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone())
        if not check:
            raise HTTPException(404)
        uploads = rows_to_dicts(conn.execute("SELECT * FROM uploads WHERE check_id = ?", (check_id,)).fetchall())
        findings = rows_to_dicts(conn.execute("SELECT * FROM findings WHERE check_id = ? ORDER BY id", (check_id,)).fetchall())
        steps = rows_to_dicts(conn.execute("SELECT * FROM check_steps WHERE check_id = ? ORDER BY sort_order", (check_id,)).fetchall())
        return {"check": check, "uploads": uploads, "findings": findings, "steps": steps}


@app.post("/api/checks/{check_id}/run")
def run_check(check_id: int, request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401)
    with db() as conn:
        check = conn.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()
        if not check:
            raise HTTPException(404)
        conn.execute(
            "UPDATE checks SET status = 'running', started_at = ? WHERE id = ?",
            (utcnow(), check_id),
        )
    try:
        engine = check_engine.CheckEngine(check_id)
        result = engine.run()
        with db() as conn:
            conn.execute(
                """UPDATE checks SET status = 'done', finished_at = ?, summary = ? WHERE id = ?""",
                (utcnow(), json.dumps(result.get("summary", {}), ensure_ascii=False), check_id),
            )
            log_action(conn, user_id=user["id"], login=user["login"], action="run_check", target=str(check_id))
        return {"ok": True, "result": result}
    except Exception as e:
        with db() as conn:
            conn.execute(
                "UPDATE checks SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                (str(e), utcnow(), check_id),
            )
        return {"ok": False, "error": str(e)}


# --- reports ---------------------------------------------------------

@app.get("/api/checks/{check_id}/report/docx")
def download_report_docx(check_id: int, request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401)
    path = report_gen.generate_docx(check_id)
    if not path:
        raise HTTPException(400, "Нет данных для отчёта")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        filename=f"report_{check_id}.docx")


@app.get("/api/checks/{check_id}/report/xlsx")
def download_report_xlsx(check_id: int, request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401)
    path = report_gen.generate_xlsx(check_id)
    if not path:
        raise HTTPException(400, "Нет данных для отчёта")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        filename=f"report_{check_id}.xlsx")


# --- AI models -------------------------------------------------------

@app.get("/api/ai-models")
def list_ai_models(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401)
    with db() as conn:
        rows = conn.execute(
            "SELECT id, name, kind, provider, base_url, model_id, is_enabled, is_default, notes, created_at FROM ai_models ORDER BY kind, name"
        ).fetchall()
        return {"models": rows_to_dicts(rows)}


@app.put("/api/ai-models")
def upsert_ai_model(request: Request, data: dict):
    user = getattr(request.state, "user", None)
    if not user or user["role"] != "admin":
        raise HTTPException(403)
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM ai_models WHERE name = ?", (data["name"],)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE ai_models SET provider=?, base_url=?, model_id=?, api_key=?,
                   is_enabled=?, notes=?, updated_at=? WHERE id=?""",
                (data.get("provider", ""), data.get("base_url", ""), data.get("model_id", ""),
                 data.get("api_key", ""), data.get("is_enabled", 1), data.get("notes", ""),
                 utcnow(), existing["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO ai_models (name, kind, provider, base_url, model_id, api_key, is_enabled, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (data["name"], data.get("kind", "cloud"), data.get("provider", "openai-compatible"),
                 data.get("base_url", ""), data.get("model_id", ""), data.get("api_key", ""),
                 data.get("is_enabled", 1), data.get("notes", ""), utcnow(), utcnow()),
            )
    return {"ok": True}


@app.delete("/api/ai-models/{model_id}")
def delete_ai_model(model_id: int, request: Request):
    user = getattr(request.state, "user", None)
    if not user or user["role"] != "admin":
        raise HTTPException(403)
    with db() as conn:
        conn.execute("DELETE FROM ai_models WHERE id = ?", (model_id,))
    return {"ok": True}


# --- web UI ----------------------------------------------------------


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    token = request.cookies.get(config.SESSION_COOKIE)
    request.state.user = validate_session(token)
    response = await call_next(request)
    return response


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    user = getattr(request.state, "user", None)
    return templates.TemplateResponse("index.html", {"request": request, "user": user})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    user = getattr(request.state, "user", None)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("admin.html", {"request": request, "user": user})


@app.get("/check/{check_id}", response_class=HTMLResponse)
def check_page(request: Request, check_id: int):
    user = getattr(request.state, "user", None)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("check.html", {"request": request, "user": user, "check_id": check_id})