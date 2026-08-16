from .main import app
from .platform import router as platform_router
from .media import router as media_router
from .camera_media import router as camera_media_router

app.include_router(platform_router)
app.include_router(media_router)
app.include_router(camera_media_router)
