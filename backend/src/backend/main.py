from fastapi import FastAPI

app = FastAPI(title="AI News Agent")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Report process health without requiring external services."""
    return {"status": "ok"}
