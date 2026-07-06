# -*- coding: utf-8 -*-
"""
Model Maker Web v3 — FastAPI 진입점 (Gemma 4 E4B / Ollama)
실행: uvicorn model_maker_web_v3.backend.main:app --host 0.0.0.0 --port 8083 --reload
"""
import os
import subprocess
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

from .api_routes import router

app = FastAPI(
    title='Model Maker Web v3',
    description='Inverter Modbus PDF → RTU register map 3-stage pipeline (Gemma 4 27B)',
    version='3.0.0',
)


@app.on_event('shutdown')
def shutdown_ollama():
    """서버 종료 시 Ollama 프로세스도 함께 종료."""
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'ollama.exe'], check=False,
                       capture_output=True)
    except Exception:
        pass

app.include_router(router, prefix='/api')

# 브라우저 캐싱 방지: HTML/JS/CSS 변경 즉시 반영
_NO_CACHE_HEADERS = {
    'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
    'Pragma': 'no-cache',
    'Expires': '0',
}


@app.middleware('http')
async def add_no_cache_headers(request: Request, call_next):
    response: Response = await call_next(request)
    path = request.url.path
    # API 는 캐싱 무관, static/HTML 만 no-cache
    if path == '/' or path.startswith('/static/') or path.endswith(('.html', '.js', '.css')):
        for k, v in _NO_CACHE_HEADERS.items():
            response.headers[k] = v
    return response


# 정적 파일
_STATIC = os.path.join(os.path.dirname(__file__), '..', 'static')
app.mount('/static', StaticFiles(directory=_STATIC), name='static')


@app.get('/')
def index():
    return FileResponse(
        os.path.join(_STATIC, 'index.html'),
        headers=_NO_CACHE_HEADERS,
    )
