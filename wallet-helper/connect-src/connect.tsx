import React, { useRef, useState, useEffect } from "react";
import { createRoot } from "react-dom/client";
import {
  AbstractWalletProvider,
  useLoginWithAbstract,
} from "@abstract-foundation/agw-react";
import { createAbstractClient } from "@abstract-foundation/agw-client";
import { LimitType } from "@abstract-foundation/agw-client/sessions";
import { generatePrivateKey, privateKeyToAccount, toAccount } from "viem/accounts";
import { abstract } from "viem/chains";
import { http } from "viem";
import { useConnectorClient } from "wagmi";

const UPVOTE_CONTRACT = "0x3b50de27506f0a8c1f4122a1e6f470009a76ce2a" as const;
// Same-origin RPC proxy — browser submits tx to our server, which forwards to Abstract.
// This bypasses the CORS restriction on https://api.mainnet.abs.xyz.
const RPC_PROXY = "/api/rpc";

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
  const { login } = useLoginWithAbstract();
  // useConnectorClient gives the underlying EOA wallet client after login()
  const { data: walletClient } = useConnectorClient();
  const [busy, setBusy] = useState(false);
  const pendingRef = useRef(false);

  useEffect(() => {
    // Fires after login() updates wagmi state with a connected wallet
    if (!walletClient || !pendingRef.current) return;
    pendingRef.current = false;
    doCreateSession(walletClient);
  }, [walletClient]);

  async function doCreateSession(wc: any) {
    setBusy(true);
    try {
      window._onSessionProgress?.("Generating session key…");

      // Build a toAccount signer backed by the connected wallet (Privy or extension)
      const signerAccount = toAccount({
        address: wc.account.address as `0x${string}`,
        signMessage: ({ message }: any) =>
          wc.signMessage({ message, account: wc.account }),
        signTypedData: (typedData: any) =>
          wc.signTypedData({ ...typedData, account: wc.account }),
        signTransaction: (tx: any) =>
          wc.signTransaction({ ...tx, account: wc.account }),
      });

      // Build the abstractClient using our same-origin RPC proxy.
      // Transaction submission goes to /api/rpc → our server → Abstract mainnet.
      // No CORS issue because it's same-origin from the browser's perspective.
      const abstractClient = await createAbstractClient({
        signer: signerAccount,
        chain: abstract,
        transport: http(RPC_PROXY),
      });

      const agwAddress: string = abstractClient.account.address;
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
    if (busy) return;
    if (walletClient) {
      // Already connected — go straight to session creation
      doCreateSession(walletClient);
    } else {
      // Trigger Privy login flow; doCreateSession fires via useEffect when walletClient arrives
      pendingRef.current = true;
      login();
    }
  };

  const btnStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    padding: "18px 22px",
    borderRadius: "16px",
    background:
      "linear-gradient(135deg, rgba(76,255,145,0.16) 0%, rgba(40,200,100,0.10) 100%)",
    color: "#78f0a8",
    border: "1px solid rgba(90,220,140,0.26)",
    cursor: busy ? "not-allowed" : "pointer",
    fontSize: "14.5px",
    fontWeight: 500,
    width: "100%",
    justifyContent: "center",
    opacity: busy ? 0.55 : 1,
    fontFamily: "inherit",
    transition: "all 0.22s cubic-bezier(0.16,1,0.3,1)",
    boxShadow:
      "inset 0 1px 0 rgba(150,255,190,0.18), 0 6px 32px rgba(50,180,100,0.14)",
    letterSpacing: "-0.01em",
  };

  return (
    <button onClick={handleClick} disabled={busy} style={btnStyle}>
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
      {busy ? "Connecting…" : "Connect Abstract Global Wallet"}
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
