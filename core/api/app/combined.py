from .main import app
from . import platform as platform_module
from .platform_security import secure_tenant_ids

platform_module.tenant_ids = secure_tenant_ids

from .platform import router as platform_router
from .media import router as media_router
from .camera_media import router as camera_media_router
from .admin_ops import router as admin_ops_router
from .p2p_admin import router as p2p_admin_router

app.include_router(platform_router)
app.include_router(media_router)
app.include_router(camera_media_router)
app.include_router(admin_ops_router)
app.include_router(p2p_admin_router)
