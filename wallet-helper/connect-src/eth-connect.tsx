import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  WagmiProvider,
  createConfig,
  http,
  useAccount,
  useConnect,
  useDisconnect,
  usePublicClient,
  useWalletClient,
} from "wagmi";
import { injected } from "wagmi/connectors";
import { mainnet } from "viem/chains";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import type { Address } from "viem";

// SeaDrop v1.0 — same canonical address across every EVM chain OpenSea
// supports. Verified against a REAL deployed drop before writing this: a
// simulated mintPublic() call against this exact address, targeting a real
// live collection, reverted with SeaDrop's own IncorrectPayment(uint256,uint256)
// error (0x0d35e921) rather than a generic/unrelated revert — proof this is
// the contract real collections actually route mints through, not a guess
// from documentation alone.
const SEADROP_ADDRESS: Address = "0x00005EA00Ac477B1030CE78506496e8C2dE24bf5";

// getPublicDrop(nftContract) is a public, no-wallet-required view function
// on SeaDrop itself — verified against a real live collection (returned a
// real, sensible endTime ~18 days out, not zero/garbage) before relying on
// it here. Struct layout confirmed against SeaDrop's own SeaDropStructs.sol.
const GET_PUBLIC_DROP_ABI = [
  {
    name: "getPublicDrop",
    type: "function",
    stateMutability: "view",
    inputs: [{ name: "nftContract", type: "address" }],
    outputs: [
      {
        name: "",
        type: "tuple",
        components: [
          { name: "mintPrice", type: "uint80" },
          { name: "startTime", type: "uint48" },
          { name: "endTime", type: "uint48" },
          { name: "maxTotalMintableByWallet", type: "uint16" },
          { name: "feeBps", type: "uint16" },
          { name: "restrictFeeRecipients", type: "bool" },
        ],
      },
    ],
  },
] as const;

// Floor: below this we treat the drop as effectively already over rather
// than offering a near-zero-length permission. Ceiling: kept under the
// backend's exact 30-day cap (opensea_automint/security.py's
// _MAX_EXPIRES_AT_SECONDS_FROM_NOW = 30*86400) with a 1-day safety margin,
// so request latency or client clock skew can never push a real submission
// past the server's own limit.
const MIN_SUGGESTED_EXPIRY_SECONDS = 5 * 60;
const MAX_SUGGESTED_EXPIRY_SECONDS = 29 * 86400;

// Deliberately separate from connect.tsx (Abstract/AGW, session-key
// automation) — this is Ethereum mainnet, a plain EOA wallet connection.
// Keeping the two bundles and their window callback namespaces disjoint so
// the two chains/account models can never cross-wire.
const wagmiConfig = createConfig({
  chains: [mainnet],
  connectors: [injected()],
  transports: { [mainnet.id]: http() },
});

const queryClient = new QueryClient();

interface GrantMintPermissionParams {
  nftContractAddress: string;
  maxQuantity: number;
  valueCapWei: string; // decimal string — passed as a string to avoid float/bigint JSON issues
  expiresInSeconds: number;
}

declare global {
  interface Window {
    // Optional: every call site uses `?.` deliberately, so a host page that
    // hasn't wired one of these up degrades to a silent no-op rather than a
    // runtime error — matches the type to how it's actually used below.
    _onEthConnected?: (ownerAddress: string) => void;
    _onEthConnectProgress?: (text: string) => void;
    _onEthConnectError?: (msg: string) => void;
    // Imperative bridge for the (vanilla-JS) dashboard modal to trigger a
    // permission grant — the modal isn't part of this React tree, so this is
    // exposed on window rather than as a prop/callback. Returns the fresh
    // session key's own address — the dashboard must show this so the user
    // knows where to send funds (this project fires direct signed
    // transactions from that key, not through a sponsored smart account —
    // it needs real ETH of its own to cover mint price + gas).
    grantMintPermission?: (
      params: GrantMintPermissionParams
    ) => Promise<{ grantId: number; sessionAddress: string }>;
    // Imperative bridge: looks up the real on-chain mint end time for a
    // collection so the dashboard can suggest "expire when the mint ends"
    // instead of an arbitrary fixed duration. Returns null when unavailable
    // (no public drop stage, RPC error, or the drop has no on-chain end
    // time) — callers fall back to their own manual duration options.
    getSuggestedExpirySeconds?: (nftContractAddress: string) => Promise<number | null>;
    // Imperative bridge: signs an arbitrary message with the connected
    // owner wallet (plain personal_sign). Used to authorize the arm/cancel
    // API calls — see opensea_automint/messages.py's build_arm_message/
    // build_cancel_message, which the dashboard's JS must construct
    // identically for the backend's signature verification to pass.
    signOwnerMessage?: (message: string) => Promise<string>;
  }
}

