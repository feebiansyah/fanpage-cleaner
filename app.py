import asyncio
import json
import re
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from config import Settings
from errors import AppError
from graph_api import GraphClient
from failure_diagnosis import FailureDiagnosis
from sessions import MemoryStore
from content import ContentLoader
from content_jobs import ContentJobs
from bulk_queue import BulkQueue
from bulk_scheduler import BulkScheduler
from bulk_checkpoint import Checkpoints
from bulk_targets import validate_selections
from single_delete import SingleDelete
from bulk_delete import BulkDelete
from bulk_executor import PagePostExecutor
from dataclasses import replace


BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=BASE_DIR / "templates")
SESSION_COOKIE = "fanpage_session"
BROWSER_COOKIE = "fanpage_browser"


def cookie(response, name, value, settings, ttl):
    response.set_cookie(name, value, max_age=ttl, httponly=True, samesite="strict",
                        secure=settings.cookie_secure, path="/")


def delete_session_cookie(response, settings):
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True,
                           secure=settings.cookie_secure, samesite="strict")


def session_payload(session, state="disconnected"):
    if session is None:
        message = "Sesi kedaluwarsa. Hubungkan kembali dengan user access token." if state == "expired" else "Belum terhubung ke Facebook."
        return {"connected": False, "state": state, "pages": [], "count": 0, "message": message}
    pages = session.metadata()
    message = ("Fanpage berhasil dimuat. Pilih Fanpage dan gunakan Load Posts untuk audit." if pages else
               "Tidak ada Fanpage yang dikembalikan oleh Facebook. Periksa akses Fanpage dan izin token.")
    return {"connected": True, "state": "connected" if pages else "empty",
            "pages": pages, "count": len(pages), "message": message,
            "session_remaining_seconds": max(0, int(session.expires_at - time.monotonic()))}


def check_origin(request: Request, settings: Settings, mutation=False):
    # No CORS: custom header also prevents cross-site GET bootstrap/status.
    if (request.headers.get("x-fanpage-client") != "1"
            or request.headers.get("sec-fetch-site") not in {None, "same-origin"}):
        raise AppError("origin_denied", 403)
    origin = request.headers.get("origin")
    if origin != settings.app_origin and (mutation or origin is not None):
        raise AppError("origin_denied", 403)
    if str(request.base_url).rstrip("/") != settings.app_origin:
        raise AppError("origin_denied", 403)


async def read_json(request: Request, settings: Settings):
    length = request.headers.get("content-length")
    if length is not None:
        try:
            size = int(length)
        except ValueError:
            raise AppError("bad_request") from None
        if size < 0 or size > settings.max_body_bytes:
            raise AppError("body_too_large", 413)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise AppError("bad_request")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > settings.max_body_bytes:
            raise AppError("body_too_large", 413)
        body.extend(chunk)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise AppError("bad_request") from None
    return payload


