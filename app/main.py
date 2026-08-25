import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.api import root, index, documents, jobs, queries, namespaces, projects, public, tree, auth, health
from app.core.upload_limit import UploadSizeLimitMiddleware
from app.core import llm
from app.services import job_daemon
from app.services.workers import init_database
from talkingdb.logger.console import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    job_daemon.start()

    provider = os.getenv("LLM_PROVIDER")
    if llm.is_configured():
        logger.info("LLM summarization ready (provider=%s)", provider)
    else:
        logger.warning(
            "LLM summarization not configured (LLM_PROVIDER=%s) - "
            "/v1/queries will return summary=null regardless of the "
            "summarize flag until it's set up",
            provider,
        )

    yield
    job_daemon.stop()


app = FastAPI(lifespan=lifespan, title="Module TalkingDB")

app.add_middleware(UploadSizeLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(root.router)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(namespaces.router)
app.include_router(projects.router)
app.include_router(jobs.router)
app.include_router(queries.router)
app.include_router(tree.router)
app.include_router(index.router)
app.include_router(public.router)
