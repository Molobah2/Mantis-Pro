import { Router } from "express";
import { isAddress, type Address } from "viem";
import { deriveSmartAccountAddress, verifySessionGrantOwnership } from "./zerodevClient.js";

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
