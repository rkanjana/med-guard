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

import os
import re
import sys
import json
import datetime
import dotenv
from pydantic import BaseModel, Field
from typing import Literal, Any, AsyncGenerator

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.workflow import Workflow, Edge, START, node
from google.adk.utils.content_utils import extract_text_from_content
from google.adk.tools import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters
from google.genai import types

# Load environment variables
dotenv.load_dotenv()

# Configure GenAI settings
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "False")
model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

# Define the shared state schema
class MedGuardState(BaseModel):
    scheduled_medications: list[str] = Field(default_factory=list)
    consumed_items: list[str] = Field(default_factory=list)
    refills: dict[str, int] = Field(default_factory=dict)
    security_logs: list[str] = Field(default_factory=list)


# Initialize MCP Connection to our server process
mcp_server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")

mcp_connection = StdioConnectionParams(
    server_params=StdioServerParameters(
        command=sys.executable,
        args=["-u", mcp_server_path]
    )
)
mcp_tools = McpToolset(connection_params=mcp_connection)


# 1. Security Checkpoint Function Node
@node
def security_checkpoint(ctx, node_input: Any) -> Any:
    # Convert node_input to plain string
    text = ""
    if isinstance(node_input, types.Content):
        text = extract_text_from_content(node_input)
    elif isinstance(node_input, str):
        text = node_input
    else:
        text = str(node_input)

    # Detect prompt injection
    injection_keywords = ["ignore previous instructions", "system prompt", "override", "developer mode", "ignore instructions"]
    is_injection = False
    for kw in injection_keywords:
        if kw in text.lower():
            is_injection = True
            break
            
    import logging
    logger = logging.getLogger("med-guard.security")

    if is_injection:
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "level": "CRITICAL",
            "event": "PROMPT_INJECTION_DETECTED",
            "message": "Potential prompt injection detected in user input."
        }
        log_str = json.dumps(log_entry)
        logger.critical(log_str)
        
        logs = ctx.state.get("security_logs", [])
        ctx.state["security_logs"] = logs + [log_str]
        ctx.route = "SECURITY_EVENT"
        return "Security Violation: Potential prompt injection detected. Your request was blocked."

    # Scrub sensitive non-essential PII using regex
    scrubbed_text = text
    pii_found = False
    
    ssn_pattern = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')
    cc_pattern = re.compile(r'\b(?:\d{4}[- ]?){3}\d{4}\b')
    email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
    phone_pattern = re.compile(r'\b(?:\+?\d{1,3}[- ]?)?\(?\d{3}\)?[- ]?\d{3}[- ]?\d{4}\b')
    
    if ssn_pattern.search(scrubbed_text):
        scrubbed_text = ssn_pattern.sub("[SSN_REDACTED]", scrubbed_text)
        pii_found = True
    if cc_pattern.search(scrubbed_text):
        scrubbed_text = cc_pattern.sub("[CARD_REDACTED]", scrubbed_text)
        pii_found = True
    if email_pattern.search(scrubbed_text):
        scrubbed_text = email_pattern.sub("[EMAIL_REDACTED]", scrubbed_text)
        pii_found = True
    if phone_pattern.search(scrubbed_text):
        scrubbed_text = phone_pattern.sub("[PHONE_REDACTED]", scrubbed_text)
        pii_found = True
        
    if pii_found:
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "level": "WARNING",
            "event": "PII_REDACTED",
            "message": "Sensitive PII was detected and redacted from user input."
        }
        log_str = json.dumps(log_entry)
        logger.warning(log_str)
        logs = ctx.state.get("security_logs", [])
        ctx.state["security_logs"] = logs + [log_str]
    else:
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "level": "INFO",
            "event": "INPUT_VERIFIED",
            "message": "User input passed security checkpoint checks."
        }
        log_str = json.dumps(log_entry)
        logger.info(log_str)
        logs = ctx.state.get("security_logs", [])
        ctx.state["security_logs"] = logs + [log_str]
            
    ctx.route = "OK"
    return scrubbed_text


# 2. Security Failure Node
@node
def security_failure_node(ctx, node_input: Any) -> str:
    return "Your request was blocked due to a security violation."


# 3. Routing schema for Orchestrator Agent
class OrchestratorRoute(BaseModel):
    route: Literal["check_interaction", "manage_refill", "general"] = Field(
        description="Select the routing destination. 'check_interaction' for food-drug or drug-drug interactions. 'manage_refill' for daily doses, schedule, and refill inquiries. 'general' for generic questions, greetings, or other inputs."
    )
    explanation: str = Field(description="Explanation of routing choice.")
    response: str = Field(default="", description="General agent response if routing is 'general'.")


