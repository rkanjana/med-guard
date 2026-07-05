import os
import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("MedGuard MCP Server")

DB_FILE = os.path.join(os.path.dirname(__file__), "med_db.json")

def init_db():
    if not os.path.exists(DB_FILE):
        default_data = {
            "scheduled_medications": [
                {
                    "drug_name": "Metformin",
                    "dosage": "500mg",
                    "schedule": "Daily with breakfast",
                    "food_constraints": "Avoid high-sugar meals"
                },
                {
                    "drug_name": "Lisinopril",
                    "dosage": "10mg",
                    "schedule": "Daily in the morning",
                    "food_constraints": "Avoid high-potassium foods (like large amounts of bananas/spinach)"
                },
                {
                    "drug_name": "Simvastatin",
                    "dosage": "20mg",
                    "schedule": "Daily at bedtime",
                    "food_constraints": "Do NOT consume with grapefruit or grapefruit juice"
                }
            ],
            "consumed_items": [],
            "refills": {
                "Metformin": 2,
                "Lisinopril": 1,
                "Simvastatin": 0
            }
        }
        with open(DB_FILE, "w") as f:
            json.dump(default_data, f, indent=4)

def read_db():
    init_db()
    with open(DB_FILE, "r") as f:
        return json.load(f)

def write_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)


@mcp.tool()
def get_daily_schedule() -> str:
    """Retrieve the list of daily scheduled medications and their details (dosage, schedule, food constraints)."""
    db = read_db()
    return json.dumps(db["scheduled_medications"], indent=2)


@mcp.tool()
def log_food_or_medicine(item: str) -> str:
    """Log a newly consumed food item or sudden medicine (e.g. headache, cold, nausea medicines, or general foods/drinks)."""
    db = read_db()
    db["consumed_items"].append(item)
    write_db(db)
    return f"Successfully logged consumption of: {item}"


@mcp.tool()
def check_interaction(item: str) -> str:
    """Check if a specific food item or sudden medicine interacts with any of the daily scheduled medications."""
    db = read_db()
    scheduled = [m["drug_name"].lower() for m in db["scheduled_medications"]]
    
    interactions = []
    item_lower = item.lower()
    
    if "grapefruit" in item_lower and "simvastatin" in scheduled:
        interactions.append(
            "CRITICAL WARNING: Grapefruit or grapefruit juice inhibits the enzyme needed to break down Simvastatin. "
            "This can lead to dangerously high levels of Simvastatin in your blood, increasing the risk of muscle damage (rhabdomyolysis) and liver injury. "
            "Do NOT consume grapefruit while taking Simvastatin."
        )
    if any(k in item_lower for k in ["banana", "spinach", "potassium"]) and "lisinopril" in scheduled:
        interactions.append(
            "WARNING: Lisinopril can increase potassium levels in your body. Consuming high-potassium items like bananas, spinach, or potassium supplements in large amounts can cause hyperkalemia (high blood potassium), which can affect heart rhythm."
        )
    if "alcohol" in item_lower and "metformin" in scheduled:
        interactions.append(
            "WARNING: Alcohol combined with Metformin increases the risk of lactic acidosis, a rare but serious and potentially life-threatening side effect. Limit or avoid alcohol."
        )
    if any(k in item_lower for k in ["aspirin", "ibuprofen", "advil", "motrin", "aleve", "naproxen"]) and "lisinopril" in scheduled:
        interactions.append(
            "WARNING: Nonsteroidal anti-inflammatory drugs (NSAIDs) like Ibuprofen or Aspirin can reduce the blood pressure-lowering effect of Lisinopril and increase the risk of kidney damage when paired."
        )
        
    if not interactions:
        return f"No direct adverse interactions detected for '{item}' with your daily scheduled medications. However, always consult a healthcare professional if you feel unwell."
        
    return "\n\n".join(interactions)


@mcp.tool()
def get_refills() -> str:
    """Retrieve the current refill details for all scheduled medications."""
    db = read_db()
    return json.dumps(db["refills"], indent=2)


@mcp.tool()
def request_refill(drug_name: str) -> str:
    """Request a refill for a specific medication. Decrements the remaining refill count if available."""
    db = read_db()
    refills = db["refills"]
    
    # Try case-insensitive lookup
    target_drug = None
    for d in refills:
        if d.lower() == drug_name.lower():
            target_drug = d
            break
            
    if not target_drug:
        return f"Medication '{drug_name}' not found in your refill records."
        
    count = refills[target_drug]
    if count <= 0:
        return f"Cannot refill '{target_drug}': No refills remaining. Please contact your doctor for a new prescription."
        
    refills[target_drug] -= 1
    write_db(db)
    return f"Refill requested successfully for '{target_drug}'. Remaining refills: {refills[target_drug]}."


if __name__ == "__main__":
    mcp.run()
