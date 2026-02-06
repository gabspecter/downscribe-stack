import os
import tempfile
import threading

import whisper
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

app = FastAPI(title="Downscribe Whisper Service")

DEFAULT_MODEL = os.getenv("WHISPER_DEFAULT_MODEL", "small")

_model_lock = threading.Lock()
_models = {}


def _get_model(name: str):
    name = (name or DEFAULT_MODEL).strip().lower()
    with _model_lock:
        m = _models.get(name)
        if m is None:
            m = whisper.load_model(name)
            _models[name] = m
        return m


@app.get("/health")
def health():
    return {"status": "ok", "default_model": DEFAULT_MODEL, "loaded_models": list(_models.keys())}


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(DEFAULT_MODEL),
    include_segments: bool = Form(False),
):
    try:
        suffix = os.path.splitext(file.filename or "")[1] or ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_path = tmp.name
            content = await file.read()
            tmp.write(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao salvar arquivo temp: {e}") from e

    try:
        whisper_model = _get_model(model)
        result = whisper_model.transcribe(temp_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no Whisper: {e}") from e
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    payload = {
        "text": result.get("text", ""),
        "language": result.get("language"),
        "model": (model or DEFAULT_MODEL),
    }
    if include_segments:
        payload["segments"] = result.get("segments", [])
    return payload
