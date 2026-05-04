# app.py - Unified Backend for Research Assistant and Notebook Analysis
import os
import uuid
import tempfile
import logging
import sqlite3
import json
import re
import gc
import time
import sys
import traceback
import secrets
import shutil
import threading
from pathlib import Path
from typing import List, Optional, Dict
from urllib.parse import quote

from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_401_UNAUTHORIZED
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from openai import AsyncOpenAI

try:
    from .mineru_net_client import MinerUNetError, convert_pdf_path_to_markdown
except ImportError:
    # Running as `python backend/mineru/main.py` (no package parent)
    from mineru_net_client import MinerUNetError, convert_pdf_path_to_markdown

# ======================
# Configuration
# ======================
class Settings(BaseSettings):
    database_path: str = "research.db"
    mineru_output_dir: str = "mineru_output"
    upload_dir: str = "uploads"
    frontend_path: str = ""
    note_path: str = ""
    research_path: str = ""
    static_path: str = ""
    temp_dir: str = "temp"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openrouter/free"
    session_cookie_name: str = "ra_sid"
    session_ttl_seconds: int = 2 * 60 * 60  # 2 hours
    mineru_api_key: str = ""
    mineru_api_base: str = "https://mineru.net"
    mineru_cloud_language: str = "en"
    # Cloud poll tuning (Render/proxy friendly; large PDFs may need async job pattern later)
    mineru_cloud_poll_max_retries: int = 100
    mineru_cloud_poll_interval_seconds: int = 5

    class Config:
        env_file = ".env"

settings = Settings()

# Resolve frontend paths relative to repo, unless overridden by env
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_ROOT = Path(os.environ.get("FRONTEND_PATH", str(_REPO_ROOT / "frontend"))).resolve()
settings.frontend_path = os.environ.get("FRONTEND_PATH", str(_FRONTEND_ROOT))
settings.note_path = os.environ.get("NOTE_PATH", str(_FRONTEND_ROOT / "notebook"))
settings.research_path = os.environ.get("RESEARCH_PATH", str(_FRONTEND_ROOT / "research"))
settings.static_path = os.environ.get("STATIC_PATH", str(_FRONTEND_ROOT / "static"))
settings.openrouter_base_url = os.environ.get("OPENROUTER_BASE_URL", settings.openrouter_base_url)
settings.openrouter_model = os.environ.get("OPENROUTER_MODEL", settings.openrouter_model)
if os.environ.get("SESSION_TTL_SECONDS"):
    try:
        settings.session_ttl_seconds = int(os.environ["SESSION_TTL_SECONDS"])
    except Exception:
        pass
for _env_key, _attr in (
    ("MINERU_CLOUD_POLL_MAX_RETRIES", "mineru_cloud_poll_max_retries"),
    ("MINERU_CLOUD_POLL_INTERVAL_SECONDS", "mineru_cloud_poll_interval_seconds"),
):
    if os.environ.get(_env_key):
        try:
            setattr(settings, _attr, int(os.environ[_env_key]))
        except Exception:
            pass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SessionKeyIn(BaseModel):
    api_key: str

class SessionStatusOut(BaseModel):
    has_api_key: bool
    session_id: str

class _SessionState:
    def __init__(self) -> None:
        self.openrouter_api_key: Optional[str] = None
        self.chat_history: List["ChatMessage"] = []
        self.files_list: List[str] = []
        self.current_notebook: int = 0
        self.files_uploaded: bool = False
        self.created_at: float = time.time()
        self.last_seen: float = time.time()

_session_lock = threading.RLock()
_sessions: Dict[str, _SessionState] = {}

def _now() -> float:
    return time.time()

def _purge_expired_sessions() -> None:
    cutoff = _now() - settings.session_ttl_seconds
    expired: List[str] = []
    with _session_lock:
        for sid, st in _sessions.items():
            if st.last_seen < cutoff:
                expired.append(sid)
        for sid in expired:
            _sessions.pop(sid, None)
            _delete_session_dirs(sid)

def _get_or_create_session(sid: str) -> _SessionState:
    with _session_lock:
        st = _sessions.get(sid)
        if st is None:
            st = _SessionState()
            _sessions[sid] = st
        st.last_seen = _now()
        return st

def _session_upload_dir(sid: str) -> str:
    return str(Path(settings.upload_dir) / sid)

def _session_temp_dir(sid: str) -> str:
    return str(Path(settings.temp_dir) / sid)

def _delete_session_dirs(sid: str) -> None:
    for d in (_session_upload_dir(sid), _session_temp_dir(sid)):
        try:
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Failed to delete session dir {d}: {e}")

def _require_openrouter_client(st: _SessionState) -> AsyncOpenAI:
    if not st.openrouter_api_key:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="OpenRouter API key required for this session. Set it via POST /session/api-key.",
        )
    return AsyncOpenAI(base_url=settings.openrouter_base_url, api_key=st.openrouter_api_key)

# ======================
# Models/Schemas
# ======================
class GenerateRequest(BaseModel):
    prompt: str
    paper_ids: Optional[List[str]] = None
    context: Optional[str] = None  # prior discussion text for follow-up /discuss calls

class ChatMessage(BaseModel):
    role: str
    content: str

# Constants
CONTEXT_WINDOW_LIMIT = 32000  # Tokens

