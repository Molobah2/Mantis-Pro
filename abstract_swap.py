"""
abstract_swap.py
────────────────
Let your Python AI agent swap tokens on Abstract chain (Chain ID: 2741)
using the official AGW CLI — no private key ever touches your agent.

ONE-TIME SETUP:
    npm install -g @abstract-foundation/agw-cli
    agw-cli auth init --json '{\"chainId\":2741}' --execute
    agw-cli session doctor --json '{}'

PYTHON REQUIREMENTS:
    pip install web3

USAGE:
    from abstract_swap import AbstractSwap

    swap = AbstractSwap()
    swap.swap_eth_for_token(AbstractSwap.PENGU, dry_run=True)   # preview
    swap.swap_eth_for_token(AbstractSwap.PENGU, dry_run=False)  # execute
"""

import json
import platform
import subprocess
import time
from web3 import Web3

# ─────────────────────────────────────────────────────
# WINDOWS FIX
# ─────────────────────────────────────────────────────
AGW = "agw-cli.cmd" if platform.system() == "Windows" else "agw-cli"

# ─────────────────────────────────────────────────────
# ABSTRACT CHAIN CONFIG
# ─────────────────────────────────────────────────────
ABSTRACT_RPC      = "https://api.mainnet.abs.xyz"
ABSTRACT_CHAIN_ID = 2741

# ─────────────────────────────────────────────────────
# TOKEN ADDRESSES  (Abstract Mainnet)
# Source: docs.abs.xyz/tooling/deployed-contracts
# ─────────────────────────────────────────────────────
WETH_ADDRESS  = Web3.to_checksum_address("0x3439153EB7AF838Ad19d56E1571FBD09333C2809")
USDC_ADDRESS  = Web3.to_checksum_address("0x84A71ccD554Cc1b02749b35d22F684CC8ec987e1")
PENGU_ADDRESS = Web3.to_checksum_address("0x9eBE3A824ca958e4b3Da772D2065518F009CBa62")

# ─────────────────────────────────────────────────────
# DEX ROUTER  — Uniswap V2 on Abstract Mainnet
# Source: docs.abs.xyz/tooling/deployed-contracts
# ─────────────────────────────────────────────────────
ROUTER_ADDRESS = Web3.to_checksum_address("0xad1eCa41E6F772bE3cb5A48A6141f9bcc1AF9F7c")

# ─────────────────────────────────────────────────────
# HOW MUCH OF YOUR ETH TO USE PER SWAP
# ─────────────────────────────────────────────────────
SWAP_ETH_PERCENT = 0.20   # 20%

# ─────────────────────────────────────────────────────
# ABIs
# ─────────────────────────────────────────────────────
ROUTER_ABI = [
    {
        "name": "swapExactETHForTokens",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path",         "type": "address[]"},
            {"name": "to",           "type": "address"},
            {"name": "deadline",     "type": "uint256"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    },
    {
        "name": "swapExactTokensForTokens",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "amountIn",     "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path",         "type": "address[]"},
            {"name": "to",           "type": "address"},
            {"name": "deadline",     "type": "uint256"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    },
]

ERC20_ABI = [
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount",  "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
]


def _agw(args: list, dry_run: bool = None) -> dict:
    """Run an agw-cli command. Returns parsed JSON response."""
    cmd = [AGW] + args
    if dry_run is True:
        cmd.append("--dry-run")
    elif dry_run is False:
        cmd.append("--execute")

    print(f"▶ {' '.join(cmd)}\n")

    result = subprocess.run(cmd, capture_output=True, text=True)
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if result.returncode != 0:
        raise RuntimeError(f"agw-cli error:\n{stderr}")

    if not stdout:
        print("(no output returned)\n")
        return {}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"raw": stdout}


def _extract_address(data: dict) -> str:
    """Extract wallet address from AGW CLI response, trying all known keys."""
    for key in ("address", "accountAddress", "walletAddress", "account", "wallet"):
        val = data.get(key, "")
        if val and val.startswith("0x"):
            return Web3.to_checksum_address(val)
    for val in data.values():
        if isinstance(val, str) and val.startswith("0x") and len(val) == 42:
            return Web3.to_checksum_address(val)
    raise RuntimeError(f"Could not find wallet address in AGW CLI response: {json.dumps(data)}")


def _encode(contract, fn_name: str, args: list) -> str:
    """Encode ABI calldata — compatible with web3.py v5, v6, and v7."""
    try:
        return contract.encode_abi(fn_name, args=args)
    except TypeError:
        try:
            return contract.encode_abi(fn_name=fn_name, args=args)
        except AttributeError:
            return contract.encodeABI(fn_name=fn_name, args=args)


