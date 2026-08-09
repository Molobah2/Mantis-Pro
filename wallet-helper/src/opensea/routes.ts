import { Router } from "express";
import { isAddress, type Address } from "viem";
import {
  deriveSmartAccountAddress,
  verifySessionGrantOwnership,
  verifyOwnerSignature,
  fireMint,
  getPublicDropWindow,
} from "./zerodevClient.js";

export const openSeaRouter = Router();

// POST /eth/smart-account-address — non-firing, read-only. Given an owner's
// EOA address, returns the counterfactual Ethereum-mainnet ZeroDev smart
// account address for it. No signing, no transaction, no session key.
openSeaRouter.post("/eth/smart-account-address", async (req, res) => {
  const { ownerAddress } = req.body as { ownerAddress?: string };

  if (!ownerAddress || !isAddress(ownerAddress)) {
    return res.status(400).json({ error: "Valid ownerAddress required" });
  }

  try {
    const smartAccountAddress = await deriveSmartAccountAddress(
      ownerAddress as Address
    );
    return res.json({ ownerAddress, smartAccountAddress });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[opensea:smart-account-address]", msg);
    return res.status(500).json({ error: msg });
  }
});

// POST /eth/verify-session-grant — non-firing. Confirms a browser-produced
// serialized session-key approval genuinely resolves to the claimed owner
// and smart-account addresses before Flask ever persists it. Never touches
// a private key; the approval itself was already fully constructed and
// signed in the browser — this only deserializes and cross-checks it.
openSeaRouter.post("/eth/verify-session-grant", async (req, res) => {
  const { serializedApproval, ownerAddress, smartAccountAddress } = req.body as {
    serializedApproval?: string;
    ownerAddress?: string;
    smartAccountAddress?: string;
  };

  if (!serializedApproval || typeof serializedApproval !== "string") {
    return res.status(400).json({ error: "serializedApproval required" });
  }
  if (!ownerAddress || !isAddress(ownerAddress)) {
    return res.status(400).json({ error: "Valid ownerAddress required" });
  }
  if (!smartAccountAddress || !isAddress(smartAccountAddress)) {
    return res.status(400).json({ error: "Valid smartAccountAddress required" });
  }

  try {
    const result = await verifySessionGrantOwnership(
      serializedApproval,
      ownerAddress as Address,
      smartAccountAddress as Address
    );
    if (!result.valid) {
      return res.status(400).json({ error: result.error ?? "Verification failed" });
    }
    return res.json({ valid: true });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("[opensea:verify-session-grant]", msg);
    return res.status(500).json({ error: msg });
  }
});

// POST /eth/verify-owner-signature — authorizes arm/cancel actions.
// Confirms the caller actually controls ownerAddress before Flask acts on
// it — without this, arm/cancel would trust a bare, self-reported address
// (public knowledge for any wallet), letting anyone act on someone else's
// behalf.
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
// The serializedApproval here is the DECRYPTED session-key blob — Flask
// decrypts it just before this call and never persists the plaintext.
openSeaRouter.post("/eth/fire-mint", async (req, res) => {
  const { serializedApproval, nftContract, smartAccountAddress, quantity, valueCapWei } = req.body as {
    serializedApproval?: string;
    nftContract?: string;
    smartAccountAddress?: string;
    quantity?: number;
    valueCapWei?: string;
  };

  if (!serializedApproval || typeof serializedApproval !== "string") {
    return res.status(400).json({ error: "serializedApproval required" });
  }
  if (!nftContract || !isAddress(nftContract)) {
    return res.status(400).json({ error: "Valid nftContract required" });
  }
  if (!smartAccountAddress || !isAddress(smartAccountAddress)) {
    return res.status(400).json({ error: "Valid smartAccountAddress required" });
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
      serializedApproval,
      nftContract: nftContract as Address,
      smartAccountAddress: smartAccountAddress as Address,
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
