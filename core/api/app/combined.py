from .main import app
from .platform import router as platform_router
from .media import router as media_router

app.include_router(platform_router)
app.include_router(media_router)
