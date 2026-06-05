"""
模态分析独立服务入口。

启动方式：
    # 开发（热重载）
    uvicorn src.modal_service.main:app --reload --port 8001

    # 生产（多 worker）
    gunicorn src.modal_service.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8001

前端访问：
    http://127.0.0.1:8001/static/modal_viewer.html
    http://127.0.0.1:8001/docs   （Swagger UI）
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .middleware import add_test_mesh_route_rewrite_middleware
from .routes import router

app = FastAPI(
    title="Modal Analysis Service",
    description="模态振型可视化服务，独立于 ODB 管线运行。",
    version="1.0.0",
)

# CORS（允许前端跨域调用，生产环境可收窄 allow_origins）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# 挂载静态文件（tools/ 目录，提供 modal_viewer.html）
_tools_dir = Path(__file__).resolve().parent.parent.parent / "tools"
if _tools_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_tools_dir)), name="static")

# 注册路由
app.include_router(router)
add_test_mesh_route_rewrite_middleware(app)
