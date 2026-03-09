import uvicorn
from app.api.main import app
from app.config.settings import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.api.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG
    )
