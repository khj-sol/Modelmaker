# -*- coding: utf-8 -*-
"""
Model Maker Web v2 — FastAPI 진입점
실행: uvicorn model_maker_web_v2.backend.main:app --host 0.0.0.0 --port 8082 --reload
"""
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .api_routes import router

app = FastAPI(
    title='Model Maker Web v2',
    description='Inverter Modbus PDF → RTU register map 3-stage pipeline',
    version='2.0.0',
)

app.include_router(router, prefix='/api')

# 정적 파일
_STATIC = os.path.join(os.path.dirname(__file__), '..', 'static')
app.mount('/static', StaticFiles(directory=_STATIC), name='static')


@app.get('/')
def index():
    return FileResponse(os.path.join(_STATIC, 'index.html'))