class AbstractSwap:
    """
    Swap tokens on Abstract chain from your Python AI agent.
    Every ETH swap automatically uses 20% of your current ETH balance.
    """

    PENGU = PENGU_ADDRESS
    USDC  = USDC_ADDRESS
    WETH  = WETH_ADDRESS

    def __init__(self, deadline_seconds: int = 300):
        self.w3               = Web3(Web3.HTTPProvider(ABSTRACT_RPC))
        self.deadline_seconds = deadline_seconds
        self.router           = self.w3.eth.contract(
            address=ROUTER_ADDRESS,
            abi=ROUTER_ABI,
        )

    def get_wallet_address(self) -> str:
        """Get your AGW wallet address."""
        data = _agw(["wallet", "address", "--json", "{}"])
        addr = _extract_address(data)
        print(f"Wallet: {addr}\n")
        return addr

    def get_balances(self) -> dict:
        """Read native ETH + token balances."""
        print("📊 Fetching balances...\n")
        data = _agw(["wallet", "balances", "--json", "{}"])
        print(json.dumps(data, indent=2))
        return data

    def get_eth_balance(self) -> float:
        """Returns current ETH balance as a float."""
        data = _agw(["wallet", "balances", "--json", "{}"])
        try:
            return float(data["nativeBalance"]["amount"]["formatted"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("Could not read ETH balance. Raw: " + json.dumps(data))

    def swap_eth_for_token(
        self,
        token_out: str,
        amount_out_min: int = 0,
        dry_run: bool = True,
    ) -> dict:
        """
        Swap 20% of your current ETH balance for any token.

        Args:
            token_out:      Token address (e.g. AbstractSwap.PENGU)
            amount_out_min: Min tokens to receive (0 = accept anything)
            dry_run:        True = preview only. False = real transaction.
        """
        wallet      = self.get_wallet_address()
        eth_balance = self.get_eth_balance()
        amount_eth  = round(eth_balance * SWAP_ETH_PERCENT, 18)

        print(f"💰 ETH balance:  {eth_balance:.18f} ETH")
        print(f"📤 Spending 20%: {amount_eth:.18f} ETH\n")

        if amount_eth <= 0:
            raise ValueError("ETH balance is too low to swap 20%.")

        wei_in   = self.w3.to_wei(amount_eth, "ether")
        deadline = int(time.time()) + self.deadline_seconds
        path     = [WETH_ADDRESS, Web3.to_checksum_address(token_out)]

        calldata = _encode(self.router, "swapExactETHForTokens",
                           [amount_out_min, path, wallet, deadline])

        label = "DRY RUN" if dry_run else "LIVE"
        print(f"\n{'─'*52}")
        print(f"🔄 [{label}] Swap {amount_eth:.8f} ETH → {token_out[:10]}...")
        print(f"   Router: {ROUTER_ADDRESS}")
        print(f"   Wallet: {wallet}")
        print(f"{'─'*52}\n")

        payload = json.dumps({
            "to":    ROUTER_ADDRESS,
            "data":  calldata,
            "value": str(wei_in),
        })

        result = _agw(["tx", "send", "--json", payload], dry_run=dry_run)
        print(json.dumps(result, indent=2))
        return result

    def swap_usdc_for_token(
        self,
        token_out: str,
        amount_usdc: float,
        amount_out_min: int = 0,
        dry_run: bool = True,
    ) -> dict:
        """
        Swap a fixed USDC amount for any token. USDC = 6 decimals.

        Args:
            token_out:      Token address to receive
            amount_usdc:    USDC to spend (e.g. 5.0 = $5)
            amount_out_min: Min tokens to receive
            dry_run:        True = preview only. False = real transaction.
        """
        wallet    = self.get_wallet_address()
        amount_in = int(amount_usdc * 1_000_000)
        deadline  = int(time.time()) + self.deadline_seconds
        path      = [USDC_ADDRESS, Web3.to_checksum_address(token_out)]

        label = "DRY RUN" if dry_run else "LIVE"

        usdc         = self.w3.eth.contract(address=USDC_ADDRESS, abi=ERC20_ABI)
        approve_data = _encode(usdc, "approve", [ROUTER_ADDRESS, amount_in])

        print(f"\n{'─'*52}")
        print(f"✅ [{label}] Step 1: Approve {amount_usdc} USDC")
        print(f"{'─'*52}\n")

        _agw(["tx", "send", "--json", json.dumps({
            "to": USDC_ADDRESS, "data": approve_data, "value": "0"
        })], dry_run=dry_run)

        calldata = _encode(self.router, "swapExactTokensForTokens",
                           [amount_in, amount_out_min, path, wallet, deadline])

        print(f"\n{'─'*52}")
        print(f"🔄 [{label}] Step 2: Swap {amount_usdc} USDC → {token_out[:10]}...")
        print(f"{'─'*52}\n")

        result = _agw(["tx", "send", "--json", json.dumps({
            "to": ROUTER_ADDRESS, "data": calldata, "value": "0"
        })], dry_run=dry_run)
        print(json.dumps(result, indent=2))
        return result


# ──────────────────────────────────────────────────
# EXECUTE — swaps 20% of ETH balance for PENGU
# ──────────────────────────────────────────────────
if __name__ == "__main__":
    swap = AbstractSwap()

    print("\n=== Wallet ===")
    swap.get_wallet_address()

    print("\n=== Balances ===")
    swap.get_balances()

    print("\n=== LIVE Swap: 20% ETH → PENGU ===")
    swap.swap_eth_for_token(
        token_out=AbstractSwap.PENGU,
        dry_run=False,  # ← LIVE — real transaction
    )
