import React, { useRef, useState, useEffect } from "react";
import { createRoot } from "react-dom/client";
import {
  AbstractWalletProvider,
  useLoginWithAbstract,
  useAbstractClient,
} from "@abstract-foundation/agw-react";
import { LimitType } from "@abstract-foundation/agw-client/sessions";
import { generatePrivateKey, privateKeyToAccount } from "viem/accounts";
import { abstract } from "viem/chains";

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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function runCreateSession(abstractClient: any) {
  window._onSessionProgress?.("Generating session key…");

  const sessionPrivKey = generatePrivateKey();
  const sessionAccount = privateKeyToAccount(sessionPrivKey);
  const expiresAt = BigInt(Math.floor(Date.now() / 1000) + 30 * 24 * 3600);

  window._onSessionProgress?.("Approve the session key in your wallet…");
  const { session } = await abstractClient.createSession({
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
          valueLimit: { limitType: LimitType.Unlimited, limit: 0n, period: 0n },
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
}

function AGWButton() {
  const { login } = useLoginWithAbstract();
  const abstractClient = useAbstractClient();
  const [busy, setBusy] = useState(false);
  const pendingRef = useRef(false);

  useEffect(() => {
    if (!abstractClient || !pendingRef.current) return;
    pendingRef.current = false;
    setBusy(true);
    runCreateSession(abstractClient)
      .catch((e: unknown) =>
        window._agwConnectError?.(e instanceof Error ? e.message : String(e))
      )
      .finally(() => setBusy(false));
  }, [abstractClient]);

  const handleClick = () => {
    if (busy) return;
    if (abstractClient) {
      setBusy(true);
      runCreateSession(abstractClient)
        .catch((e: unknown) =>
          window._agwConnectError?.(e instanceof Error ? e.message : String(e))
        )
        .finally(() => setBusy(false));
    } else {
      pendingRef.current = true;
      login();
    }
  };

  const btnStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    padding: "14px 22px",
    borderRadius: "12px",
    background: "#4cff91",
    color: "#000",
    border: "none",
    cursor: busy ? "not-allowed" : "pointer",
    fontSize: "15px",
    fontWeight: 700,
    width: "100%",
    justifyContent: "center",
    opacity: busy ? 0.6 : 1,
    fontFamily: "inherit",
    transition: "opacity 0.15s",
  };

  return (
    <button onClick={handleClick} disabled={busy} style={btnStyle}>
      <svg
        width="20"
        height="20"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        style={{ flexShrink: 0 }}
      >
        <rect x="3" y="11" width="18" height="11" rx="2" />
        <path d="M7 11V7a5 5 0 0 1 10 0v4" />
      </svg>
      {busy ? "Connecting to Abstract…" : "Connect Abstract Global Wallet"}
    </button>
  );
}

function App() {
  return (
    <AbstractWalletProvider chain={abstract}>
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
    el.innerHTML = `<p style="color:#ff4c6a;font-size:12px">Bundle error: ${e instanceof Error ? e.message : e}</p>`;
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountAGWConnect);
} else {
  mountAGWConnect();
}
