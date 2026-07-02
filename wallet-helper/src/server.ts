import express from "express";
import { createSessionClient } from "@abstract-foundation/agw-client/sessions";
import { privateKeyToAccount } from "viem/accounts";
import { http } from "viem";
import { abstract, abstractTestnet } from "viem/chains";

const app = express();
app.use(express.json({ limit: "64kb" }));

const UPVOTE_CONTRACT = "0x3b50de27506f0a8c1f4122a1e6f470009a76ce2a" as `0x${string}`;
const UPVOTE_ABI = [
  {
    name: "upvote",
    type: "function",
    stateMutability: "nonpayable",
    inputs:  [{ name: "appId", type: "uint256" }],
    outputs: [],
  },
] as const;

/** Revive BigInt values serialized as digit strings from Python json.dumps. */
function reviveBigInt(_k: string, v: unknown): unknown {
  if (typeof v === "string" && /^\d{10,}$/.test(v)) return BigInt(v);
  return v;
}

app.get("/health", (_req, res) => {
  res.json({ ok: true });
});

app.post("/upvote", async (req, res) => {
  const { sessionPrivKey, agwAddress, sessionConfig, appId, network } =
    req.body as {
      sessionPrivKey: string;
      agwAddress:     string;
      sessionConfig:  string;
      appId:          number;
      network:        string;
    };

  if (!sessionPrivKey || !agwAddress || !sessionConfig || appId == null) {
    return res.status(400).json({ error: "Missing required fields" });
  }

  try {
    const chain     = network === "testnet" ? abstractTestnet : abstract;
    const rpc       = network === "testnet"
      ? "https://api.testnet.abs.xyz"
      : "https://api.mainnet.abs.xyz";

    const signer    = privateKeyToAccount(sessionPrivKey as `0x${string}`);
    const session   = JSON.parse(sessionConfig, reviveBigInt);

    const sessionClient = createSessionClient({
      account:   agwAddress as `0x${string}`,
      chain,
      signer,
      session,
      transport: http(rpc),
    });

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const txHash = await (sessionClient as any).writeContract({
      address:      UPVOTE_CONTRACT,
      abi:          UPVOTE_ABI,
      functionName: "upvote",
      args:         [BigInt(appId)],
      chain,
      account:      agwAddress as `0x${string}`,
    });

    return res.json({ txHash });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[upvote]", msg);
    return res.status(500).json({ error: msg });
  }
});

const PORT = parseInt(process.env.WALLET_HELPER_PORT ?? "3456", 10);
app.listen(PORT, "127.0.0.1", () => {
  console.log(`[wallet-helper] listening on 127.0.0.1:${PORT}`);
});
