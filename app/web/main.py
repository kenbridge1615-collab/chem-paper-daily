"""FastAPI wiring for the local web app (design section 9).

Routes stay thin: they parse the request, call the pure helpers in
views.py / actions.py / uploads.py in a worker thread, and render a template.
The server binds to 127.0.0.1:8765 only (requirements R5-4).

Run with:  uv run python -m app.web.main --open-browser
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
import webbrowser
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app import daily_job
from app.config import LOG_DIR, PDF_DIR, Config, load_config
from app.db import init_db
from app.http_client import httpx_fetcher
from app.logging_setup import setup_logging
from app.web import actions, security, views
from app.web.uploads import UploadError

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

log = logging.getLogger("chem_paper_daily.web")


def _default_fetch():
    return httpx_fetcher(default_headers={"User-Agent": "chem-paper-daily/0.1"})


def create_app(
    cfg: Config | None = None,
    *,
    pdf_dir: Path | None = None,
    run_job: Callable[..., object] | None = None,
    fetch_factory: Callable[[], object] | None = None,
    allow_test_host: bool = False,
) -> FastAPI:
    """Build the app. Arguments are injectable for tests."""
    cfg = cfg or load_config()
    pdf_dir = pdf_dir or PDF_DIR
    run_job = run_job or daily_job.run_once
    fetch_factory = fetch_factory or _default_fetch

    # Create the schema once at startup so read-only pages work on a fresh DB.
    init_db(cfg.db_path).close()

    app = FastAPI(title="Chem Paper Daily", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["status_labels"] = views.STATUS_LABELS

    def db():
        # A fresh connection per call; always used within one thread.
        return closing(init_db(cfg.db_path))

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if not security.host_allowed(request.headers.get("host"), allow_test=allow_test_host):
            return PlainTextResponse("このアプリは 127.0.0.1 からのみ利用できます。", status_code=403)
        if request.method not in ("GET", "HEAD") and not security.origin_allowed(
            request.headers.get("origin"), request.headers.get("referer"),
            allow_test=allow_test_host,
        ):
            return PlainTextResponse("外部のページからの操作は受け付けません。", status_code=403)
        return await call_next(request)

    def render(request: Request, name: str, context: dict, status_code: int = 200) -> HTMLResponse:
        context = {
            **context,
            "flash": views.flash_message(request.query_params.get("msg")),
            "nav": request.url.path,
        }
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    def message(request: Request, text: str, *, status_code: int = 400, back: str = "/") -> HTMLResponse:
        return render(request, "message.html", {"message": text, "back": back}, status_code)

    def redirect(url: str, msg: str | None = None) -> RedirectResponse:
        return RedirectResponse(views.with_message(url, msg), status_code=303)

    # --- Pages ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def today(request: Request):
        def work():
            with db() as conn:
                return views.today_page(conn, cfg)
        return render(request, "index.html", await run_in_threadpool(work))

    @app.get("/papers", response_class=HTMLResponse)
    async def papers(request: Request, q: str = "", genre: str = "", journal: str = ""):
        def work():
            with db() as conn:
                return views.papers_page(conn, q=q, genre=genre, journal=journal)
        return render(request, "papers.html", await run_in_threadpool(work))

    @app.get("/papers/{paper_id}", response_class=HTMLResponse)
    async def paper_detail(request: Request, paper_id: int):
        def work():
            with db() as conn:
                return views.paper_detail_page(conn, cfg, paper_id)
        ctx = await run_in_threadpool(work)
        if ctx is None:
            return message(request, "論文が見つかりません。", status_code=404, back="/papers")
        return render(request, "paper_detail.html", ctx)

    @app.get("/library", response_class=HTMLResponse)
    async def library(request: Request, q: str = "", status: str = ""):
        def work():
            with db() as conn:
                return views.library_page(conn, q=q, status=status)
        return render(request, "library.html", await run_in_threadpool(work))

    @app.get("/library/add", response_class=HTMLResponse)
    async def library_add_form(request: Request):
        return render(request, "library_add.html", {"doi": "", "title": ""})

    @app.get("/settings", response_class=HTMLResponse)
    async def settings(request: Request):
        def work():
            with db() as conn:
                return views.settings_page(conn, cfg)
        return render(request, "settings.html", await run_in_threadpool(work))

    @app.get("/pdfs/{name}")
    async def serve_pdf(name: str):
        from app.web.uploads import resolve_pdf_path

        path = resolve_pdf_path(pdf_dir, name)
        if path is None or not path.is_file():
            return PlainTextResponse("PDF が見つかりません。", status_code=404)
        return FileResponse(
            path, media_type="application/pdf",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    # --- Actions (POST, then redirect) -------------------------------------

    @app.post("/papers/{paper_id}/feedback")
    async def feedback(request: Request, paper_id: int):
        form = await request.form()
        nxt = views.safe_next_url(form.get("next"), f"/papers/{paper_id}")
        try:
            rating = int(form.get("rating", "0"))
        except (TypeError, ValueError):
            rating = 0

        def work():
            with db() as conn:
                actions.set_feedback(conn, paper_id, rating)
        try:
            await run_in_threadpool(work)
        except actions.ActionError as exc:
            return message(request, str(exc), back=nxt)
        return redirect(nxt, "feedback_saved")

    @app.post("/papers/{paper_id}/library")
    async def add_to_library(request: Request, paper_id: int):
        form = await request.form()
        nxt = views.safe_next_url(form.get("next"), f"/papers/{paper_id}")

        def work():
            with db() as conn:
                actions.add_to_library(conn, paper_id)
        try:
            await run_in_threadpool(work)
        except actions.ActionError as exc:
            return message(request, str(exc), back=nxt)
        return redirect(nxt, "library_added")

    # Registered before /library/{paper_id} so "add" is not parsed as an id.
    @app.post("/library/add")
    async def library_add(request: Request):
        form = await request.form()
        doi = str(form.get("doi", ""))
        title = str(form.get("title", ""))
        pdf = form.get("pdf")
        has_pdf = isinstance(pdf, UploadFile) and bool(pdf.filename)

        def work():
            with db() as conn:
                result = actions.add_by_doi(conn, cfg, fetch_factory(), doi, title=title)
                if has_pdf:
                    actions.attach_pdf(conn, result.paper_id, filename=pdf.filename,
                                       stream=pdf.file, pdf_dir=pdf_dir)
                return result
        try:
            result = await run_in_threadpool(work)
        except (actions.ActionError, UploadError) as exc:
            return render(
                request, "library_add.html",
                {"doi": doi, "title": title, "error": str(exc)}, status_code=400,
            )
        finally:
            if isinstance(pdf, UploadFile):
                await pdf.close()
        return redirect(f"/papers/{result.paper_id}", "pdf_saved" if has_pdf else "library_added")

    @app.post("/library/{paper_id}")
    async def update_library(request: Request, paper_id: int):
        form = await request.form()
        nxt = views.safe_next_url(form.get("next"), f"/papers/{paper_id}")
        status = str(form.get("status", "want"))
        notes = str(form.get("notes", ""))

        def work():
            with db() as conn:
                actions.update_library(conn, paper_id, status=status, notes=notes)
        try:
            await run_in_threadpool(work)
        except actions.ActionError as exc:
            return message(request, str(exc), back=nxt)
        return redirect(nxt, "library_saved")

    @app.post("/library/{paper_id}/pdf")
    async def upload_pdf(request: Request, paper_id: int):
        form = await request.form()
        nxt = views.safe_next_url(form.get("next"), f"/papers/{paper_id}")
        pdf = form.get("pdf")
        if not isinstance(pdf, UploadFile) or not pdf.filename:
            return message(request, "PDF ファイルを選んでください。", back=nxt)

        def work():
            with db() as conn:
                actions.attach_pdf(conn, paper_id, filename=pdf.filename,
                                   stream=pdf.file, pdf_dir=pdf_dir)
        try:
            await run_in_threadpool(work)
        except (actions.ActionError, UploadError) as exc:
            return message(request, str(exc), back=nxt)
        finally:
            await pdf.close()
        return redirect(nxt, "pdf_saved")

    @app.post("/extra")
    async def extra(request: Request):
        def work():
            with db() as conn:
                return actions.request_extra(conn, cfg, run_job=run_job)
        key = await run_in_threadpool(work)
        return redirect("/", key)

    return app


def _port_in_use(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _open_browser_when_ready(url: str, host: str, port: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chem Paper Daily web app")
    parser.add_argument("--open-browser", action="store_true", help="open the browser once ready")
    args = parser.parse_args()

    import uvicorn

    url = f"http://{security.HOST}:{security.PORT}/"
    if _port_in_use(security.HOST, security.PORT):
        # Already running (e.g. start.bat double-clicked twice): just open it.
        print(f"Chem Paper Daily はすでに起動しています: {url}")
        if args.open_browser:
            webbrowser.open(url)
        return

    setup_logging(LOG_DIR)
    app = create_app()
    if args.open_browser:
        threading.Thread(
            target=_open_browser_when_ready, args=(url, security.HOST, security.PORT),
            daemon=True,
        ).start()
    print(f"Chem Paper Daily: {url}  (この黒い画面を閉じるとアプリが止まります)")
    # Host is fixed to 127.0.0.1 so the app is reachable only from this PC.
    uvicorn.run(app, host=security.HOST, port=security.PORT, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    main()
