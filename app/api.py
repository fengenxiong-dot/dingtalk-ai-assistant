import secrets

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from . import admin
from .config import Settings
from .db import Asset, Database, Task, User
from .service import Service, UserError
from .storage import Storage


class UserInput(BaseModel):
    corp_id: str = Field(min_length=1, max_length=128)
    staff_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=128)
    text_limit: int = Field(default=50, ge=0)
    image_limit: int = Field(default=5, ge=0)
    enabled: bool = True


class DevMessage(BaseModel):
    staff_id: str = Field(default="demo", min_length=1, max_length=128)
    message_id: str = Field(min_length=1, max_length=512)
    text: str = Field(max_length=10000)


def create_app(settings=None, db=None):
    settings = settings or Settings.load()
    settings.validate()
    db = db or Database(settings)
    service = Service(db, settings)
    app = FastAPI(title="企业 AI 管理接口", version="0.1.0", description="后台管理接口。用户会话内容不在管理员列表展示；员工从钉钉使用。数据库请先运行 python -m app init。")
    security = HTTPBearer(auto_error=False)

    def check_admin(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
        if not settings.admin_token or not credentials or not secrets.compare_digest(credentials.credentials.encode(), settings.admin_token.encode()):
            raise HTTPException(401, "需要管理员令牌")

    def check_dev(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
        if settings.provider != "mock" or not settings.dev_token:
            raise HTTPException(404, "本地模拟入口未启用")
        if not credentials or not secrets.compare_digest(credentials.credentials.encode(), settings.dev_token.encode()):
            raise HTTPException(401, "需要本地测试令牌")

    @app.exception_handler(UserError)
    async def user_error(_, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health", tags=["运行状态"])
    def health():
        try:
            with db.Session() as s:
                s.execute(text("SELECT id FROM write_gate WHERE id = 1")).scalar_one()
        except Exception:
            raise HTTPException(503, "数据库未初始化或不可用") from None
        return {"status": "ok", "provider": settings.provider}

    @app.get("/admin/overview", dependencies=[Depends(check_admin)], tags=["使用概览"])
    def get_overview():
        return admin.overview(db, settings)

    @app.get("/admin/users", dependencies=[Depends(check_admin)], tags=["用户与额度"])
    def get_users():
        return admin.users(db)

    @app.put("/admin/users", dependencies=[Depends(check_admin)], tags=["用户与额度"])
    def put_user(body: UserInput):
        return {"id": service.provision(**body.model_dump())}

    @app.get("/admin/tasks", dependencies=[Depends(check_admin)], tags=["任务记录"])
    def get_tasks(page: int = Query(default=1, ge=1)):
        return admin.tasks(db, page)

    @app.get("/admin/costs", dependencies=[Depends(check_admin)], tags=["使用概览"])
    def get_costs(days: int = Query(default=7, ge=1, le=366)):
        return admin.costs(db, days)

    @app.get("/admin/deliveries", dependencies=[Depends(check_admin)], tags=["任务记录"])
    def get_deliveries():
        return admin.deliveries(db)

    @app.post("/admin/deliveries/{delivery_id}/resend", dependencies=[Depends(check_admin)], tags=["任务记录"])
    def resend(delivery_id: int):
        return admin.resend(db, delivery_id)

    @app.post("/dev/messages", dependencies=[Depends(check_dev)], tags=["仅模拟模式"])
    def message(body: DevMessage):
        return service.receive("local", body.staff_id, body.message_id, body.text)

    @app.get("/dev/tasks/{task_id}", dependencies=[Depends(check_dev)], tags=["仅模拟模式"])
    def dev_task(task_id: int, staff_id: str = "demo"):
        with db.Session() as s:
            row = s.scalar(select(Task).join(User, User.id == Task.user_id).where(Task.id == task_id, User.corp_id == "local", User.staff_id == staff_id, User.enabled.is_(True)))
            if not row:
                raise HTTPException(404, "找不到本人任务")
            return {"id": row.id, "status": row.status, "result": row.result, "asset_id": row.result_asset_id, "error": row.error}

    @app.get("/dev/assets/{asset_id}", dependencies=[Depends(check_dev)], tags=["仅模拟模式"])
    def dev_asset(asset_id: int, staff_id: str = "demo"):
        with db.Session() as s:
            row = s.scalar(select(Asset).join(User, User.id == Asset.user_id).where(Asset.id == asset_id, User.corp_id == "local", User.staff_id == staff_id, User.enabled.is_(True)))
            if not row or row.expired or not row.path:
                raise HTTPException(404, "图片不存在或已过期")
            path = Storage(settings).resolve(row.path)
            if not path.exists():
                raise HTTPException(404, "图片文件已不存在")
            return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})

    return app
