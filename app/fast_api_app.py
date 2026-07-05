# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import contextlib
import os
from collections.abc import AsyncIterator

import google.auth
from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from google.cloud import logging as google_cloud_logging

from app.app_utils import services
from app.app_utils.a2a import attach_a2a_routes
from app.app_utils.reasoning_engine_adapter import (
    attach_reasoning_engine_routes,
)
from app.app_utils.telemetry import (
    setup_agent_engine_telemetry,
    setup_telemetry,
)
from app.app_utils.typing import Feedback

load_dotenv()
setup_telemetry()
import logging as std_logging

# Must run before get_fast_api_app to set the tracer provider resource.
use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "True").lower() != "false"
logger = None

if use_vertex:
    try:
        setup_agent_engine_telemetry()
        _, project_id = google.auth.default()
        logging_client = google_cloud_logging.Client()
        logger = logging_client.logger(__name__)
    except Exception as e:
        std_logging.warning(f"Could not initialize Google Cloud logging/telemetry: {e}")

if logger is None:
    class LocalLogger:
        def log_struct(self, data, severity="INFO"):
            std_logging.info(f"[{severity}] {data}")
    logger = LocalLogger()
allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Runner for the A2A path, sharing the same session/artifact services as the
    # adk_api and reasoning_engine paths (see services.py). Imported here so the
    # agent is built after env/telemetry setup.
    from app.agent import app as adk_app
    from app.agent import root_agent

    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    # Shared by the A2A path and the reasoning_engine adapter routes.
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name
    await attach_a2a_routes(
        app,
        agent=root_agent,
        runner=runner,
        task_store=InMemoryTaskStore(),
        rpc_path=f"/a2a/{adk_app.name}",
    )
    yield


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=False,
    lifespan=lifespan,
)
app.title = "med-guard"
app.description = "API for interacting with the Agent med-guard"


# Proxy routes so the Vertex AI Console Playground (reasoning_engine SDK) can
# talk to this agent alongside the native adk_api routes.
attach_reasoning_engine_routes(app)


from fastapi.responses import HTMLResponse, StreamingResponse
import json

@app.get("/playground", response_class=HTMLResponse)
def get_custom_ui():
    """Serve the MedGuard Concierge frontend dashboard."""
    html_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/", response_class=HTMLResponse)
def get_root_ui():
    """Serve the MedGuard Concierge frontend dashboard at root path."""
    return get_custom_ui()


@app.get("/api/state")
async def get_state_endpoint(user_id: str, session_id: str):
    """Retrieve the current state dictionary of the workflow session."""
    session_service = services.get_session_service()
    session = await session_service.get_session(
        app_name=app.state.agent_app_name,
        user_id=user_id,
        session_id=session_id
    )
    if not session:
        return {"state": {}}
    return {"state": session.state}


@app.post("/api/chat")
async def chat_endpoint(payload: dict):
    """Run/resume agent workflow query and stream back SSE chunks."""
    user_id = payload.get("user_id")
    session_id = payload.get("session_id")
    message = payload.get("message")
    interrupt_id = payload.get("interrupt_id")
    
    from google.genai import types
    if interrupt_id:
        content = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name=interrupt_id,
                        id=interrupt_id,
                        response={"response": message}
                    )
                )
            ]
        )
    else:
        content = types.Content(
            role="user",
            parts=[types.Part(text=message)]
        )
        
    runner = app.state.runner
    
    async def event_generator():
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=content
        ):
            from vertexai.agent_engines import _utils
            dumped = _utils.dump_event_for_json(event)
            yield f"data: {json.dumps(dumped)}\n\n"
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log feedback.

    Args:
        feedback: The feedback data to log

    Returns:
        Success message
    """
    logger.log_struct(feedback.model_dump(), severity="INFO")
    return {"status": "success"}


# Main execution
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