// Holds the latest wagmi hook values so the window bridges (called from
// outside React, e.g. the dashboard's vanilla-JS modal) can read current
// wallet state without themselves being hooks. Updated by EthConnectButton
// on every relevant render via useEffect — see below.
const walletStateRef: {
  current: {
    ownerAddress: Address | undefined;
    walletClient: ReturnType<typeof useWalletClient>["data"];
    publicClient: ReturnType<typeof usePublicClient>;
  };
} = {
  current: {
    ownerAddress: undefined,
    walletClient: undefined,
    publicClient: undefined,
  },
};

// Must build byte-for-byte the same message text as opensea_automint/
// messages.py's build_grant_message — the backend verifies the signature
// against a message it reconstructs independently from the request's own
// fields, so any mismatch makes a legitimate signature fail verification.
// expiresAt/timestamp are always plain integers (never floats), so their
// text representation can never differ between JS and Python.
function buildGrantMessage(
  sessionAddress: string,
  nftContract: string,
  maxQuantity: number,
  valueCapWei: string,
  expiresAt: number,
  timestamp: number
): string {
  return (
    "Mantis Pro OpenSea Auto-Mint\n" +
    "action: grant\n" +
    "sessionAddress: " + sessionAddress + "\n" +
    "nftContract: " + nftContract + "\n" +
    "maxQuantity: " + maxQuantity + "\n" +
    "valueCapWei: " + valueCapWei + "\n" +
    "expiresAt: " + expiresAt + "\n" +
    "timestamp: " + timestamp
  );
}

async function grantMintPermission(
  params: GrantMintPermissionParams
): Promise<{ grantId: number; sessionAddress: string }> {
  const { ownerAddress, walletClient } = walletStateRef.current;

  if (!ownerAddress || !walletClient) {
    throw new Error("Connect your wallet first");
  }

  window._onEthConnectProgress?.("Generating a fresh session key…");

  // A throwaway keypair generated fresh for this grant. Its private key
  // never leaves this browser except encrypted, server-side, after this
  // point. Unlike the smart-account design this project started with,
  // THIS key directly signs and pays for the mint transaction itself — it
  // needs to hold real ETH (mint price + gas) of its own. A leaked key can
  // spend whatever it holds, not just trigger a mint — fund it accordingly.
  const sessionPrivateKey = generatePrivateKey();
  const sessionAccount = privateKeyToAccount(sessionPrivateKey);

  const nftContract = params.nftContractAddress as Address;
  const expiresAt = Math.floor(Date.now() / 1000) + params.expiresInSeconds;
  const timestamp = Math.floor(Date.now() / 1000);

  const message = buildGrantMessage(
    sessionAccount.address,
    nftContract,
    params.maxQuantity,
    params.valueCapWei,
    expiresAt,
    timestamp
  );

  window._onEthConnectProgress?.("Approve the authorization in your wallet…");
  const signature = await walletClient.signMessage({ account: ownerAddress, message });

  window._onEthConnectProgress?.("Saving permission…");

  const res = await fetch("/api/opensea/session-grant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ownerAddress,
      sessionAddress: sessionAccount.address,
      sessionPrivateKey,
      nftContract,
      maxQuantity: params.maxQuantity,
      valueCapWei: params.valueCapWei,
      expiresAt,
      timestamp,
      signature,
    }),
  });

  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(body.error || `API ${res.status}`);
  }

  const result = (await res.json()) as { grantId: number };
  return { grantId: result.grantId, sessionAddress: sessionAccount.address };
}

window.grantMintPermission = grantMintPermission;