# ======================
# Database
# ======================
def init_db():
    try:
        conn = sqlite3.connect(settings.database_path)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS papers (
                id TEXT PRIMARY KEY,
                filename TEXT,
                content TEXT,
                points TEXT,
                content_length INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database init failed: {str(e)}")
        raise

def store_paper(paper_id, filename, content, points):
    try:
        conn = sqlite3.connect(settings.database_path)
        c = conn.cursor()
        content_size = len(content) if content else 0
        
        c.execute(
            """INSERT INTO papers (id, filename, content, points, content_length) 
            VALUES (?, ?, ?, ?, ?)""",
            (paper_id, filename, content, 
             json.dumps(points) if not isinstance(points, str) else points,
             content_size)
        )
        conn.commit()
        logger.info(f"Stored paper: {filename}")
    except Exception as e:
        logger.error(f"Failed to store paper: {str(e)}")
        raise
    finally:
        if conn:
            conn.close()

def _parse_points_field(points_field) -> List[str]:
    if points_field is None:
        return []
    if isinstance(points_field, list):
        return [str(x) for x in points_field]
    if isinstance(points_field, str):
        try:
            pl = json.loads(points_field)
            if isinstance(pl, list):
                return [str(x) for x in pl]
            return [str(pl)] if pl else []
        except json.JSONDecodeError:
            return [points_field] if points_field.strip() else []
    return []


def extract_abstract_from_markdown(md: Optional[str], max_chars: int = 4500) -> str:
    """Text under an 'Abstract' markdown heading until the next # heading (MinerU-style MD)."""
    if not md or not str(md).strip():
        return ""
    text = str(md)
    m = re.search(r"(?im)^#{1,6}\s*abstract\s*\n?", text)
    if not m:
        return ""
    rest = text[m.end() :]
    nm = re.search(r"(?im)^#{1,6}\s+\S", rest)
    if nm:
        body = rest[: nm.start()].strip()
    else:
        body = rest.strip()
    body = clean_md_content(body)
    if len(body) > max_chars:
        return body[:max_chars].rstrip() + "…"
    return body


def opening_excerpt_for_ranking(md: Optional[str], max_chars: int = 1600) -> str:
    """Fallback when no Abstract section exists."""
    return clean_md_content(md or "")[:max_chars]


def _load_all_paper_rows() -> List[sqlite3.Row]:
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, filename, content, points FROM papers").fetchall()
    conn.close()
    return list(rows)


def _papers_from_ids_ordered(paper_ids: List[str]) -> List[dict]:
    if not paper_ids:
        return []
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    rows = {
        r["id"]: dict(r)
        for r in conn.execute(
            "SELECT id, filename, content, points FROM papers WHERE id IN ({})".format(
                ",".join("?" * len(paper_ids))
            ),
            tuple(paper_ids),
        ).fetchall()
    }
    conn.close()
    ordered = []
    for pid in paper_ids:
        if pid in rows:
            ordered.append(rows[pid])
    return ordered


def _parse_rankings_json(raw: str) -> Optional[List[dict]]:
    if not raw:
        return None
    t = raw.strip()
    if "```" in t:
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t, re.I)
        if fence:
            t = fence.group(1).strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        brace = re.search(r"\{[\s\S]*\}\s*$", t)
        if not brace:
            return None
        try:
            obj = json.loads(brace.group(0))
        except json.JSONDecodeError:
            return None
    rankings = obj.get("rankings") if isinstance(obj, dict) else None
    if not isinstance(rankings, list):
        return None
    out = []
    for item in rankings:
        if not isinstance(item, dict):
            continue
        pid = item.get("id")
        if not pid or not isinstance(pid, str):
            continue
        try:
            sc = int(float(item.get("score", 50)))
        except (TypeError, ValueError):
            sc = 50
        sc = max(0, min(100, sc))
        out.append(
            {
                "id": pid,
                "include": bool(item.get("include", True)),
                "score": sc,
                "note": str(item.get("note", ""))[:200],
            }
        )
    return out or None


