from .main import app
from . import platform as platform_module
from .platform_security import secure_tenant_ids
from .legacy_security import legacy_admin_guard

platform_module.tenant_ids = secure_tenant_ids
app.middleware("http")(legacy_admin_guard)

from .platform import router as platform_router
from .media import router as media_router
from .camera_media import router as camera_media_router
from .admin_ops import router as admin_ops_router
from .p2p_admin import router as p2p_admin_router
from .operations import router as operations_router
from .event_catalog import router as event_catalog_router
from .live import router as live_router
from .admin_crud import router as admin_crud_router

app.include_router(platform_router)
app.include_router(media_router)
app.include_router(camera_media_router)
app.include_router(admin_ops_router)
app.include_router(p2p_admin_router)
app.include_router(operations_router)
app.include_router(event_catalog_router)
app.include_router(live_router)
app.include_router(admin_crud_router)
