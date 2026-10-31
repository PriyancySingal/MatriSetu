import os
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Twilio credentials — set these in Railway environment variables
TWILIO_ACCOUNT_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN      = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_NUMBER = "whatsapp:+14155238886"
PORT                   = int(os.environ.get("PORT", 5000))
DEBUG                  = os.environ.get("DEBUG", "True") == "True"
ASHA_WORKER_NUMBER     = os.environ.get("ASHA_NUMBER", "")

# Meko (shared memory layer)
MEKO_ENABLED           = os.environ.get("MEKO_ENABLED", "false").lower() == "true"
MEKO_API_KEY           = os.environ.get("MEKO_API_KEY", "")
MEKO_MCP_URL           = os.environ.get(
    "MEKO_MCP_URL",
    "https://mcp.mekodata.ai/mcp"
)
MEKO_PATIENTS_DATAPACK = os.environ.get("MEKO_PATIENTS_DATAPACK", "")
MEKO_DISTRICT_DATAPACK = os.environ.get("MEKO_DISTRICT_DATAPACK", "")
MEKO_PATIENT_SALT      = os.environ.get("MEKO_PATIENT_SALT", "")
MEKO_TIMEOUT_SECONDS   = float(os.environ.get("MEKO_TIMEOUT_SECONDS", "20"))
