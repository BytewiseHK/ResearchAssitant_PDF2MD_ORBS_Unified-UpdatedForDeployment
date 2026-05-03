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
import copy
import secrets
import shutil
import threading
from pathlib import Path
from typing import List, Optional, Dict

from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.proxy_headers import ProxyHeadersMiddleware
from starlette.status import HTTP_401_UNAUTHORIZED
from pydantic import BaseModel
from pydantic_settings import BaseSettings
import torch
import pypdfium2
from openai import AsyncOpenAI
import mineru
from mineru.cli.common import convert_pdf_bytes_to_bytes_by_pypdfium2
from mineru.data.data_reader_writer import FileBasedDataWriter
from mineru.utils.enum_class import MakeMode
from mineru.backend.pipeline.pipeline_analyze import doc_analyze as pipeline_doc_analyze
from mineru.backend.pipeline.pipeline_middle_json_mkcontent import union_make as pipeline_union_make
from mineru.backend.pipeline.model_json_to_middle_json import result_to_middle_json as pipeline_result_to_middle_json




# Add the backend directory to Python path
#sys.path.append(os.path.dirname(os.path.abspath(__file__)))

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
    session_cookie_name: str = "ra_sid"
    session_ttl_seconds: int = 2 * 60 * 60  # 2 hours
    
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
if os.environ.get("SESSION_TTL_SECONDS"):
    try:
        settings.session_ttl_seconds = int(os.environ["SESSION_TTL_SECONDS"])
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

