import json
import subprocess
from dotenv import load_dotenv

load_dotenv()

IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
AGENT_URI = "https://gist.githubusercontent.com/Molobah2/7ca6f6f5d60a548ba0e2602e19d846c8/raw/mantis-pro.json"

print("=" * 40)
print("REGISTERING MANTIS PRO ON ABSTRACT")
print("=" * 40)
print(f"Registry: {IDENTITY_REGISTRY}")
print(f"Agent URI: {AGENT_URI}")
print()

# Step 1 - Preview first
print("Step 1 — Previewing registration...")
payload = {
    "address": IDENTITY_REGISTRY,
    "abi": [
        {
            "inputs": [{"type": "string", "name": "agentURI"}],
            "name": "register",
            "outputs": [{"type": "uint256"}],
            "stateMutability": "nonpayable",
            "type": "function"
        }
    ],
    "functionName": "register",
    "args": [AGENT_URI]
}

with open("register_payload.json", "w") as f:
    json.dump(payload, f)

result = subprocess.run(
    "agw-cli contract write --json @register_payload.json --execute",
    capture_output=True, text=True, shell=True
)
print(result.stdout)
print(result.stderr)
print("=" * 40)
print("MANTIS PRO REGISTERED ON ABSTRACT!")
print("=" * 40)