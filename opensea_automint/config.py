import os

# DATA_DIR: mount a Railway Volume here (e.g. /data) so DB and session survive redeploys
_DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.dirname(__file__)))

RPC_ETHEREUM_MAINNET = os.getenv("RPC_ETHEREUM_MAINNET", "https://eth.llamarpc.com")
NODE_HELPER_URL       = "http://127.0.0.1:3456"

DATA_DIR = _DATA_DIR


def get_rpc() -> str:
    return RPC_ETHEREUM_MAINNET
