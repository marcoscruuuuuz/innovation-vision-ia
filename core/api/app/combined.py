from .main import app
from .platform import router

app.include_router(router)
