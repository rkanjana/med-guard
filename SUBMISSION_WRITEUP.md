# 🛡️ MedGuard: Submission Writeup

## 1. Project Overview
MedGuard is a secure medication concierge agent powered by Google's Agent Development Kit (ADK) and FastMCP. The application's goal is to help patients monitor their daily dosage schedules, request refills securely, log food or temporary medicines consumed, and proactively check for drug-drug and food-drug interactions (e.g., grapefruit juice with Simvastatin, potassium-rich foods with Lisinopril, alcohol with Metformin, or NSAIDs with Lisinopril).

## 2. Multi-Agent Workflow & Routing Design
MedGuard utilizes a multi-agent workflow compile structure to maximize reliability and avoid routing errors.

### Node Layout
- **Security Checkpoint**: The primary gateway node. Every input is inspected for security vulnerabilities before any other system component executes.
- **Orchestrator Node**: Uses a router agent powered by `OrchestratorRoute` (a Pydantic schema) to determine the next destination:
  - `check_interaction`: Specialized agent for food/drug reaction analysis.
  - `manage_refill`: Specialized agent for dosing schedules, remaining refills, and confirmation flows.
  - `general`: Directly handles standard conversational inputs like greetings or health queries.
- **Specialized Agents**:
  - `interaction_checker`: Dedicated to executing food/drug checking logic using the FastMCP toolset. Configured with `mode="single_turn"` to adhere to downstream compiler rules.
  - `schedule_refill_manager`: Specialized in checking and requesting refills. Configured with `mode="single_turn"`.
- **Human-in-the-Loop (HITL) Refill Node**: Refill requests require a multi-step execution. When a refill request is detected, the workflow yields a `RequestInput` confirmation event. When the user confirms with `"yes"`, it triggers the mutation tool.

---

## 3. Security Checkpoint & Auditing
Patient medical data is highly sensitive. The `security_checkpoint` node enforces:
- **Prompt Injection Prevention**: Rejects queries containing common injection keywords (`ignore previous instructions`, `override developer mode`, etc.) and immediately routes execution to a `Security Failure` node.
- **PII Scrubbing**: Employs rigorous regex patterns to redact SSNs, credit card numbers, email addresses, and phone numbers to placeholders (e.g., `[SSN_REDACTED]`).
- **Structured Audit Logging**: Logs every event to a structured JSON format with levels `INFO`, `WARNING`, and `CRITICAL`, appending the audit logs to the workflow's state `ctx.state["security_logs"]`.

Example log entry:
```json
{
  "timestamp": "2026-07-01T01:23:45.678900",
  "level": "WARNING",
  "event": "PII_REDACTED",
  "message": "Sensitive PII was detected and redacted from user input."
}
```

---

## 4. FastMCP Toolset Integration
An MCP Server is integrated using Python's `FastMCP` framework, implementing 5 tools:
1. `get_daily_schedule()`: Retrieves scheduled medicines, dosages, and food constraints from a file-based JSON database (`med_db.json`).
2. `log_food_or_medicine(item)`: Logs newly consumed food or transient medications.
3. `check_interaction(item)`: Compares the item against scheduled drugs and returns adverse reaction warnings.
4. `get_refills()`: Queries remaining prescription counts.
5. `request_refill(drug_name)`: Decrements the remaining refill count in the database.

---

## 5. Verification & Test Results
- **Automated Tests**: Integration tests in `tests/integration/test_agent.py` verify that the ADK `Runner` constructs, runs, and logs transitions successfully. End-to-end tests verify that the FastAPI backend registers well-known A2A card endpoints and reasoning engine streams correctly.
- **Local Dev & Workarounds**: Implemented telemetry bypass checks in `fast_api_app.py` and mocked `google.auth.default` during testing. This allows running and testing E2E server routes locally without GCloud auth credentials.