async def get_relevant_papers_by_points(prompt: str):
    try:
        conn = sqlite3.connect(settings.database_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        c.execute("SELECT id, filename, content, points FROM papers")
        papers = c.fetchall()
        
        if not papers:
            return []

        processed_papers = []
        for paper in papers:
            try:
                points = json.loads(paper['points']) if paper['points'] else []
            except json.JSONDecodeError:
                points = [paper['points']] if paper['points'] else []
            
            relevance_score = sum(
                1 for point in points 
                if isinstance(point, str) and prompt.lower() in point.lower()
            )
            
            processed_papers.append({
                'id': paper['id'],
                'filename': paper['filename'],
                'content': paper['content'],
                'points': points,
                'relevance': relevance_score
            })
        
        return sorted(
            processed_papers,
            key=lambda x: x['relevance'],
            reverse=True
        )[:len(processed_papers)-1]

    except Exception as e:
        logger.error(f"Relevant papers retrieval failed: {str(e)}")
        raise HTTPException(500, "Paper retrieval error")
    finally:
        conn.close()

# ======================
# Mineru PDF Processor
# ======================
def cleanup_resources():
    """Force cleanup of resources between batches"""
    gc.collect()
    time.sleep(0.5)
    if 'pypdfium2' in globals():
        pypdfium2.PdfDocument.__del__ = lambda self: None

def sanitize_filename(name, max_length=40):
    """Strict filename sanitization"""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = re.sub(r'[\s.]+', '_', name)
    return name[:max_length].strip('_.')

def safe_prepare_env(output_dir, pdf_file_name):
    """Create output directories"""
    try:
        safe_name = sanitize_filename(pdf_file_name)
        base_dir = Path(output_dir) / safe_name[:30]
        
        image_dir = base_dir / "img"
        md_dir = base_dir / "out"
        
        for d in [base_dir, image_dir, md_dir]:
            d.mkdir(parents=True, exist_ok=True)
        
        return str(image_dir), str(md_dir)
    except Exception as e:
        logger.error(f"Directory creation failed: {str(e)}")
        raise

def clean_md_content(content):
    """Clean up markdown content for better processing"""
    content = re.sub(r'!\[.*?\]\(.*?\)', '', content)
    content = re.sub(r'\{.*?\}', '', content)
    content = re.sub(r'\n{3,}', '\n\n', content)
    return content.strip()

def process_pdf_with_mineru(pdf_path, output_dir):
    """Process a PDF file with Mineru and return extracted text"""
    try:
        with open(pdf_path, 'rb') as f:
            pdf_bytes = f.read()
        
        pdf_name = Path(pdf_path).stem
        image_dir, md_dir = safe_prepare_env(output_dir, pdf_name)
        
        try:
            pdf_bytes = convert_pdf_bytes_to_bytes_by_pypdfium2(pdf_bytes, 0, None)
        except Exception as e:
            logger.warning(f"Page conversion failed, using original: {str(e)}")
        
        infer_results, all_image_lists, all_pdf_docs, lang_list, ocr_enabled_list = pipeline_doc_analyze(
            [pdf_bytes], ["en"], parse_method="auto", formula_enable=True, table_enable=False
        )
        
        model_json = copy.deepcopy(infer_results[0])
        image_writer = FileBasedDataWriter(image_dir)
        md_writer = FileBasedDataWriter(md_dir)
        
        middle_json = pipeline_result_to_middle_json(
            infer_results[0], all_image_lists[0], all_pdf_docs[0],
            image_writer, "en", ocr_enabled_list[0], True
        )
        
        pdf_info = middle_json["pdf_info"]
        md_content_str = pipeline_union_make(pdf_info, MakeMode.MM_MD, os.path.basename(image_dir))
        md_file_path = os.path.join(md_dir, f"{pdf_name}.md")
        
        with open(md_file_path, 'w', encoding='utf-8') as f:
            f.write(md_content_str)
        
        with open(md_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        cleaned_content = clean_md_content(content)
        logger.info(f"Successfully processed: {pdf_name}")
        return cleaned_content
        
    except Exception as e:
        logger.error(f"PDF processing failed: {str(e)}")
        traceback.print_exc()
        raise
    finally:
        cleanup_resources()

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
            model="anthropic/claude-opus-4.1",
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

# Honor X-Forwarded-* when behind Render/Vercel proxies
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

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

# Mount frontend
# Mount both frontends
app.mount("/app/static", StaticFiles(directory=settings.static_path, html=True), name="static")
app.mount("/app/notebook", StaticFiles(directory=settings.note_path, html=True), name="notebook")
app.mount("/app/research", StaticFiles(directory=settings.research_path, html=True), name="research")

app.mount("/app", StaticFiles(directory=settings.frontend_path, html=True), name="main_app")

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
        text_content = process_pdf_with_mineru(pdf_path, output_dir)

        prompt = f"""Extract exactly 5 key research points from this paper.
        Format each point EXACTLY like this:
        - [concise point here]
        Include nothing else in your response.

        Paper content:
        {text_content[:4000]}"""

        response = await client.chat.completions.create(
            model="anthropic/claude-opus-4.1",
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

@app.post("/generate")
async def generate_points(request: GenerateRequest, http_request: Request):
    """Generate research points from papers"""
    try:
        st = _get_or_create_session(http_request.state.session_id)
        client = _require_openrouter_client(st)
        relevant_papers = await get_relevant_papers_by_points(request.prompt)
        if not relevant_papers:
            return {
                "status": "success",
                "points": [{
                    "formatted_text": "No relevant papers found",
                    "clean_source": ""
                }]
            }

        context = "\n\n".join(
            f"PAPER {i+1}: {p['filename']}\n"
            f"KEY POINTS:\n" + "\n".join(p['points'][:5]) + "\n"
            f"CONTENT EXCERPT:\n{p['content'][:4000]}..."
            for i, p in enumerate(relevant_papers[:len(relevant_papers)-1])
        )

        prompt = f"""Generate 5-7 high-quality research points about: {request.prompt}
        
        Using these papers as sources:
        {context}
        
        FORMATTING RULES:
        1. Each point must start with • 
        2. Include (Source: Paper X | *name of the paper and its author* | APA 7 Citation of paper) where X is the paper number
        3. Keep points concise but informative
        4. Compare/contrast different papers when relevant
        5. Cover different aspects of the topic"""
        
        response = await client.chat.completions.create(
            model="anthropic/claude-opus-4.1",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2000
        )

        raw_points = [
            line.strip() 
            for line in response.choices[0].message.content.split('\n') 
            if line.strip().startswith('•')
        ][:7]

        paper_map = {
            f"Paper{i+1}": p['id']
            for i, p in enumerate(relevant_papers[:3])
        }

        points = []
        for line in raw_points:
            source_matches = re.findall(r'\(Source: (Paper\d+(?:, Paper\d+)*)\)', line)
            source_papers = []
            if source_matches:
                for match in source_matches[0].split(', '):
                    if match in paper_map:
                        source_papers.append({
                            'id': paper_map[match],
                            'name': next(
                                p['filename'] for p in relevant_papers 
                                if p['id'] == paper_map[match]
                            )
                        })

            point_text = line.split('• ')[1].split(' (Source:')[0].strip()
            
            points.append({
                "formatted_text": line,
                "raw_data": {
                    "text": point_text,
                    "sources": source_papers,
                    "source": ", ".join(p['name'] for p in source_papers),
                    "sourceIds": [p['id'] for p in source_papers]
                }
            })

        return {
            "status": "success", 
            "points": points,
            "paper_ids": list(paper_map.values())
        }

    except Exception as e:
        logger.error(f"Generation failed: {str(e)}")
        return {
            "status": "error",
            "points": [{
                "formatted_text": f"Error: {str(e)}",
                "clean_source": ""
            }]
        }

@app.post("/discuss")
async def discuss_points(request: GenerateRequest, http_request: Request):
    """Generate a research discussion analyzing ALL available papers"""
    try:
        st = _get_or_create_session(http_request.state.session_id)
        client = _require_openrouter_client(st)
        if request.paper_ids:
            # Get specific papers if IDs provided
            papers = []
            conn = sqlite3.connect(settings.database_path)
            conn.row_factory = sqlite3.Row
            for paper_id in request.paper_ids:
                paper = conn.execute(
                    "SELECT id, filename, content, points FROM papers WHERE id = ?",
                    (paper_id,)
                ).fetchone()
                if paper:
                    papers.append(dict(paper))
            conn.close()
        else:
            # Get ALL relevant papers for the topic
            papers = await get_relevant_papers_by_points(request.prompt)
            # Remove the slicing that limits to first few papers
            # Previously this was: papers[:len(papers)-1] which excluded papers

        if not papers:
            return {
                "status": "success",
                "discussion": "No relevant papers found for discussion"
            }

        # Build context from ALL papers
        context_parts = []
        for i, p in enumerate(papers):
            # Properly handle points (could be string JSON or list)
            if isinstance(p['points'], str):
                try:
                    points_list = json.loads(p['points'])
                except json.JSONDecodeError:
                    points_list = [p['points']] if p['points'] else []
            else:
                points_list = p['points'] or []
            
            paper_context = f"PAPER {i+1} - {p['filename']}:\n"
            paper_context += f"KEY POINTS:\n" + "\n".join(points_list[:100]) + "\n"  # Limit points to avoid token overflow
            paper_context += f"CONTENT EXCERPT:\n{p['content'][:4000]}...\n"  # Reduced excerpt length
            context_parts.append(paper_context)

        context = "\n\n".join(context_parts)

        discussion_prompt = f"""Generate a comprehensive discussion about: {request.prompt}
        
        Analyze ALL {len(papers)} provided research papers to provide a thorough examination:
        {context}
        
        GUIDELINES:
        1. Start with an overview synthesizing findings from ALL papers
        2. Compare and contrast perspectives across ALL papers, not just the first few
        3. Identify patterns, contradictions, and consensus across the entire corpus
        4. Highlight areas of agreement/disagreement among ALL sources
        5. Use [1], [2], [3], etc. for citations matching ALL paper numbers
        6. Discuss limitations and future directions considering ALL evidence
        7. Maintain academic tone but avoid jargon
        8. Length: ~800-1000 words to adequately cover ALL materials
        9. Ensure you reference findings from later papers ([4], [5], etc.) not just early ones"""

        response = await client.chat.completions.create(
            model="anthropic/claude-opus-4.1",
            messages=[{"role": "user", "content": discussion_prompt}],
            temperature=0.7,
            max_tokens=4500  # Increased for longer discussion
        )

        return {
            "status": "success",
            "discussion": response.choices[0].message.content,
            "sources": [p['filename'] for p in papers],
            "total_papers_analyzed": len(papers)
        }

    except Exception as e:
        logger.error(f"Discussion failed: {str(e)}")
        return {
            "status": "error",
            "discussion": f"Error generating discussion: {str(e)}"
        }

@app.get("/database")
async def view_database():
    """View all papers in database"""
    try:
        conn = sqlite3.connect(settings.database_path)
        conn.row_factory = sqlite3.Row
        papers = conn.execute("SELECT id, filename, content, points FROM papers").fetchall()
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

@app.get("/download/{paper_id}")
async def download_markdown(paper_id: str):
    """Download markdown version of a paper"""
    try:
        conn = sqlite3.connect(settings.database_path)
        paper = conn.execute(
            "SELECT filename FROM papers WHERE id = ?", 
            (paper_id,)
        ).fetchone()
        conn.close()
        
        if not paper:
            raise HTTPException(404, "Paper not found")
        
        paper_dir = os.path.join(settings.mineru_output_dir, paper_id)
        if not os.path.exists(paper_dir):
            raise HTTPException(404, "Paper directory not found")
        
        md_files = []
        for root, _, files in os.walk(paper_dir):
            for file in files:
                if file.endswith('.md'):
                    md_files.append(os.path.join(root, file))
        
        if not md_files:
            raise HTTPException(404, "No markdown files found")
        
        md_path = md_files[0]
        md_filename = os.path.basename(md_path)
        
        return FileResponse(
            md_path,
            media_type="text/markdown",
            filename=md_filename
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Download failed: {str(e)}")
        raise HTTPException(500, "Failed to download markdown file")

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
    
    
