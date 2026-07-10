import React, { useRef, useState, useEffect } from "react";
import { createRoot } from "react-dom/client";
import {
  AbstractWalletProvider,
  useAbstractClient,
  useCreateSession,
} from "@abstract-foundation/agw-react";
import { useConnect, useDisconnect } from "wagmi";
import { LimitType } from "@abstract-foundation/agw-client/sessions";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import { abstract } from "viem/chains";
import { http } from "viem";

const UPVOTE_CONTRACT = "0x3b50de27506f0a8c1f4122a1e6f470009a76ce2a" as const;

declare global {
  interface Window {
    _onSessionCreated: (
      sessionPrivKey: string,
      sessionConfig: string,
      agwAddress: string,
      expiresAt: number
    ) => void;
    _onSessionProgress: (text: string) => void;
    _agwConnectError: (msg: string) => void;
  }
}

function AGWButton() {
  const { connect, connectors, isPending: isConnecting, error: connectError } = useConnect();
  const { data: abstractClient } = useAbstractClient();
  const { createSessionAsync } = useCreateSession();
  const [busy, setBusy] = useState(false);
  const pendingRef = useRef(false);

  // Surface wagmi connect errors immediately
  useEffect(() => {
    if (connectError) {
      window._agwConnectError?.(connectError.message);
    }
  }, [connectError]);

  // Once abstractClient is available after login, create the session
  useEffect(() => {
    if (!abstractClient || !pendingRef.current) return;
    pendingRef.current = false;
    doCreateSession();
  }, [abstractClient]);

  async function doCreateSession() {
    if (!abstractClient) return;
    setBusy(true);
    try {
      window._onSessionProgress?.("Generating session key…");

      const sessionPrivKey = generatePrivateKey();
      const sessionAccount = privateKeyToAccount(sessionPrivKey);
      const expiresAt = BigInt(Math.floor(Date.now() / 1000) + 30 * 24 * 3600);

      window._onSessionProgress?.("Approve the session key in your wallet…");
      const { session } = await createSessionAsync({
        session: {
          signer: sessionAccount.address,
          expiresAt,
          feeLimit: {
            limitType: LimitType.Lifetime,
            limit: BigInt("500000000000000"),
            period: 0n,
          },
          callPolicies: [
            {
              target: UPVOTE_CONTRACT,
              selector: "0x7060a227" as `0x${string}`,
              maxValuePerUse: 0n,
              valueLimit: {
                limitType: LimitType.Unlimited,
                limit: 0n,
                period: 0n,
              },
              constraints: [],
            },
          ],
          transferPolicies: [],
        },
      });

      const agwAddress: string = abstractClient.account.address;
      const sessionConfigJson = JSON.stringify(session, (_k, v) =>
        typeof v === "bigint" ? v.toString() : v
      );

      window._onSessionProgress?.("Storing session…");
      window._onSessionCreated(
        sessionPrivKey,
        sessionConfigJson,
        agwAddress,
        Number(expiresAt)
      );
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      window._agwConnectError?.(msg);
    } finally {
      setBusy(false);
    }
  }

  const handleClick = () => {
    if (busy || isConnecting) return;

    if (abstractClient) {
      doCreateSession();
      return;
    }

    const connector = connectors.find((c) => c.id === "xyz.abs.privy");
    if (!connector) {
      window._agwConnectError?.(
        "Abstract wallet connector not found. Please reload the page and try again."
      );
      return;
    }

    pendingRef.current = true;
    connect({ connector });
  };

  const isLoading = busy || isConnecting;

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
    cursor: isLoading ? "not-allowed" : "pointer",
    fontSize: "14.5px",
    fontWeight: 500,
    width: "100%",
    justifyContent: "center",
    opacity: isLoading ? 0.55 : 1,
    fontFamily: "inherit",
    transition: "all 0.22s cubic-bezier(0.16,1,0.3,1)",
    boxShadow:
      "inset 0 1px 0 rgba(255,255,255,0.14), 0 4px 20px rgba(0,0,0,0.4)",
    letterSpacing: "-0.01em",
  };

  return (
    <button onClick={handleClick} disabled={isLoading} style={btnStyle}>
      <svg
        width="16"
        height="16"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ flexShrink: 0 }}
      >
        <rect x="3" y="11" width="18" height="11" rx="2" />
        <path d="M7 11V7a5 5 0 0 1 10 0v4" />
      </svg>
      {isLoading ? "Connecting…" : "Connect Abstract Global Wallet"}
    </button>
  );
}

function App() {
  return (
    <AbstractWalletProvider chain={abstract} transport={http("/api/rpc")}>
      <AGWButton />
    </AbstractWalletProvider>
  );
}

function mountAGWConnect() {
  const el = document.getElementById("agw-root");
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
  document.addEventListener("DOMContentLoaded", mountAGWConnect);
} else {
  mountAGWConnect();
}
