import { Router } from "express";
import { isAddress, isHex, type Address } from "viem";
import {
  verifyOwnerSignature,
  verifySessionKeyMatchesAddress,
  fireMint,
  fireSignedMint,
  getPublicDropWindow,
  getRecentPublicDropUpdates,
  type ChainName,
  type SignedMintParamsInput,
} from "./ethClient.js";

export const openSeaRouter = Router();

const KNOWN_CHAINS: readonly ChainName[] = ["ethereum", "robinhood"];

// Every route below defaults an omitted/undefined `chain` to "ethereum" —
// existing callers (Python's node_client.py, before this feature) never
// sent one, so this keeps them working unchanged.
function parseChain(raw: unknown): ChainName | null {
  if (raw === undefined || raw === null) return "ethereum";
  if (typeof raw !== "string") return null;
  return (KNOWN_CHAINS as readonly string[]).includes(raw) ? (raw as ChainName) : null;
}

// POST /eth/verify-owner-signature — authorizes grant/arm/cancel actions.
// Confirms the caller actually controls ownerAddress before Flask acts on
// it — without this, those routes would trust a bare, self-reported
// address (public knowledge for any wallet), letting anyone act on someone
// else's behalf.
openSeaRouter.post("/eth/verify-owner-signature", async (req, res) => {
  const { ownerAddress, message, signature } = req.body as {
    ownerAddress?: string;
    message?: string;
    signature?: string;
  };

  if (!ownerAddress || !isAddress(ownerAddress)) {
    return res.status(400).json({ error: "Valid ownerAddress required" });
  }
  if (!message || typeof message !== "string") {
    return res.status(400).json({ error: "message required" });
  }
  if (!signature || typeof signature !== "string" || !signature.startsWith("0x")) {
    return res.status(400).json({ error: "Valid signature required" });
  }

  try {
    const valid = await verifyOwnerSignature(
      ownerAddress as Address,
      message,
      signature as `0x${string}`
    );
    return res.json({ valid });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[opensea:verify-owner-signature]", msg);
    return res.status(500).json({ error: msg });
  }
});

// POST /eth/verify-session-key — non-firing. Confirms a browser-produced
// session private key actually corresponds to the sessionAddress a grant
// request claims for it, before Flask ever persists the (encrypted) key.
// Never touches the key beyond this one derivation — it's discarded
// immediately after the response.
openSeaRouter.post("/eth/verify-session-key", (req, res) => {
  const { sessionPrivateKey, sessionAddress } = req.body as {
    sessionPrivateKey?: string;
    sessionAddress?: string;
  };

  if (!sessionPrivateKey || !isHex(sessionPrivateKey) || sessionPrivateKey.length !== 66) {
    return res.status(400).json({ error: "Valid sessionPrivateKey required" });
  }
  if (!sessionAddress || !isAddress(sessionAddress)) {
    return res.status(400).json({ error: "Valid sessionAddress required" });
  }

  try {
    const valid = verifySessionKeyMatchesAddress(
      sessionPrivateKey as `0x${string}`,
      sessionAddress as Address
    );
    return res.json({ valid });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[opensea:verify-session-key]", msg);
    return res.status(500).json({ error: msg });
  }
});

// POST /eth/public-drop-window — read-only, no wallet needed. Used by the
// Python firing watcher to know a drop's real on-chain start/end time.
openSeaRouter.post("/eth/public-drop-window", async (req, res) => {
  const { nftContract, chain } = req.body as { nftContract?: string; chain?: string };

  if (!nftContract || !isAddress(nftContract)) {
    return res.status(400).json({ error: "Valid nftContract required" });
  }
  const chainName = parseChain(chain);
  if (!chainName) {
    return res.status(400).json({ error: `chain must be one of: ${KNOWN_CHAINS.join(", ")}` });
  }

  try {
    const window = await getPublicDropWindow(nftContract as Address, chainName);
    if (!window) {
      return res.json({ available: false });
    }
    return res.json({ available: true, ...window });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[opensea:public-drop-window]", msg);
    return res.status(500).json({ error: msg });
  }
});

// POST /eth/recent-public-drop-updates — read-only, no wallet needed. Used
// by the Python on-chain discovery job to find collections that have
// configured a SeaDrop public mint stage, independent of OpenSea's own
// /drops listing page. fromBlock omitted/null means "start from a recent
// lookback" (see ethClient.ts's INITIAL_LOOKBACK_BLOCKS) — the caller's
// first-ever call, before it has a scan bookmark of its own.
openSeaRouter.post("/eth/recent-public-drop-updates", async (req, res) => {
  const { fromBlock } = req.body as { fromBlock?: string | null };

  let fromBlockBig: bigint | null = null;
  if (fromBlock !== undefined && fromBlock !== null) {
    try {
      fromBlockBig = BigInt(fromBlock);
    } catch {
      return res.status(400).json({ error: "fromBlock must be a numeric string" });
    }
    if (fromBlockBig < 0n) {
      return res.status(400).json({ error: "fromBlock must be non-negative" });
    }
  }

  try {
    const result = await getRecentPublicDropUpdates(fromBlockBig);
    return res.json(result);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[opensea:recent-public-drop-updates]", msg);
    return res.status(500).json({ error: msg });
  }
});

