import { Router } from "express";
import { isAddress, type Address } from "viem";
import { deriveSmartAccountAddress } from "./zerodevClient.js";

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
