"""FastAPI 진입점.

실행: uvicorn api.main:app --reload --port 8000  (프로젝트 루트에서)
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api import service


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = service.load_store()
    yield


app = FastAPI(title="전기차 신호등 API", lifespan=lifespan)


def store(request: Request) -> service.Store:
    return request.app.state.store


@app.exception_handler(KeyError)
async def not_found(request: Request, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": str(exc.args[0]) if exc.args else "없음"})


@app.exception_handler(ValueError)
async def cannot_calculate(request: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": f"계산할 수 없습니다: {exc}"})


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/meta")
def meta(request: Request):
    return {"base_date": store(request).base_date}


@app.get("/regions")
def regions(request: Request):
    return service.regions(store(request))


@app.get("/models")
def models(request: Request, region: str):
    s = store(request)
    if region not in service.regions(s):
        raise HTTPException(404, f"'{region}' 지역이 없습니다.")
    return service.complete_models(s, region)


@app.post("/evaluate")
def evaluate(request: Request, inp: service.EvaluateInput):
    return service.evaluate(store(request), inp)


@app.post("/explain")
def explain(request: Request, inp: service.EvaluateInput):
    return service.explain(store(request), inp)


class NoticeInput(BaseModel):
    region: str
    model: str
    has_scrap: bool


@app.post("/notice-summary")
def notice_summary(request: Request, inp: NoticeInput):
    return {"items": service.notice_summary(store(request), inp.region, inp.has_scrap, inp.model)}


@app.get("/contact")
def contact(request: Request, region: str):
    return {"contact": service.region_contact(store(request), region)}


@app.post("/chat")
def chat(request: Request, inp: service.ChatInput):
    return service.chat(store(request), inp)