// POST /eth/fire-mint — THE ONE ROUTE IN THIS FILE THAT SPENDS REAL ETH.
// Only ever called from Flask's own server-side watcher (opensea_automint's
// firing module) over loopback — this process only listens on 127.0.0.1
// (see server.ts), so it's unreachable from the public internet regardless.
// sessionPrivateKey here is the DECRYPTED session key — Flask decrypts it
// just before this call and never persists the plaintext.
openSeaRouter.post("/eth/fire-mint", async (req, res) => {
  const { sessionPrivateKey, nftContract, quantity, valueCapWei, chain } = req.body as {
    sessionPrivateKey?: string;
    nftContract?: string;
    quantity?: number;
    valueCapWei?: string;
    chain?: string;
  };

  if (!sessionPrivateKey || !isHex(sessionPrivateKey) || sessionPrivateKey.length !== 66) {
    return res.status(400).json({ error: "Valid sessionPrivateKey required" });
  }
  if (!nftContract || !isAddress(nftContract)) {
    return res.status(400).json({ error: "Valid nftContract required" });
  }
  if (!Number.isInteger(quantity) || (quantity as number) <= 0) {
    return res.status(400).json({ error: "quantity must be a positive integer" });
  }
  let valueCapWeiBig: bigint;
  try {
    valueCapWeiBig = BigInt(valueCapWei ?? "");
  } catch {
    return res.status(400).json({ error: "valueCapWei must be a numeric string" });
  }
  if (valueCapWeiBig < 0n) {
    return res.status(400).json({ error: "valueCapWei must be non-negative" });
  }
  const chainName = parseChain(chain);
  if (!chainName) {
    return res.status(400).json({ error: `chain must be one of: ${KNOWN_CHAINS.join(", ")}` });
  }

  try {
    const result = await fireMint({
      sessionPrivateKey: sessionPrivateKey as `0x${string}`,
      nftContract: nftContract as Address,
      quantity: quantity as number,
      valueCapWei: valueCapWeiBig,
      chain: chainName,
    });
    return res.json(result);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[opensea:fire-mint]", msg);
    return res.status(500).json({ error: msg });
  }
});

const MINT_PARAMS_FIELDS = [
  "mintPrice",
  "maxTotalMintableByWallet",
  "startTime",
  "endTime",
  "dropStageIndex",
  "maxTokenSupplyForStage",
  "feeBps",
] as const;

function parseMintParams(raw: unknown): SignedMintParamsInput | null {
  if (typeof raw !== "object" || raw === null) return null;
  const obj = raw as Record<string, unknown>;
  if (typeof obj.restrictFeeRecipients !== "boolean") return null;

  const parsed: Partial<Record<(typeof MINT_PARAMS_FIELDS)[number], bigint>> = {};
  for (const field of MINT_PARAMS_FIELDS) {
    const value = obj[field];
    if (typeof value !== "string" && typeof value !== "number") return null;
    try {
      parsed[field] = BigInt(value);
    } catch {
      return null;
    }
  }
  return { ...(parsed as Record<(typeof MINT_PARAMS_FIELDS)[number], bigint>),
    restrictFeeRecipients: obj.restrictFeeRecipients };
}

// POST /eth/fire-signed-mint — THE OTHER ROUTE IN THIS FILE THAT SPENDS
// REAL ETH. Allowlist/presale counterpart to /eth/fire-mint — used when a
// drop's active stage requires SeaDrop's mintSigned() rather than
// mintPublic(). mintParams/salt/signature come from Flask's
// opensea_session.py, which fetched them fresh from OpenSea's own backend
// (using the connected owner's replayed OpenSea browser session) right
// before this call — this route never derives or trusts any of that data
// itself, only passes it through to the on-chain call.
openSeaRouter.post("/eth/fire-signed-mint", async (req, res) => {
  const { sessionPrivateKey, nftContract, quantity, valueCapWei, mintParams, salt, signature, chain } =
    req.body as {
      sessionPrivateKey?: string;
      nftContract?: string;
      quantity?: number;
      valueCapWei?: string;
      mintParams?: unknown;
      salt?: string;
      signature?: string;
      chain?: string;
    };

  if (!sessionPrivateKey || !isHex(sessionPrivateKey) || sessionPrivateKey.length !== 66) {
    return res.status(400).json({ error: "Valid sessionPrivateKey required" });
  }
  if (!nftContract || !isAddress(nftContract)) {
    return res.status(400).json({ error: "Valid nftContract required" });
  }
  if (!Number.isInteger(quantity) || (quantity as number) <= 0) {
    return res.status(400).json({ error: "quantity must be a positive integer" });
  }
  let valueCapWeiBig: bigint;
  try {
    valueCapWeiBig = BigInt(valueCapWei ?? "");
  } catch {
    return res.status(400).json({ error: "valueCapWei must be a numeric string" });
  }
  if (valueCapWeiBig < 0n) {
    return res.status(400).json({ error: "valueCapWei must be non-negative" });
  }
  const parsedMintParams = parseMintParams(mintParams);
  if (!parsedMintParams) {
    return res.status(400).json({ error: "Valid mintParams required" });
  }
  let saltBig: bigint;
  try {
    saltBig = BigInt(salt ?? "");
  } catch {
    return res.status(400).json({ error: "salt must be a numeric string" });
  }
  if (!signature || !isHex(signature)) {
    return res.status(400).json({ error: "Valid signature required" });
  }
  const chainName = parseChain(chain);
  if (!chainName) {
    return res.status(400).json({ error: `chain must be one of: ${KNOWN_CHAINS.join(", ")}` });
  }

  try {
    const result = await fireSignedMint({
      sessionPrivateKey: sessionPrivateKey as `0x${string}`,
      nftContract: nftContract as Address,
      quantity: quantity as number,
      valueCapWei: valueCapWeiBig,
      mintParams: parsedMintParams,
      salt: saltBig,
      signature: signature as `0x${string}`,
      chain: chainName,
    });
    return res.json(result);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[opensea:fire-signed-mint]", msg);
    return res.status(500).json({ error: msg });
  }
});
