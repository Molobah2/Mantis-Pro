import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  WagmiProvider,
  createConfig,
  http,
  useAccount,
  useConnect,
  useDisconnect,
} from "wagmi";
import { injected } from "wagmi/connectors";
import { mainnet } from "viem/chains";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Deliberately separate from connect.tsx (Abstract/AGW, session-key
// automation) — this is Ethereum mainnet, a plain EOA wallet connection,
// no session key yet. Keeping the two bundles and their window callback
// namespaces disjoint so the two chains/account models can never cross-wire.
const wagmiConfig = createConfig({
  chains: [mainnet],
  connectors: [injected()],
  transports: { [mainnet.id]: http() },
});

const queryClient = new QueryClient();

declare global {
  interface Window {
    // Optional: every call site uses `?.` deliberately, so a host page that
    // hasn't wired one of these up degrades to a silent no-op rather than a
    // runtime error — matches the type to how it's actually used below.
    _onEthConnected?: (ownerAddress: string, smartAccountAddress: string) => void;
    _onEthConnectProgress?: (text: string) => void;
    _onEthConnectError?: (msg: string) => void;
  }
}

function isSmartAccountAddressResponse(
  data: unknown
): data is { smartAccountAddress: string } {
  return (
    typeof data === "object" &&
    data !== null &&
    typeof (data as { smartAccountAddress?: unknown }).smartAccountAddress === "string"
  );
}

function EthConnectButton() {
  const { address, isConnected } = useAccount();
  const { connect, connectors, isPending: isConnecting, error: connectError } =
    useConnect();
  const { disconnect } = useDisconnect();
  const [deriving, setDeriving] = useState(false);
  const [smartAccountAddress, setSmartAccountAddress] = useState<string | null>(
    null
  );

  useEffect(() => {
    if (connectError) {
      window._onEthConnectError?.(connectError.message);
    }
  }, [connectError]);

  // Once a wallet connects, derive its smart account address via our own
  // backend (never the Node helper directly — that's loopback-only).
  useEffect(() => {
    if (!address) {
      setSmartAccountAddress(null);
      return;
    }
    let cancelled = false;
    setDeriving(true);
    window._onEthConnectProgress?.("Deriving smart account address…");

    fetch(`/api/opensea/eth/smart-account-address?owner=${encodeURIComponent(address)}`)
      .then((res) => {
        if (!res.ok) throw new Error(`API ${res.status}`);
        return res.json() as Promise<unknown>;
      })
      .then((data) => {
        if (cancelled) return;
        if (!isSmartAccountAddressResponse(data)) {
          throw new Error("Unexpected response shape from smart-account-address API");
        }
        setSmartAccountAddress(data.smartAccountAddress);
        window._onEthConnected?.(address, data.smartAccountAddress);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : String(e);
        window._onEthConnectError?.(msg);
      })
      .finally(() => {
        if (!cancelled) setDeriving(false);
      });

    return () => {
      cancelled = true;
    };
  }, [address]);

  const handleClick = () => {
    if (isConnecting || deriving) return;
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
    cursor: isConnecting || deriving ? "not-allowed" : "pointer",
    fontSize: "14.5px",
    fontWeight: 500,
    width: "100%",
    justifyContent: "center",
    opacity: isConnecting || deriving ? 0.55 : 1,
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
        <div>
          Smart account:{" "}
          {deriving ? "deriving…" : smartAccountAddress ?? "unavailable"}
        </div>
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
