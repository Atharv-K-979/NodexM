"""Entry point: `uvicorn main:app --reload` (or `python main.py`)."""
from nodexm.app import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000)
