import express from "express";
import { createAbstractClient } from "@abstract-foundation/agw-client";
import { createSessionClient, LimitType } from "@abstract-foundation/agw-client/sessions";
import { privateKeyToAccount, generatePrivateKey } from "viem/accounts";
import { createWalletClient, http } from "viem";
import { eip712WalletActions } from "viem/zksync";
import { abstract, abstractTestnet } from "viem/chains";
import { openSeaRouter } from "./opensea/routes.js";

const app = express();
app.use(express.json({ limit: "64kb" }));

// OpenSea Auto-Mint: Ethereum-mainnet ZeroDev routes, kept in their own
// module (see src/opensea/) since they're a separate chain/account-
// abstraction stack from the Abstract/AGW routes below.
app.use(openSeaRouter);

const UPVOTE_CONTRACT = "0x3b50de27506f0a8c1f4122a1e6f470009a76ce2a" as `0x${string}`;
const UPVOTE_ABI = [
  {
    name: "voteForApp",
    type: "function",
    stateMutability: "nonpayable",
    inputs:  [{ name: "appId", type: "uint256" }],
    outputs: [],
  },
] as const;

/** Revive BigInt values serialized as digit strings from Python json.dumps.
 *  Matches ANY pure-digit string (including "0", "1") — safe because BigInts
 *  are the only values serialized this way; non-BigInt numbers are JSON numbers. */
function reviveBigInt(_k: string, v: unknown): unknown {
  if (typeof v === "string" && /^\d+$/.test(v)) return BigInt(v);
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
      functionName: "voteForApp",
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

// POST /create-session — server-side session creation using owner private key
app.post("/create-session", async (req, res) => {
  const { ownerPrivKey, network } = req.body as {
    ownerPrivKey: string;
    network:      string;
  };

  if (!ownerPrivKey) {
    return res.status(400).json({ error: "ownerPrivKey required" });
  }

  try {
    const chain = network === "testnet" ? abstractTestnet : abstract;
    const rpc   = network === "testnet"
      ? "https://api.testnet.abs.xyz"
      : "https://api.mainnet.abs.xyz";

    const normalizedKey  = ownerPrivKey.startsWith("0x") ? ownerPrivKey : `0x${ownerPrivKey}`;
    const ownerAccount   = privateKeyToAccount(normalizedKey as `0x${string}`);
    // createAbstractClient reads signer.address — pass PrivateKeyAccount directly (not a WalletClient)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const abstractClient = await createAbstractClient({ signer: ownerAccount as any, chain, transport: http(rpc) });
    const agwAddress = abstractClient.account.address;

    const sessionPrivKey   = generatePrivateKey();
    const sessionAccount   = privateKeyToAccount(sessionPrivKey);
    const expiresAt        = BigInt(Math.floor(Date.now() / 1000) + 30 * 24 * 3600);

    const { session } = await abstractClient.createSession({
      account: agwAddress,
      chain,
      session: {
        signer:    sessionAccount.address,
        expiresAt,
        feeLimit: {
          limitType: LimitType.Lifetime,
          limit:     BigInt("500000000000000"),
          period:    0n,
        },
        callPolicies: [{
          target:         UPVOTE_CONTRACT,
          selector:       "0x7060a227" as `0x${string}`,
          maxValuePerUse: 0n,
          valueLimit:     { limitType: LimitType.Unlimited, limit: 0n, period: 0n },
          constraints:    [],
        }],
        transferPolicies: [],
      },
    });

    const sessionConfigJson = JSON.stringify(session, (_k, v) =>
      typeof v === "bigint" ? v.toString() : v
    );

    return res.json({
      sessionPrivKey,
      sessionConfig: sessionConfigJson,
      agwAddress,
      expiresAt: Number(expiresAt),
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[create-session]", msg);
    return res.status(500).json({ error: msg });
  }
});

// POST /direct-upvote — call voteForApp(uint256) directly from an EOA, no AGW session needed
app.post("/direct-upvote", async (req, res) => {
  const { ownerPrivKey, appId, network } = req.body as {
    ownerPrivKey: string;
    appId:        number;
    network:      string;
  };
  if (!ownerPrivKey || appId == null) {
    return res.status(400).json({ error: "ownerPrivKey and appId required" });
  }
  try {
    const chain = network === "testnet" ? abstractTestnet : abstract;
    const rpc   = network === "testnet" ? "https://api.testnet.abs.xyz" : "https://api.mainnet.abs.xyz";
    const key   = ownerPrivKey.startsWith("0x") ? ownerPrivKey : `0x${ownerPrivKey}`;
    const walletClient = createWalletClient({
      account:   privateKeyToAccount(key as `0x${string}`),
      chain,
      transport: http(rpc),
    }).extend(eip712WalletActions());
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const txHash = await (walletClient as any).writeContract({
      address:      UPVOTE_CONTRACT,
      abi:          UPVOTE_ABI,
      functionName: "voteForApp",
      args:         [BigInt(appId)],
    });
    return res.json({ txHash });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[direct-upvote]", msg);
    return res.status(500).json({ error: msg });
  }
});

// POST /sign-message — sign a personal_sign message via AGW client (EIP-1271 compatible)
// Use this instead of raw EOA signing when the server verifies against a smart contract wallet.
app.post("/sign-message", async (req, res) => {
  const { ownerPrivKey, message } = req.body as {
    ownerPrivKey: string;
    message:      string;
  };
  if (!ownerPrivKey || !message) {
    return res.status(400).json({ error: "ownerPrivKey and message required" });
  }
  try {
    const key           = ownerPrivKey.startsWith("0x") ? ownerPrivKey : `0x${ownerPrivKey}`;
    const ownerAccount  = privateKeyToAccount(key as `0x${string}`);
    const abstractClient = await createAbstractClient({
      signer:    ownerAccount as any,
      chain:     abstract,
      transport: http("https://api.mainnet.abs.xyz"),
    });
    const signature = await abstractClient.signMessage({ message });
    return res.json({ signature, agwAddress: abstractClient.account.address });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[sign-message]", msg);
    return res.status(500).json({ error: msg });
  }
});

const PORT = parseInt(process.env.WALLET_HELPER_PORT ?? "3456", 10);
app.listen(PORT, "127.0.0.1", () => {
  console.log(`[wallet-helper] listening on 127.0.0.1:${PORT}`);
});