async def read_token(request: Request, settings: Settings):
    payload = await read_json(request, settings)
    if not isinstance(payload, dict) or set(payload) != {"token"}:
        raise AppError("bad_request")
    token = payload["token"]
    if not isinstance(token, str):
        raise AppError("token_invalid")
    if not token.strip():
        raise AppError("token_empty")
    if len(token) > settings.max_token_length or not re.fullmatch(r"[!-~]+", token):
        raise AppError("token_invalid")
    return token


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None, checkpoint_root=None):
    settings = settings or Settings.from_env()

    async def cleanup_loop(store):
        while True:
            await asyncio.sleep(settings.cleanup_interval)
            store.cleanup()

    @asynccontextmanager
    async def lifespan(application):
        # Only one client, no shared Authorization default, no env proxies, no redirects.
        timeout = httpx.Timeout(settings.request_timeout, connect=settings.connect_timeout,
                                pool=settings.connect_timeout)
        limits = httpx.Limits(max_connections=settings.max_connections,
                             max_keepalive_connections=settings.max_keepalive_connections)
        async with httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=False,
                                     trust_env=False, transport=transport) as http:
            application.state.http = http
            application.state.graph = GraphClient(http, settings, application.state.store)
            application.state.jobs.bind(application.state.graph)
            application.state.bulk_scheduler.real_executor=PagePostExecutor(application.state.graph,application.state.bulk)
            cleanup = asyncio.create_task(cleanup_loop(application.state.store))
            await application.state.bulk_scheduler.start()
            try:
                yield
            finally:
                await application.state.bulk_scheduler.shutdown()
                cleanup.cancel()
                with suppress(asyncio.CancelledError):
                    await cleanup
                application.state.store.clear()

    application = FastAPI(title="Fanpage Cleaner", lifespan=lifespan,
                          docs_url=None, redoc_url=None, openapi_url=None)
    application.state.settings = settings
    application.state.store = MemoryStore(settings)
    application.state.jobs = ContentJobs(application.state.store, settings)
    application.state.store.content_jobs = application.state.jobs
    application.state.bulk=BulkQueue(application.state.store,settings,Checkpoints(checkpoint_root or BASE_DIR/'runtime'/'dry-run',settings))
    application.state.bulk_scheduler=BulkScheduler(application.state.bulk)
    application.state.store.bulk_queue=application.state.bulk
    application.state.single_delete=SingleDelete(application.state.store,application.state.bulk)
    application.state.store.single_delete=application.state.single_delete
    application.state.bulk_delete=BulkDelete(application.state.store,application.state.bulk,application.state.single_delete)
    application.state.store.bulk_delete=application.state.bulk_delete
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])
    application.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    @application.middleware("http")
    async def local_security(request, call_next):
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            response = JSONResponse(AppError("origin_denied", 403).payload(), status_code=403)
        else:
            response = await call_next(request)
        if request.url.path.startswith("/api/") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' https://fbcdn.net https://*.fbcdn.net https://fbsbx.com https://*.fbsbx.com; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        return response

    @application.exception_handler(AppError)
    async def app_error(request, error):
        headers = {"Cache-Control": "no-store"}
        if error.retry_after is not None:
            headers["Retry-After"] = str(error.retry_after)
        payload = error.payload()
        if hasattr(error, "cause_code"):
            payload["error"]["cause_code"] = error.cause_code
        if hasattr(error, "sources"):
            payload["sources"] = error.sources
        return JSONResponse(payload, status_code=error.status, headers=headers)

    @application.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={
            "max_token_length": settings.max_token_length})

    @application.get("/health")
    async def health():
        return {"status": "ok"}

    @application.get("/api/session")
    async def status(request: Request):
        check_origin(request, application.state.settings)
        store = application.state.store
        browser_id, _, _ = store.bootstrap(request.cookies.get(BROWSER_COOKIE))
        session_id = request.cookies.get(SESSION_COOKIE)
        session = store.session(browser_id, session_id)
        response = JSONResponse(session_payload(session, "expired" if session_id and session is None else "disconnected"))
        cookie(response, BROWSER_COOKIE, browser_id, settings, settings.browser_ttl)
        if session_id and session is None:
            delete_session_cookie(response, settings)
        return response

    @application.post("/api/connect")
    async def connect(request: Request):
        current = application.state.settings
        check_origin(request, current, mutation=True)
        store = application.state.store
        browser_id = request.cookies.get(BROWSER_COOKIE)
        if store.browser(browser_id) is None:
            raise AppError("browser_required", 409)
        # Bound even slow/incomplete bodies; body is never printed by validation.
        try:
            token = await asyncio.wait_for(read_token(request, current), current.connect_timeout)
        except asyncio.TimeoutError:
            raise AppError("timeout", 504) from None
        token_key = store.token_key(token)
        store.begin(browser_id, request.cookies.get(SESSION_COOKIE), token_key)
        try:
            pages = await asyncio.wait_for(application.state.graph.accounts(token, token_key), current.operation_timeout)
            session_id, session = store.replace(browser_id, token, pages)
            response = JSONResponse(session_payload(session))
            cookie(response, SESSION_COOKIE, session_id, settings, settings.session_ttl)
            cookie(response, BROWSER_COOKIE, browser_id, settings, settings.browser_ttl)
            return response
        except asyncio.TimeoutError:
            raise AppError("timeout", 504) from None
        except AppError:
            raise
        except Exception:
            # Unexpected exceptions must not become token-bearing tracebacks/logs.
            raise AppError("internal_error", 500) from None
        finally:
            store.finish(browser_id)

    @application.post("/api/disconnect")
    async def disconnect(request: Request):
        check_origin(request, application.state.settings, mutation=True)
        application.state.store.disconnect(request.cookies.get(BROWSER_COOKIE), request.cookies.get(SESSION_COOKIE))
        response = JSONResponse(session_payload(None))
        delete_session_cookie(response, settings)
        return response

    def validate_page_id(page_id):
        if not isinstance(page_id, str) or not re.fullmatch(r"[0-9]{1,40}", page_id):
            raise AppError("invalid_page")

    @application.post("/api/posts/load")
    async def load_posts(request: Request):
        current = application.state.settings
        check_origin(request, current, mutation=True)
        try:
            payload = await asyncio.wait_for(read_json(request, current), current.connect_timeout)
        except asyncio.TimeoutError:
            raise AppError("timeout", 504) from None
        except AppError as error:
            if error.code == "bad_request":
                raise AppError("invalid_load_body") from None
            raise
        if not isinstance(payload, dict) or set(payload) != {"page_id", "limit"}:
            raise AppError("invalid_load_body")
        page_id, limit = payload["page_id"], payload["limit"]
        validate_page_id(page_id)
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise AppError("invalid_limit")
        browser_id, session_id = request.cookies.get(BROWSER_COOKIE), request.cookies.get(SESSION_COOKIE)
        store = application.state.store
        session, page = store.require_page(browser_id, session_id, page_id)
        if not page.access_token:
            raise AppError("page_token_missing", 403)
        browser = store.begin_load(browser_id, session_id, store.token_key(page.access_token), limit, page_id)
        deadline = min(session.expires_at, time.monotonic() + current.posts_timeout)
        loader = ContentLoader(application.state.graph, store, current)
        try:
            browser.load_task = asyncio.create_task(loader.load(browser_id, session_id, page, limit, deadline))
            result = await browser.load_task
            store.require_page(browser_id, session_id, page_id)
            store.audits.put(session_id, result, session.expires_at)
            return JSONResponse(store.audits.get(session_id, page_id))
        except asyncio.CancelledError:
            if session.expires_at <= time.monotonic():
                raise AppError("session_expired", 401) from None
            if store.session(browser_id, session_id) is None:
                raise AppError("load_cancelled", 409) from None
            raise
        except AppError:
            raise
        except Exception:
            raise AppError("internal_error", 500) from None
        finally:
            store.finish_load(browser_id, session_id)

    @application.get("/api/posts")
    async def cached_posts(request: Request, page_id: str):
        check_origin(request, application.state.settings)
        validate_page_id(page_id)
        browser_id, session_id = request.cookies.get(BROWSER_COOKIE), request.cookies.get(SESSION_COOKIE)
        store = application.state.store
        store.require_page(browser_id, session_id, page_id)
        cached = store.audits.get(session_id, page_id)
        return cached or {"page_id": page_id, "state": "not_loaded", "items": [], "displayed": 0}

    @application.post("/api/posts/jobs")
    async def start_content_job(request: Request):
        check_origin(request, settings, mutation=True)
        try:
            payload = await asyncio.wait_for(read_json(request, settings), settings.connect_timeout)
        except asyncio.TimeoutError:
            raise AppError("timeout",504) from None
        if not isinstance(payload, dict) or set(payload) != {"page_id","limit"}:
            raise AppError("invalid_load_body")
        validate_page_id(payload['page_id'])
        if type(payload['limit']) is not int or not 1 <= payload['limit'] <= 5000:
            raise AppError('invalid_limit')
        job=application.state.jobs.start(request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE),payload['page_id'],payload['limit'])
        return application.state.jobs.snapshot(job)

    def owned_job(request, job_id, mutation=False):
        check_origin(request,settings,mutation=mutation)
        return application.state.jobs.owned(request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE),job_id)

    @application.get("/api/posts/jobs/{job_id}")
    async def content_job_status(request: Request, job_id: str):
        return application.state.jobs.snapshot(owned_job(request,job_id))

    @application.post("/api/posts/jobs/{job_id}/step")
    async def content_job_step(request: Request, job_id: str):
        job=owned_job(request,job_id,True)
        try:
            payload=await asyncio.wait_for(read_json(request,settings),settings.connect_timeout)
        except asyncio.TimeoutError:
            raise AppError('timeout',504) from None
        if not isinstance(payload,dict) or set(payload) != {'revision'}:
            raise AppError('invalid_revision',409)
        return await application.state.jobs.step(job,payload['revision'])

    @application.post("/api/posts/jobs/{job_id}/stop")
    async def content_job_stop(request: Request, job_id: str):
        return await application.state.jobs.stop(owned_job(request,job_id,True))

    @application.post("/api/dry-run/jobs")
    async def start_dry_run(request: Request):
        check_origin(request,settings,mutation=True)
        try:
            payload=await asyncio.wait_for(read_json(request,replace(settings,max_body_bytes=settings.dry_body_bytes)),settings.connect_timeout)
        except asyncio.TimeoutError:
            raise AppError('timeout',504) from None
        browser_id,sid=request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE)
        targets,duplicates,pages=validate_selections(application.state.store,browser_id,sid,payload)
        job=application.state.bulk.add(browser_id,sid,targets,duplicates,pages)
        return application.state.bulk.snapshot(job)

    async def single_delete_body(request):
        check_origin(request,settings,mutation=True)
        try:
            return await asyncio.wait_for(read_json(request,settings),settings.connect_timeout)
        except asyncio.TimeoutError:
            raise AppError('timeout',504) from None

    @application.post('/api/posts/delete/prepare')
    async def prepare_single_delete(request: Request):
        payload=await single_delete_body(request)
        return application.state.single_delete.prepare(request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE),payload)

    @application.post('/api/posts/delete/execute')
    async def execute_single_delete(request: Request):
        payload=await single_delete_body(request)
        return await application.state.single_delete.execute(application.state.graph,request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE),payload)

    @application.get('/api/posts/delete/last')
    async def last_single_delete(request: Request):
        check_origin(request,settings)
        return application.state.single_delete.last(request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE))

    @application.post('/api/reels/delete/prepare')
    async def prepare_single_reel(request: Request):
        payload=await single_delete_body(request)
        return application.state.single_delete.prepare(request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE),payload,'reel_video')

    @application.post('/api/reels/delete/execute')
    async def execute_single_reel(request: Request):
        payload=await single_delete_body(request)
        return await application.state.single_delete.execute(application.state.graph,request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE),payload,'reel_video')

    @application.post('/api/bulk-delete/prepare')
    async def prepare_bulk_delete(request: Request):
        payload=await single_delete_body(request)
        return application.state.bulk_delete.prepare(request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE),payload)

    failure_diagnosis=FailureDiagnosis(application.state.store,application.state.bulk_delete,application.state.single_delete)

    @application.post('/api/delete-diagnosis')
    async def diagnose_failed_delete(request: Request):
        payload=await single_delete_body(request)
        return await failure_diagnosis.run(application.state.graph,request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE),payload)

    @application.post('/api/bulk-delete/confirm')
    async def confirm_bulk_delete(request: Request):
        payload=await single_delete_body(request)
        return application.state.bulk_delete.confirm(request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE),payload)

    @application.get('/api/bulk-delete/jobs/active')
    async def active_bulk_delete(request: Request):
        check_origin(request,settings)
        return application.state.bulk_delete.active(request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE))

    def owned_real(request,job_id,mutation=False):
        check_origin(request,settings,mutation=mutation)
        return application.state.bulk_delete.owned(request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE),job_id)

    @application.get('/api/bulk-delete/jobs/{job_id}')
    async def real_bulk_status(request: Request,job_id: str):
        return application.state.bulk.snapshot(owned_real(request,job_id))

    @application.post('/api/bulk-delete/jobs/{job_id}/stop')
    async def real_bulk_stop(request: Request,job_id: str):
        job=owned_real(request,job_id,True)
        application.state.bulk.cancel(job)
        return application.state.bulk.snapshot(job)

    def owned_dry(request,job_id,mutation=False):
        check_origin(request,settings,mutation=mutation)
        job=application.state.bulk.owned(request.cookies.get(BROWSER_COOKIE),request.cookies.get(SESSION_COOKIE),job_id)
        if job.mode!='dry_run': raise AppError('dry_job_not_found',404)
        return job

    @application.get("/api/dry-run/jobs/{job_id}")
    async def dry_status(request: Request,job_id: str,offset: int=0):
        if offset<0 or offset>settings.dry_max_targets:
            raise AppError('invalid_selection')
        return application.state.bulk.snapshot(owned_dry(request,job_id),offset)

    @application.post("/api/dry-run/jobs/{job_id}/stop")
    async def dry_stop(request: Request,job_id: str):
        job=owned_dry(request,job_id,True)
        application.state.bulk.cancel(job)
        return application.state.bulk.snapshot(job)

    return application


app = create_app()