// Reads SeaDrop's real, on-chain public-stage end time for a collection and
// returns how many seconds from now that is — clamped to a sane floor/ceiling
// — so the dashboard can offer "expire when the mint ends" as a suggested
// default instead of forcing every user through a fixed, arbitrary duration.
// Read-only: no signing, no wallet prompt, works the moment publicClient is
// available (doesn't require a full grant flow to have started).
async function getSuggestedExpirySeconds(
  nftContractAddress: string
): Promise<number | null> {
  const { publicClient } = walletStateRef.current;
  if (!publicClient) return null;

  try {
    const drop = await publicClient.readContract({
      address: SEADROP_ADDRESS,
      abi: GET_PUBLIC_DROP_ABI,
      functionName: "getPublicDrop",
      args: [nftContractAddress as Address],
    });

    const endTime = Number(drop.endTime);
    if (!endTime) return null; // no public drop stage configured for this contract

    const secondsRemaining = endTime - Math.floor(Date.now() / 1000);
    if (secondsRemaining <= MIN_SUGGESTED_EXPIRY_SECONDS) return null; // already ended, or ending imminently

    return Math.min(secondsRemaining, MAX_SUGGESTED_EXPIRY_SECONDS);
  } catch {
    // Token-gated / allowlist-only stages, or any contract that doesn't
    // implement SeaDrop's public-stage interface, reverts here — that's an
    // expected "not available" outcome, not a bug to surface to the user.
    return null;
  }
}

window.getSuggestedExpirySeconds = getSuggestedExpirySeconds;

// Plain personal_sign over an arbitrary message — used to prove the
// connected wallet controls ownerAddress before the backend acts on a
// grant/arm/cancel request.
async function signOwnerMessage(message: string): Promise<string> {
  const { ownerAddress, walletClient } = walletStateRef.current;
  if (!ownerAddress || !walletClient) {
    throw new Error("Connect your wallet first");
  }
  return await walletClient.signMessage({ account: ownerAddress, message });
}

window.signOwnerMessage = signOwnerMessage;

function EthConnectButton() {
  const { address, isConnected } = useAccount();
  const { connect, connectors, isPending: isConnecting, error: connectError } =
    useConnect();
  const { disconnect } = useDisconnect();
  const { data: walletClient } = useWalletClient();
  const publicClient = usePublicClient();

  // Keep the module-level bridge in sync so the window.* bridges (invoked
  // from outside this React tree) always read current state.
  useEffect(() => {
    walletStateRef.current = {
      ownerAddress: address,
      walletClient,
      publicClient,
    };
  }, [address, walletClient, publicClient]);

  useEffect(() => {
    if (connectError) {
      window._onEthConnectError?.(connectError.message);
    }
  }, [connectError]);

  useEffect(() => {
    if (address) {
      window._onEthConnected?.(address);
    }
  }, [address]);

  const handleClick = () => {
    if (isConnecting) return;
    if (isConnected) return; // already connected, nothing to do on click

    const connector = connectors[0];
    if (!connector) {
      window._onEthConnectError?.(
        "No browser wallet found. Install MetaMask or another injected wallet."
      );
      return;
    }
    connect({ connector });
  };

  const btnStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    padding: "18px 22px",
    borderRadius: "16px",
    background:
      "linear-gradient(135deg, rgba(255,255,255,0.09) 0%, rgba(255,255,255,0.04) 100%)",
    color: "rgba(232,234,242,0.92)",
    border: "1px solid rgba(200,208,235,0.18)",
    cursor: isConnecting ? "not-allowed" : "pointer",
    fontSize: "14.5px",
    fontWeight: 500,
    width: "100%",
    justifyContent: "center",
    opacity: isConnecting ? 0.55 : 1,
    fontFamily: "inherit",
    transition: "all 0.22s cubic-bezier(0.16,1,0.3,1)",
    boxShadow:
      "inset 0 1px 0 rgba(255,255,255,0.14), 0 4px 20px rgba(0,0,0,0.4)",
    letterSpacing: "-0.01em",
  };

  if (isConnected && address) {
    return (
      <div style={{ fontSize: "12.5px", color: "rgba(232,234,242,0.72)" }}>
        <div>Owner: {address}</div>
        <button
          onClick={() => disconnect()}
          style={{ ...btnStyle, marginTop: "10px", padding: "10px 16px" }}
        >
          Disconnect
        </button>
      </div>
    );
  }

  return (
    <button onClick={handleClick} disabled={isConnecting} style={btnStyle}>
      {isConnecting ? "Connecting…" : "Connect Wallet"}
    </button>
  );
}

function App() {
  return (
    <WagmiProvider config={wagmiConfig}>
      <QueryClientProvider client={queryClient}>
        <EthConnectButton />
      </QueryClientProvider>
    </WagmiProvider>
  );
}

function mountEthConnect() {
  const el = document.getElementById("eth-connect-root");
  if (!el) return;
  el.innerHTML = "";
  try {
    createRoot(el).render(<App />);
  } catch (e) {
    el.innerHTML = `<p style="color:#ff4c6a;font-size:12px">Bundle error: ${
      e instanceof Error ? e.message : e
    }</p>`;
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountEthConnect);
} else {
  mountEthConnect();
}