async def llm_rank_papers_by_abstracts(
    client: AsyncOpenAI, user_prompt: str, summaries: List[dict]
) -> Dict[str, dict]:
    """summaries: {id, filename, abstract, source_kind}; returns id -> {include, score, note}."""
    if not summaries:
        return {}
    mini = [
        {
            "id": s["id"],
            "filename": (s.get("filename") or "")[:200],
            "abstract": (s.get("abstract") or "")[:3200],
            "source_kind": s.get("source_kind", "abstract"),
        }
        for s in summaries
    ]
    sys_msg = (
        "You classify academic papers for relevance to a user's research topic. "
        "You only see titles and abstract (or opening excerpt) text — not full PDFs. "
        "Return strict JSON only."
    )
    user_msg = f"""Research topic / question:
{user_prompt.strip()[:4000]}

Papers (JSON array; source_kind is 'abstract' if parsed from an Abstract heading, else 'opening_excerpt'):
{json.dumps(mini, ensure_ascii=False)}

Return JSON with exactly this shape (one entry per paper id, no extras):
{{"rankings":[{{"id":"<paper uuid>","include":true|false,"score":0-100,"note":"<=25 words"}}]}}

Rules:
- include=true when the paper would materially help the user explore or answer the topic.
- score reflects match strength (0 irrelevant, 100 central).
- Use the exact "id" strings from the input."""
    raw_content = ""
    try:
        response = await client.chat.completions.create(
            model=settings.openrouter_model,
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=4000,
            response_format={"type": "json_object"},
        )
        raw_content = (response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning(f"LLM ranking (json_object) failed, retrying without: {e}")
        try:
            response = await client.chat.completions.create(
                model=settings.openrouter_model,
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.2,
                max_tokens=4000,
            )
            raw_content = (response.choices[0].message.content or "").strip()
        except Exception as e2:
            logger.error(f"LLM ranking failed: {e2}")
            return {}

    rankings = _parse_rankings_json(raw_content)
    if not rankings:
        logger.warning("Could not parse rankings JSON; including all papers with neutral score")
        return {
            s["id"]: {"include": True, "score": 50, "note": "parse fallback"}
            for s in summaries
        }

    by_id = {r["id"]: r for r in rankings}
    for s in summaries:
        if s["id"] not in by_id:
            by_id[s["id"]] = {"include": True, "score": 45, "note": "model omitted id"}
    return by_id


async def build_write_candidates(client: AsyncOpenAI, user_prompt: str) -> List[dict]:
    rows = _load_all_paper_rows()
    summaries = []
    for row in rows:
        md = row["content"] or ""
        ab = extract_abstract_from_markdown(md)
        if not (ab or "").strip():
            ab = opening_excerpt_for_ranking(md)
            sk = "opening_excerpt"
        else:
            sk = "abstract"
        summaries.append(
            {
                "id": row["id"],
                "filename": row["filename"] or "Untitled",
                "abstract": ab,
                "source_kind": sk,
            }
        )
    if not summaries:
        return []
    rank_map = await llm_rank_papers_by_abstracts(client, user_prompt, summaries)
    out = []
    for s in summaries:
        r = rank_map.get(s["id"], {"include": True, "score": 50, "note": ""})
        try:
            sc = int(float(r.get("score", 50)))
        except (TypeError, ValueError):
            sc = 50
        sc = max(0, min(100, sc))
        out.append(
            {
                "id": s["id"],
                "filename": s["filename"],
                "abstract": s["abstract"],
                "source_kind": s["source_kind"],
                "llm_include": bool(r.get("include", True)),
                "llm_score": sc,
                "llm_note": str(r.get("note", "")),
            }
        )
    out.sort(key=lambda x: -x["llm_score"])
    return out


async def select_papers_for_prompt_llm(client: AsyncOpenAI, user_prompt: str) -> List[dict]:
    """Papers to use when caller did not pass explicit paper_ids (server-side fallback)."""
    cand = await build_write_candidates(client, user_prompt)
    picked = [c for c in cand if c["llm_include"]]
    if not picked:
        picked = cand[: max(1, min(5, len(cand)))]
    ids = [c["id"] for c in picked]
    return _papers_from_ids_ordered(ids)


# ======================
# Markdown helpers (MinerU.net output)
# ======================
def clean_md_content(content):
    """Clean up markdown content for better processing"""
    content = re.sub(r'!\[.*?\]\(.*?\)', '', content)
    content = re.sub(r'\{.*?\}', '', content)
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content.strip()

# ======================
# Chat Utilities
# ======================
def mark_new_message(history: List[ChatMessage], role: str, content: str) -> List[ChatMessage]:
    """Add a new message to chat history"""
    return history + [ChatMessage(role=role, content=content)]

def format_chat_history(history: List[ChatMessage]):
    """Format chat history for LLM input"""
    return [{"role": msg.role, "content": msg.content} for msg in history]

def mark_file_upload(history: List[ChatMessage], upload_results: dict) -> List[ChatMessage]:
    """Create a system message about file uploads"""
    upload_summary = "\n".join(
        f"{name}: {result['Context']}" 
        for name, result in upload_results.items()
    )
    return mark_new_message(history, "system", f"Files uploaded:\n{upload_summary}")

def mark_content_sending(files: List[str], current_idx: int) -> str:
    """Create message about sending notebook content"""
    return f"Analyzing notebook: {files[current_idx]}"

def calculate_tokens(history: List[ChatMessage]) -> int:
    """Estimate token count (simplified)"""
    return sum(len(msg.content.split()) for msg in history)

def remove_duplicates(directory: str):
    """Remove duplicate files in directory"""
    seen = set()
    for filename in os.listdir(directory):
        if filename in seen:
            os.remove(os.path.join(directory, filename))
        else:
            seen.add(filename)

def clean_directory(directory: str):
    """Clean all files in directory except .gitkeep"""
    for filename in os.listdir(directory):
        if filename not in (".gitkeep", ".DS_Store"):
            try:
                os.remove(os.path.join(directory, filename))
            except Exception as e:
                logger.warning(f"Could not delete {filename}: {str(e)}")

# ======================
# LLM Responders
# ======================
async def get_response(client: AsyncOpenAI, messages: List[dict]) -> str:
    """Get response from LLM for general chat"""
    try:
        response = await client.chat.completions.create(
            model=settings.openrouter_model,
            messages=messages,
            temperature=0.7,
            max_tokens=2000
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"LLM response failed: {str(e)}")
        return "Sorry, I encountered an error generating a response."

async def get_analysis(client: AsyncOpenAI, upload_dir: str, filename: str) -> str:
    """Get analysis of a notebook file"""
    try:
        content = ""
        filepath = os.path.join(upload_dir, filename)
        
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
        
        prompt = f"""Analyze this Jupyter notebook file ({filename}):
        
        {content[:10000]}... [truncated if long]
        
        Provide:
        1. Overview of the notebook's purpose
        2. Key code functionality
        3. Data flow analysis
        4. Potential improvements
        5. Any issues found"""
        
        return await get_response(client, [{"role": "user", "content": prompt}])
    except Exception as e:
        logger.error(f"Notebook analysis failed: {str(e)}")
        return f"Analysis failed for {filename}: {str(e)}"

# ======================
# Application Setup
# ======================
app = FastAPI(title="Unified Research Assistant")

# ============================================================================
# MEMORY MANAGEMENT MIDDLEWARE (Critical for 2GB instances)
# ============================================================================
class _MemoryManagementMiddleware(BaseHTTPMiddleware):
    """Aggressively manage memory on constrained instances"""
    def __init__(self, app, *args, **kwargs):
        super().__init__(app, *args, **kwargs)
        self.request_count = 0
        self.last_gc = time.time()

    async def dispatch(self, request: Request, call_next):
        self.request_count += 1
        response = await call_next(request)

        # Aggressive garbage collection every 5 requests or every 60 seconds
        if self.request_count % 5 == 0 or (time.time() - self.last_gc) > 60:
            gc.collect()
            self.last_gc = time.time()

        return response

app.add_middleware(_MemoryManagementMiddleware)

class _SessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        _purge_expired_sessions()

        cookie_name = settings.session_cookie_name
        sid = request.cookies.get(cookie_name)
        new_sid = False

        if not sid or not re.fullmatch(r"[a-f0-9]{32}", sid):
            sid = secrets.token_hex(16)
            new_sid = True

        request.state.session_id = sid
        response = await call_next(request)

        if new_sid:
            forwarded_proto = request.headers.get("x-forwarded-proto")
            is_https = (forwarded_proto == "https") or (request.url.scheme == "https")
            response.set_cookie(
                key=cookie_name,
                value=sid,
                httponly=True,
                secure=is_https,
                samesite="lax",
                max_age=settings.session_ttl_seconds,
                path="/",
            )
        return response

app.add_middleware(_SessionMiddleware)

# CORS configuration (must be explicit when using cookies)
cors_origins_env = os.environ.get("CORS_ORIGINS", "").strip()
if cors_origins_env:
    cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
else:
    # Local dev default (same-origin and common localhost variants)
    cors_origins = [
        "https://ra-pdf2md-orbs-unified-deploy.vercel.app",   # <-- removed trailing slash
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database and directories
init_db()
os.makedirs(settings.mineru_output_dir, exist_ok=True)
os.makedirs(settings.upload_dir, exist_ok=True)
os.makedirs(settings.temp_dir, exist_ok=True)
os.makedirs(settings.frontend_path, exist_ok=True)

# Frontend StaticFiles mounts are registered after all API routes (see end of file) so
# JSON routes like /write/candidates are never shadowed by catch-all behavior.

# ======================
# Session endpoints
# ======================
@app.get("/session/status", response_model=SessionStatusOut)
async def session_status(request: Request):
    sid = request.state.session_id
    st = _get_or_create_session(sid)
    return SessionStatusOut(has_api_key=bool(st.openrouter_api_key), session_id=sid)

@app.post("/session/api-key", response_model=SessionStatusOut)
async def session_set_api_key(payload: SessionKeyIn, request: Request):
    sid = request.state.session_id
    st = _get_or_create_session(sid)
    api_key = (payload.api_key or "").strip()
    if not api_key:
        raise HTTPException(400, "api_key is required")
    st.openrouter_api_key = api_key
    return SessionStatusOut(has_api_key=True, session_id=sid)

@app.post("/session/end")
async def session_end(request: Request, response: Response):
    sid = request.state.session_id
    with _session_lock:
        _sessions.pop(sid, None)
    _delete_session_dirs(sid)
    response.delete_cookie(key=settings.session_cookie_name, path="/")
    return {"status": "success"}

# ======================
# Health & Memory Management Endpoints
# ======================
@app.get("/health")
async def health_check():
    """Lightweight health check (doesn't load models)"""
    try:
        conn = sqlite3.connect(settings.database_path)
        conn.execute("SELECT 1")
        conn.close()
        return {"status": "ok", "message": "Service healthy"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(503, "Service unavailable")

@app.post("/memory/cleanup")
async def cleanup_memory():
    """Force garbage collection and cache cleanup"""
    import psutil
    process = psutil.Process()
    mem_before = process.memory_info().rss / 1024 / 1024  # MB

    gc.collect()

    mem_after = process.memory_info().rss / 1024 / 1024  # MB
    freed = mem_before - mem_after

    return {
        "status": "cleaned",
        "memory_before_mb": round(mem_before, 2),
        "memory_after_mb": round(mem_after, 2),
        "freed_mb": round(freed, 2)
    }

# ======================
# API Endpoints
# ======================
@app.get("/")
async def root(request: Request):
    """Serve the main frontend"""
    sid = request.state.session_id
    st = _get_or_create_session(sid)
    st.chat_history.clear()
    st.files_uploaded = False
    st.files_list.clear()
    st.current_notebook = 0
    clean_directory(_session_temp_dir(sid))
    clean_directory(_session_upload_dir(sid))
    return FileResponse(os.path.join(settings.frontend_path, "index.html"))

@app.post("/clear-chat-history")
async def clear_chat_history(request: Request):
    """Reset chat state"""
    sid = request.state.session_id
    st = _get_or_create_session(sid)
    st.chat_history.clear()
    st.files_uploaded = False
    st.files_list.clear()
    st.current_notebook = 0
    clean_directory(_session_temp_dir(sid))
    clean_directory(_session_upload_dir(sid))
    return {"status": "success", "message": "Chat history cleared"}

@app.post("/chatbot-answer")
async def chatbot_answer(message: ChatMessage, request: Request):
    """Handle chat messages"""
    sid = request.state.session_id
    st = _get_or_create_session(sid)
    client = _require_openrouter_client(st)

    st.chat_history = mark_new_message(st.chat_history, message.role, message.content)
    formatted_messages = format_chat_history(st.chat_history)
    
    response_text = await get_response(client, formatted_messages)
    st.chat_history = mark_new_message(st.chat_history, "assistant", response_text)
    
    tokens = calculate_tokens(st.chat_history)
    if tokens >= CONTEXT_WINDOW_LIMIT:
        response_text += "\n\n*SYSTEM WARNING*: Chat memory is full! Please clear chat history."
    
    return ChatMessage(role="assistant", content=response_text)

@app.post("/files-upload")
async def process_files(files: List[UploadFile], request: Request):    
    """Handle file uploads"""
    sid = request.state.session_id
    st = _get_or_create_session(sid)
    
    upload_dir = _session_upload_dir(sid)
    temp_dir = _session_temp_dir(sid)
    os.makedirs(upload_dir, exist_ok=True)
    os.makedirs(temp_dir, exist_ok=True)

    clean_directory(temp_dir)
    clean_directory(upload_dir)
    st.files_list.clear()
    st.current_notebook = 0
    
    upload_results = {}
    for file in files:
        if not file or not file.filename:
            upload_results["Unknown"] = {"Saved": False, "Context": "No file provided"}
            continue

        filename = file.filename.strip()
        
        if not filename.lower().endswith(".ipynb"):
            upload_results[filename] = {"Saved": False, "Context": "Not a valid Jupyter Notebook (.ipynb)"}
            continue
        
        safe_filename = os.path.basename(filename)
        filepath = os.path.join(upload_dir, safe_filename)
        
        try:
            content = await file.read()
            if len(content) == 0:
                upload_results[filename] = {"Saved": False, "Context": "File is empty"}
                continue
            
            if len(content) > 24 * 1024 * 1024:  # 24MB
                upload_results[filename] = {"Saved": False, "Context": "File too large (max 24MB)"}
                continue
            
            with open(filepath, "wb") as f:
                f.write(content)
            upload_results[filename] = {"Saved": True, "Context": f"Saved as {safe_filename}"}
            
        except Exception as e:
            upload_results[filename] = {"Saved": False, "Context": f"Error: {str(e)}"}

    st.chat_history = mark_file_upload(st.chat_history, upload_results)
    st.files_list = [f for f in os.listdir(upload_dir) if f.endswith(".ipynb")]
    st.files_uploaded = bool(st.files_list)
    
    return upload_results

@app.post("/analyze")
async def start_analysis(request: Request):
    """Analyze uploaded notebooks"""
    sid = request.state.session_id
    st = _get_or_create_session(sid)
    client = _require_openrouter_client(st)
    
    if not st.files_uploaded:
        response_text = "No notebooks found. Please upload files first."
        st.chat_history = mark_new_message(st.chat_history, "user", "*/start*")
        st.chat_history = mark_new_message(st.chat_history, "assistant", response_text)
        return ChatMessage(role="assistant", content=response_text)
    
    if st.current_notebook >= len(st.files_list):
        response_text = "No more notebooks to analyze."
        st.chat_history = mark_new_message(st.chat_history, "user", "*/next*")
        st.chat_history = mark_new_message(st.chat_history, "assistant", response_text)
        return ChatMessage(role="assistant", content=response_text)
    
    response_text = await get_analysis(client, _session_upload_dir(sid), st.files_list[st.current_notebook])
    user_input = mark_content_sending(st.files_list, st.current_notebook)
    
    st.chat_history = mark_new_message(st.chat_history, "user", user_input)
    st.chat_history = mark_new_message(st.chat_history, "assistant", response_text)
    
    st.current_notebook += 1
    return ChatMessage(role="assistant", content=response_text)

@app.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    """Upload and process research PDFs"""
    if file.content_type != "application/pdf":
        raise HTTPException(400, "Only PDF files are allowed")

    temp_dir = tempfile.mkdtemp()
    pdf_id = str(uuid.uuid4())
    pdf_path = os.path.join(temp_dir, f"{pdf_id}.pdf")
    
    try:
        st = _get_or_create_session(request.state.session_id)
        client = _require_openrouter_client(st)

        with open(pdf_path, "wb") as f:
            f.write(await file.read())

        output_dir = os.path.join(settings.mineru_output_dir, pdf_id)
        os.makedirs(output_dir, exist_ok=True)

        api_key = (settings.mineru_api_key or "").strip()
        if not api_key:
            raise HTTPException(
                503,
                "MINERU_API_KEY is not set. This branch uses MinerU.net for PDF extraction.",
            )
        try:
            raw_md = await convert_pdf_path_to_markdown(
                pdf_path,
                api_base=settings.mineru_api_base,
                api_key=api_key,
                language=settings.mineru_cloud_language,
                output_dir=output_dir,
                max_retries=settings.mineru_cloud_poll_max_retries,
                retry_interval=settings.mineru_cloud_poll_interval_seconds,
            )
        except MinerUNetError as e:
            raise HTTPException(502, f"MinerU.net error: {e}") from e
        except TimeoutError:
            raise HTTPException(
                504,
                "MinerU.net conversion timed out. For large PDFs, increase "
                "MINERU_CLOUD_POLL_MAX_RETRIES / MINERU_CLOUD_POLL_INTERVAL_SECONDS "
                "or use a background-job flow.",
            ) from None
        text_content = clean_md_content(raw_md)

        prompt = f"""Extract exactly 5 key research points from this paper.
        Format each point EXACTLY like this:
        - [concise point here]
        Include nothing else in your response.

        Paper content:
        {text_content[:4000]}"""

        response = await client.chat.completions.create(
            model=settings.openrouter_model,
            messages=[{"role": "user", "content": prompt}]
        )

        raw_points = [
            line.strip() 
            for line in response.choices[0].message.content.split('\n') 
            if line.strip().startswith('-') and len(line.strip()) > 2
        ]
        
        points = []
        for i in range(5):
            if i < len(raw_points):
                point = raw_points[i].strip()[2:] if raw_points[i].startswith('- ') else raw_points[i].strip()
                points.append(f"- {point}")
            else:
                points.append(f"- Key point {i+1} from research")

        formatted_points = []
        for point in points:
            formatted_points.append({
                "text": point,
                "source": file.filename,
                "sourceId": pdf_id
            })

        store_paper(pdf_id, file.filename, text_content, json.dumps(points))

        return JSONResponse({
            "status": "success",
            "points": formatted_points,
            "id": pdf_id,
            "filename": file.filename
        })

    except Exception as e:
        logger.error(f"Upload failed: {str(e)}")
        raise HTTPException(500, f"Processing failed: {str(e)}")
    finally:
        try:
            if os.path.exists(pdf_path):
                os.unlink(pdf_path)
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)
        except Exception as e:
            logger.warning(f"Temp file cleanup failed: {str(e)}")


@app.post("/write/candidates")
@app.post("/research/candidates")  # alias (same handler) if proxies block paths containing "write"
async def write_paper_candidates(request: GenerateRequest, http_request: Request):
    """LLM ranks all DB papers using abstract (or opening excerpt); for Write-mode checkbox UI."""
    if not (request.prompt or "").strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    st = _get_or_create_session(http_request.state.session_id)
    client = _require_openrouter_client(st)
    candidates = await build_write_candidates(client, request.prompt.strip())
    return {"status": "success", "candidates": candidates}


@app.post("/generate")
async def generate_points(request: GenerateRequest, http_request: Request):
    """Generate research points from papers (prefer explicit paper_ids from user checkboxes)."""
    try:
        st = _get_or_create_session(http_request.state.session_id)
        client = _require_openrouter_client(st)
        if request.paper_ids and len(request.paper_ids) > 0:
            relevant_papers = _papers_from_ids_ordered(request.paper_ids)
        else:
            relevant_papers = await select_papers_for_prompt_llm(client, request.prompt)

        if not relevant_papers:
            return {
                "status": "success",
                "points": [
                    {
                        "formatted_text": "No papers in the database yet. Upload PDFs in Review mode first.",
                        "clean_source": "",
                        "raw_data": {},
                    }
                ],
                "paper_ids": [],
            }

        context_parts = []
        for i, p in enumerate(relevant_papers):
            pts = _parse_points_field(p.get("points"))
            excerpt = (p.get("content") or "")[:4500]
            context_parts.append(
                f"PAPER {i+1}: {p.get('filename', 'Untitled')}\n"
                f"KEY POINTS:\n" + "\n".join(pts[:12]) + "\n"
                f"CONTENT EXCERPT:\n{excerpt}...\n"
            )
        context = "\n\n".join(context_parts)
        n_papers = len(relevant_papers)

        prompt = f"""Generate 5-7 high-quality research points about: {request.prompt}

Using these {n_papers} papers as sources (numbered Paper 1 … Paper {n_papers}):
{context}

FORMATTING RULES:
1. Each point must start with • 
2. Include (Source: Paper X | *name of the paper and its author* | APA 7 Citation of paper) where X matches the paper number above (use multiple Paper numbers if a point synthesizes several sources).
3. Keep points concise but informative
4. Compare/contrast different papers when relevant
5. Cover different aspects of the topic"""

        response = await client.chat.completions.create(
            model=settings.openrouter_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2000,
        )

        raw_points = [
            line.strip()
            for line in (response.choices[0].message.content or "").split("\n")
            if line.strip().startswith("•")
        ][:7]

        paper_map = {f"Paper{i+1}": p["id"] for i, p in enumerate(relevant_papers)}

        points = []
        for line in raw_points:
            source_matches = re.findall(r"\(Source: (Paper\d+(?:, Paper\d+)*)\)", line)
            source_papers = []
            if source_matches:
                for match in source_matches[0].split(", "):
                    match = match.strip()
                    if match in paper_map:
                        pid = paper_map[match]
                        source_papers.append(
                            {
                                "id": pid,
                                "name": next(
                                    (p["filename"] for p in relevant_papers if p["id"] == pid),
                                    match,
                                ),
                            }
                        )

            if "• " in line:
                point_text = line.split("• ", 1)[1].split(" (Source:")[0].strip()
            else:
                point_text = line.lstrip("•").strip()

            points.append(
                {
                    "formatted_text": line,
                    "raw_data": {
                        "text": point_text,
                        "sources": source_papers,
                        "source": ", ".join(p["name"] for p in source_papers),
                        "sourceIds": [p["id"] for p in source_papers],
                    },
                }
            )

        return {
            "status": "success",
            "points": points,
            "paper_ids": [p["id"] for p in relevant_papers],
        }

    except Exception as e:
        logger.error(f"Generation failed: {str(e)}")
        return {
            "status": "error",
            "points": [
                {
                    "formatted_text": f"Error: {str(e)}",
                    "clean_source": "",
                }
            ],
        }

@app.post("/discuss")
async def discuss_points(request: GenerateRequest, http_request: Request):
    """Discussion from selected papers, or abstract-ranked fallback; supports follow-up via context."""
    try:
        st = _get_or_create_session(http_request.state.session_id)
        client = _require_openrouter_client(st)
        if request.paper_ids and len(request.paper_ids) > 0:
            papers = _papers_from_ids_ordered(request.paper_ids)
        else:
            papers = await select_papers_for_prompt_llm(client, request.prompt)

        if not papers:
            return {
                "status": "success",
                "discussion": "No relevant papers found for discussion. Add papers to the database or broaden your topic.",
            }

        excerpt_limit = 2200 if (request.context and request.context.strip()) else 4000
        context_parts = []
        for i, p in enumerate(papers):
            points_list = _parse_points_field(p.get("points"))
            paper_context = f"PAPER {i+1} - {p.get('filename', 'Untitled')}:\n"
            paper_context += "KEY POINTS:\n" + "\n".join(points_list[:80]) + "\n"
            paper_context += f"CONTENT EXCERPT:\n{(p.get('content') or '')[:excerpt_limit]}...\n"
            context_parts.append(paper_context)

        papers_blob = "\n\n".join(context_parts)
        n = len(papers)

        if request.context and request.context.strip():
            ctx_trim = request.context.strip()[:12000]
            discussion_prompt = f"""You are continuing a research assistant session.

You have {n} papers (cite as [1]–[{n}] matching the order below). Answer the follow-up using markdown (## headings, **bold**, lists when helpful).

SOURCE MATERIALS:
{papers_blob}

PRIOR DISCUSSION:
{ctx_trim}

FOLLOW-UP QUESTION:
{request.prompt.strip()[:4000]}

Guidelines:
- Ground the answer in the prior discussion and papers; cite as [1], [2], etc.
- If a short factual answer suffices, stay brief; otherwise about 350–900 words."""
        else:
            discussion_prompt = f"""Generate a comprehensive discussion about: {request.prompt}

Analyze ALL {n} provided research papers:
{papers_blob}

GUIDELINES:
1. Start with an overview synthesizing findings from ALL papers
2. Compare and contrast perspectives across ALL papers, not just the first few
3. Identify patterns, contradictions, and consensus across the entire corpus
4. Highlight areas of agreement/disagreement among ALL sources
5. Use [1], [2], [3], etc. for citations matching ALL paper numbers
6. Discuss limitations and future directions considering ALL evidence
7. Maintain academic tone but avoid jargon
8. Use markdown (## sections, **emphasis**) for readability
9. Length: ~800–1200 words when appropriate
10. Reference findings across the full set of papers, not only early ones"""

        response = await client.chat.completions.create(
            model=settings.openrouter_model,
            messages=[{"role": "user", "content": discussion_prompt}],
            temperature=0.7,
            max_tokens=4500,
        )

        return {
            "status": "success",
            "discussion": response.choices[0].message.content or "",
            "sources": [p["filename"] for p in papers],
            "paper_ids": [p["id"] for p in papers],
            "total_papers_analyzed": len(papers),
        }

    except Exception as e:
        logger.error(f"Discussion failed: {str(e)}")
        return {
            "status": "error",
            "discussion": f"Error generating discussion: {str(e)}",
        }

@app.get("/database")
async def view_database():
    """View all papers in database"""
    try:
        conn = sqlite3.connect(settings.database_path)
        conn.row_factory = sqlite3.Row
        papers = conn.execute(
            "SELECT id, filename, content, points, created_at FROM papers "
            "ORDER BY datetime(created_at) DESC"
        ).fetchall()
        conn.close()
        
        response = {
            "status": "success",
            "papers": [{
                "id": p["id"],
                "filename": p["filename"] or "Untitled",
                "content_preview": (p["content"][:100] + "...") if p["content"] else "",
                "content_length": len(p["content"]) if p["content"] else 0,
                "points": json.loads(p["points"]) if p["points"] else []
            } for p in papers]
        }
        
        return JSONResponse(response)
            
    except Exception as e:
        logger.error(f"Database error: {e}")
        return JSONResponse(
            {"status": "error", "message": "Database error"},
            status_code=500
        )

@app.get("/database/{paper_id}")
async def view_paper_details(paper_id: str):
    """View details of a specific paper"""
    try:
        conn = sqlite3.connect(settings.database_path)
        conn.row_factory = sqlite3.Row
        paper = conn.execute(
            "SELECT id, filename, content, points FROM papers WHERE id = ?", 
            (paper_id,)
        ).fetchone()
        conn.close()
        
        if not paper:
            raise HTTPException(404, "Paper not found")
            
        points = (json.loads(paper["points"]) 
                 if paper["points"] and paper["points"].startswith('[') 
                 else (paper["points"] if paper["points"] else []))
        
        return {
            "id": paper["id"],
            "filename": paper["filename"],
            "content": paper["content"],
            "points": points
        }
    except Exception as e:
        logger.error(f"Paper details failed: {str(e)}")
        raise HTTPException(500, str(e))


def _valid_paper_id(paper_id: str) -> bool:
    try:
        uuid.UUID(str(paper_id))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _delete_paper_artifacts(paper_id: str) -> None:
    """Remove MinerU output directory for this paper id (best-effort)."""
    paper_dir = os.path.join(settings.mineru_output_dir, paper_id)
    try:
        if os.path.isdir(paper_dir):
            shutil.rmtree(paper_dir, ignore_errors=True)
    except Exception as e:
        logger.warning("Could not remove mineru output dir %s: %s", paper_dir, e)


@app.delete("/database/{paper_id}")
async def delete_paper(paper_id: str):
    """Delete one paper from SQLite and remove its mineru_output folder."""
    if not _valid_paper_id(paper_id):
        raise HTTPException(status_code=400, detail="Invalid paper id")
    try:
        conn = sqlite3.connect(settings.database_path)
        cur = conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
        deleted = cur.rowcount
        conn.commit()
        conn.close()
        if deleted == 0:
            raise HTTPException(status_code=404, detail="Paper not found")
        _delete_paper_artifacts(paper_id)
        logger.info("Deleted paper %s", paper_id)
        return {"status": "success", "deleted": 1, "id": paper_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete paper failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete paper")


@app.delete("/database")
async def delete_all_papers():
    """Delete every paper and associated output directories."""
    try:
        conn = sqlite3.connect(settings.database_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id FROM papers").fetchall()
        conn.execute("DELETE FROM papers")
        conn.commit()
        conn.close()
        for r in rows:
            _delete_paper_artifacts(r["id"])
        n = len(rows)
        logger.info("Deleted all papers (%s rows)", n)
        return {"status": "success", "deleted": n}
    except Exception as e:
        logger.error(f"Delete all papers failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear database")


def _safe_download_basename(original_filename: str) -> str:
    """Stem from stored DB filename (usually the PDF name), ASCII-safe for Content-Disposition."""
    base = os.path.splitext((original_filename or "paper").strip())[0] or "paper"
    safe = re.sub(r"[^\w\-\.\(\)\s]+", "_", base, flags=re.UNICODE)
    safe = re.sub(r"\s+", " ", safe).strip(" ._") or "paper"
    return safe[:180]


@app.get("/download/{paper_id}")
async def download_markdown(paper_id: str):
    """Download markdown; filename uses the original upload name (not MinerU internal names like full.md)."""
    try:
        conn = sqlite3.connect(settings.database_path)
        conn.row_factory = sqlite3.Row
        paper = conn.execute(
            "SELECT filename, content FROM papers WHERE id = ?",
            (paper_id,),
        ).fetchone()
        conn.close()

        if not paper:
            raise HTTPException(404, "Paper not found")

        download_stem = _safe_download_basename(paper["filename"] or "paper")
        download_name = f"{download_stem}.md"
        body = paper["content"] or ""

        if body.strip():
            disp_ascii = f'attachment; filename="{download_name}"'
            disp_utf8 = f"filename*=UTF-8''{quote(download_name)}"
            return Response(
                content=body.encode("utf-8"),
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f"{disp_ascii}; {disp_utf8}"},
            )

        paper_dir = os.path.join(settings.mineru_output_dir, paper_id)
        if not os.path.exists(paper_dir):
            raise HTTPException(404, "Paper directory not found")

        md_files = []
        for root, _, files in os.walk(paper_dir):
            for file in files:
                if file.endswith(".md"):
                    md_files.append(os.path.join(root, file))

        if not md_files:
            raise HTTPException(404, "No markdown files found")

        def _sort_key(p: str) -> tuple:
            base = os.path.basename(p).lower()
            is_full = 1 if base == "full.md" else 0
            return (is_full, -os.path.getsize(p))

        md_path = sorted(md_files, key=_sort_key)[0]

        return FileResponse(
            md_path,
            media_type="text/markdown",
            filename=download_name,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        raise HTTPException(500, "Failed to download markdown file")


# Mount frontend last so API routes above take precedence on this app instance.
app.mount("/app/static", StaticFiles(directory=settings.static_path, html=True), name="static")
app.mount("/app/notebook", StaticFiles(directory=settings.note_path, html=True), name="notebook")
app.mount("/app/research", StaticFiles(directory=settings.research_path, html=True), name="research")
app.mount("/app", StaticFiles(directory=settings.frontend_path, html=True), name="main_app")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)