"""Local admin launch entry point."""
from virtualme.adapters.web.app import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9090)