# Orchestrator LlmAgent
orchestrator_agent = Agent(
    name="orchestrator_agent",
    model=Gemini(
        model=model_name,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="You are a routing agent. Analyze the user's query and decide the best routing path.",
    output_schema=OrchestratorRoute,
)


# Wrapper function node for Orchestrator routing
@node(rerun_on_resume=True)
async def orchestrator(ctx, node_input: Any) -> Any:
    res = await ctx.run_node(orchestrator_agent, node_input=node_input)
    route_val = res.get("route", "general")
    ctx.route = route_val
    if route_val == "general":
        return res.get("response", "How can I help you today?")
    return node_input


# 4. Specialized Interaction Checker Agent
interaction_checker = Agent(
    name="interaction_checker",
    model=Gemini(
        model=model_name,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    mode="single_turn",
    instruction=(
        "You are a specialized medical concierge focused on checking drug-drug and drug-food interactions.\n"
        "Your task is to identify if any foods or medications react with the user's daily scheduled medications.\n"
        "Use the tools provided to retrieve the user's daily schedule, log newly consumed items, and check for interactions.\n"
        "Always prioritize user safety and warn them about adverse interactions clearly."
    ),
    tools=[mcp_tools],
)


# 5. Specialized Schedule Refill Manager Agent
schedule_refill_manager = Agent(
    name="schedule_refill_manager",
    model=Gemini(
        model=model_name,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    mode="single_turn",
    instruction=(
        "You are a specialized medical concierge focused on tracking daily schedules, dose logging, and refill requests.\n"
        "Use the tools provided to view the medication schedule, retrieve current refills, or request a refill.\n"
        "Ensure you update the user on their remaining refills."
    ),
    tools=[mcp_tools],
)


# Wrapper function node with HITL step for refill confirmation
@node(rerun_on_resume=True)
async def schedule_refill_node(ctx, node_input: Any) -> AsyncGenerator[Any, None]:
    interrupt_id = "refill_confirm"
    confirm_response = ctx.resume_inputs.get(interrupt_id)
    
    text = ""
    if isinstance(node_input, types.Content):
        text = extract_text_from_content(node_input)
    elif isinstance(node_input, str):
        text = node_input
    else:
        text = str(node_input)
        
    drug_match = re.search(r'refill(?:\s+for)?\s+([A-Za-z0-9\-]+)', text, re.IGNORECASE)
    drug_name = drug_match.group(1) if drug_match else ""
    
    if confirm_response is not None:
        text_resp = ""
        if isinstance(confirm_response, types.Content):
            text_resp = extract_text_from_content(confirm_response)
        elif isinstance(confirm_response, str):
            text_resp = confirm_response
        else:
            text_resp = str(confirm_response)
            
        if "yes" in text_resp.lower() or "confirm" in text_resp.lower() or text_resp.lower() == "y":
            prompt = f"The user has confirmed they want to request a refill for the medication: {drug_name or 'their medication'}. Please proceed with ordering the refill now using request_refill tool."
            res = await ctx.run_node(schedule_refill_manager, node_input=prompt)
            yield res
        else:
            yield f"Refill request for {drug_name or 'medication'} cancelled by user."
        return

    is_refill_query = "refill" in text.lower()
    
    if is_refill_query:
        msg = f"You are requesting a refill for {drug_name or 'your medication'}. Please confirm by replying 'yes' or 'confirm'."
        from google.adk.events import RequestInput
        yield RequestInput(
            interrupt_id=interrupt_id,
            message=msg
        )
    else:
        res = await ctx.run_node(schedule_refill_manager, node_input=node_input)
        yield res


# 6. Final Output Node
@node
def final_output(ctx, node_input: Any) -> Any:
    return node_input


# Define the Workflow Graph Nodes & Edges
workflow_nodes = [
    security_checkpoint,
    security_failure_node,
    orchestrator,
    interaction_checker,
    schedule_refill_node,
    final_output
]

workflow_edges = [
    Edge(from_node=START, to_node=security_checkpoint),
    Edge(from_node=security_checkpoint, to_node=orchestrator, route="OK"),
    Edge(from_node=security_checkpoint, to_node=security_failure_node, route="SECURITY_EVENT"),
    Edge(from_node=orchestrator, to_node=interaction_checker, route="check_interaction"),
    Edge(from_node=orchestrator, to_node=schedule_refill_node, route="manage_refill"),
    Edge(from_node=orchestrator, to_node=final_output, route="general"),
    Edge(from_node=interaction_checker, to_node=final_output),
    Edge(from_node=schedule_refill_node, to_node=final_output)
]

med_guard_workflow = Workflow(
    name="med_guard_workflow",
    edges=workflow_edges,
    state_schema=MedGuardState
)

# Root agent referenced by runner and tests
root_agent = med_guard_workflow

# Instantiate the App wrapping the workflow
app = App(
    name="app",
    root_agent=med_guard_workflow
)
