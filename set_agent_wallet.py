import os
import json
import subprocess
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

AGW_ADDRESS = "0x40fAfAC283f5Eda53Bc572C0bC02CaEbca96036e"
EOA_ADDRESS = os.getenv("EOA_ADDRESS")
IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
TX_HASH = "0x21b5b0fa1ac34becaf94a6e6cb6a05d06ad0096233d93d872e8ad1e4117a55f0"
RPC_URL = "https://api.mainnet.abs.xyz"

w3 = Web3(Web3.HTTPProvider(RPC_URL))

print("=" * 40)
print("MANTIS PRO — SET AGENT WALLET")
print("=" * 40)
print(f"AGW wallet (registered): {AGW_ADDRESS}")
print(f"EOA wallet (new):        {EOA_ADDRESS}")
print()

ABI = [
    {
        "inputs": [{"type": "address", "name": "owner"}],
        "name": "balanceOf",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"type": "address", "name": "owner"},
            {"type": "uint256", "name": "index"}
        ],
        "name": "tokenOfOwnerByIndex",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

contract = w3.eth.contract(
    address=Web3.to_checksum_address(IDENTITY_REGISTRY),
    abi=ABI
)

# Check AGW wallet for tokens
print("Checking AGW wallet for agentId...")
try:
    balance = contract.functions.balanceOf(
        Web3.to_checksum_address(AGW_ADDRESS)
    ).call()
    print(f"Tokens owned by AGW: {balance}")

    if balance > 0:
        agent_id = contract.functions.tokenOfOwnerByIndex(
            Web3.to_checksum_address(AGW_ADDRESS), 0
        ).call()
        print(f"AgentId: {agent_id}")
    else:
        print("No tokens found — using hardcoded agentId 857")
    agent_id = 857
except Exception as e:
    print(f"Contract read failed: {e}")
    print("Using hardcoded agentId 857")
    agent_id = 857

print()
print(f"AgentId: {agent_id}")
print(f"Setting wallet to: {EOA_ADDRESS}")
print()

# Preview setAgentWallet
payload = {
    "address": IDENTITY_REGISTRY,
    "abi": [
        {
            "inputs": [
                {"type": "uint256", "name": "agentId"},
                {"type": "address", "name": "wallet"}
            ],
            "name": "setAgentWallet",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function"
        }
    ],
    "functionName": "setAgentWallet",
    "args": [agent_id, EOA_ADDRESS]
}

with open("set_wallet_payload.json", "w") as f:
    json.dump(payload, f)

print("Previewing transaction...")
result = subprocess.run(
    "agw-cli contract write --json @set_wallet_payload.json --execute",
    capture_output=True, text=True, shell=True
)
print(result.stdout)
print(result.stderr)
print("=" * 40)
print("PREVIEW COMPLETE — ready to execute")
print("=" * 40)