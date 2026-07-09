import os

# DATA_DIR: mount a Railway Volume here (e.g. /data) so DB and session survive redeploys
_DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.dirname(__file__)))

UPVOTE_CONTRACT  = "0x3b50de27506f0a8c1f4122a1e6f470009a76ce2a"
UPVOTE_SELECTOR  = "0x7060a227"   # toFunctionSelector("upvote(uint256)")
AGW_ADDRESS      = os.getenv("AGW_ADDRESS", "0x9d60f5906d43aa12b0496765ec202bf498e9cd1f")
NETWORK          = os.getenv("PORTAL_UPVOTE_NETWORK", "mainnet")   # testnet | mainnet
NODE_HELPER_URL  = "http://127.0.0.1:3456"
SESSION_FILE     = os.path.join(_DATA_DIR, ".upvote_session")

RPC_MAINNET = "https://api.mainnet.abs.xyz"
RPC_TESTNET = "https://api.testnet.abs.xyz"

def get_rpc():
    return RPC_MAINNET if NETWORK == "mainnet" else RPC_TESTNET
