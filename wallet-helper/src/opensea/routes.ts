import { Router } from "express";
import { isAddress, isHex, type Address } from "viem";
import {
  verifyOwnerSignature,
  verifySessionKeyMatchesAddress,
  fireMint,
  getPublicDropWindow,
} from "./ethClient.js";

export const openSeaRouter = Router();

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
  const { nftContract } = req.body as { nftContract?: string };

  if (!nftContract || !isAddress(nftContract)) {
    return res.status(400).json({ error: "Valid nftContract required" });
  }

  try {
    const window = await getPublicDropWindow(nftContract as Address);
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

// POST /eth/fire-mint — THE ONE ROUTE IN THIS FILE THAT SPENDS REAL ETH.
// Only ever called from Flask's own server-side watcher (opensea_automint's
// firing module) over loopback — this process only listens on 127.0.0.1
// (see server.ts), so it's unreachable from the public internet regardless.
// sessionPrivateKey here is the DECRYPTED session key — Flask decrypts it
// just before this call and never persists the plaintext.
openSeaRouter.post("/eth/fire-mint", async (req, res) => {
  const { sessionPrivateKey, nftContract, quantity, valueCapWei } = req.body as {
    sessionPrivateKey?: string;
    nftContract?: string;
    quantity?: number;
    valueCapWei?: string;
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

  try {
    const result = await fireMint({
      sessionPrivateKey: sessionPrivateKey as `0x${string}`,
      nftContract: nftContract as Address,
      quantity: quantity as number,
      valueCapWei: valueCapWeiBig,
    });
    return res.json(result);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[opensea:fire-mint]", msg);
    return res.status(500).json({ error: msg });
  }
});
