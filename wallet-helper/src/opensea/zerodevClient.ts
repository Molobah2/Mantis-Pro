/**
 * Ethereum-mainnet ZeroDev smart-account plumbing for the OpenSea Auto-Mint
 * tool. Separate chain, separate account-abstraction stack from the rest of
 * wallet-helper (which is Abstract-chain/AGW-only) — deliberately kept in
 * its own module so the two never get confused.
 *
 * Most of this file is READ-ONLY / non-firing (address derivation, approval
 * verification). fireMint at the bottom is the one exception — it submits a
 * REAL UserOperation that spends real ETH. Everything above it never
 * touches a private key or submits a transaction.
 */
import { http, type Address, createPublicClient, encodeFunctionData, verifyMessage } from "viem";
import { mainnet } from "viem/chains";
import { signerToEcdsaValidator } from "@zerodev/ecdsa-validator";
import { createKernelAccount, createKernelAccountClient, addressToEmptyAccount } from "@zerodev/sdk";
import { getEntryPoint, KERNEL_V3_1 } from "@zerodev/sdk/constants";
import { deserializePermissionAccount } from "@zerodev/permissions";

// eth.llamarpc.com (the original default here) had a real, hours-long
// outage in production (Cloudflare 521) — publicnode has been reliable
// across every test run during this feature's development. Still just a
// free public RPC; RPC_ETHEREUM_MAINNET overrides it with a paid provider
// whenever that's set up.
const ETH_RPC =
  process.env.RPC_ETHEREUM_MAINNET ?? "https://ethereum-rpc.publicnode.com";

const entryPoint = getEntryPoint("0.7");

// Stateless config — one client is reused across calls rather than
// reconstructed per request.
const publicClient = createPublicClient({ chain: mainnet, transport: http(ETH_RPC) });

/**
 * Derives the counterfactual ZeroDev Kernel smart-account address for a
 * given owner EOA address. Uses addressToEmptyAccount — an address-only
 * viem account whose signing methods all throw — because computing this
 * address only requires the owner's public address encoded into the
 * account's init data, never a real signature. This is what makes it safe
 * to run server-side without the owner's private key ever being involved.
 */
export async function deriveSmartAccountAddress(
  ownerAddress: Address
): Promise<Address> {
  const emptyOwner = addressToEmptyAccount(ownerAddress);

  const ecdsaValidator = await signerToEcdsaValidator(publicClient, {
    signer: emptyOwner,
    entryPoint,
    kernelVersion: KERNEL_V3_1,
  });

  const account = await createKernelAccount(publicClient, {
    plugins: { sudo: ecdsaValidator },
    entryPoint,
    kernelVersion: KERNEL_V3_1,
  });

  return account.address;
}

/**
 * Verifies a browser-produced serialized session-key approval actually
 * corresponds to the claimed owner/smart-account addresses BEFORE the
 * backend ever persists it.
 *
 * Why this matters: serializePermissionAccount() requires the sudo signer
 * to actually be able to sign (confirmed empirically — an address-only
 * signer throws "Method not supported" if you try), so an attacker cannot
 * forge a working approval for an address they don't control the private
 * key for. But nothing stops a client from POSTing a genuinely-valid
 * approval alongside FALSE ownerAddress/smartAccountAddress metadata
 * fields — e.g. their own real approval, relabeled as someone else's. This
 * closes that gap: deserializing the blob and checking its real resolved
 * address against BOTH the claimed smartAccountAddress AND the
 * independently-recomputed deterministic address for the claimed
 * ownerAddress is only satisfiable if the approval was genuinely produced
 * by that owner's real wallet.
 */
export async function verifySessionGrantOwnership(
  serializedApproval: string,
  claimedOwnerAddress: Address,
  claimedSmartAccountAddress: Address
): Promise<{ valid: boolean; error?: string }> {
  let reconstructedAddress: Address;
  try {
    const reconstructed = await deserializePermissionAccount(
      publicClient,
      entryPoint,
      KERNEL_V3_1,
      serializedApproval
    );
    reconstructedAddress = reconstructed.address;
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { valid: false, error: `Could not deserialize approval: ${msg}` };
  }

  if (reconstructedAddress.toLowerCase() !== claimedSmartAccountAddress.toLowerCase()) {
    return {
      valid: false,
      error: "Approval does not resolve to the claimed smart account address",
    };
  }

  const expectedAddress = await deriveSmartAccountAddress(claimedOwnerAddress);
  if (expectedAddress.toLowerCase() !== claimedSmartAccountAddress.toLowerCase()) {
    return {
      valid: false,
      error: "Claimed smart account address does not match the claimed owner address",
    };
  }

  return { valid: true };
}

/**
 * Verifies a plain EOA signature over an arbitrary message actually
 * recovers to the claimed owner address — used to authorize arm/cancel
 * actions (opensea_automint/routes.py's /api/opensea/arm and
 * /api/opensea/arm/<id>/cancel), which otherwise would accept a bare,
 * self-reported ownerAddress with no proof of control (Ethereum addresses
 * are public; anyone could otherwise arm or cancel on someone else's
 * behalf). Deliberately a plain personal_sign check, not the ZeroDev
 * session-key machinery above — arming/cancelling doesn't need a smart
 * account, just proof the caller controls the EOA they claim to be.
 */
export async function verifyOwnerSignature(
  ownerAddress: Address,
  message: string,
  signature: `0x${string}`
): Promise<boolean> {
  try {
    return await verifyMessage({ address: ownerAddress, message, signature });
  } catch {
    return false;
  }
}

// ── Firing (the one part of this file that spends real ETH) ────────────────

// Same canonical address used in the browser-side permission construction
// (wallet-helper/connect-src/eth-connect.tsx) — verified against a real
// deployed drop earlier in this project via a simulateContract call that
// reverted with SeaDrop's own IncorrectPayment error, not a generic revert.
const SEADROP_ADDRESS: Address = "0x00005EA00Ac477B1030CE78506496e8C2dE24bf5";

const SEADROP_ABI = [
  {
    name: "mintPublic",
    type: "function",
    stateMutability: "payable",
    inputs: [
      { name: "nftContract", type: "address" },
      { name: "feeRecipient", type: "address" },
      { name: "minterIfNotPayer", type: "address" },
      { name: "quantity", type: "uint256" },
    ],
    outputs: [],
  },
  {
    name: "getAllowedFeeRecipients",
    type: "function",
    stateMutability: "view",
    inputs: [{ name: "nftContract", type: "address" }],
    outputs: [{ name: "", type: "address[]" }],
  },
  {
    name: "getPublicDrop",
    type: "function",
    stateMutability: "view",
    inputs: [{ name: "nftContract", type: "address" }],
    outputs: [
      {
        name: "",
        type: "tuple",
        components: [
          { name: "mintPrice", type: "uint80" },
          { name: "startTime", type: "uint48" },
          { name: "endTime", type: "uint48" },
          { name: "maxTotalMintableByWallet", type: "uint16" },
          { name: "feeBps", type: "uint16" },
          { name: "restrictFeeRecipients", type: "bool" },
        ],
      },
    ],
  },
] as const;

const ZERODEV_PROJECT_ID = process.env.ZERODEV_PROJECT_ID ?? "";
// ZeroDev's v3 API: one URL serves as both bundler and (when a project has
// gas sponsorship configured) paymaster endpoint. Chain 1 = Ethereum mainnet.
const ZERODEV_BUNDLER_URL = `https://rpc.zerodev.app/api/v3/${ZERODEV_PROJECT_ID}/chain/1`;

const USER_OP_RECEIPT_TIMEOUT_MS = 60_000;

export interface PublicDropWindow {
  startTime: number;
  endTime: number;
  mintPriceWei: string;
}

/**
 * Read-only, no-wallet-required lookup of a collection's real on-chain
 * public mint window — used by the firing watcher to know precisely when a
 * drop actually goes live/ends, rather than relying on scraped page text.
 * Verified live against real collections before being relied on anywhere
 * (see opensea_automint/RESEARCH_NOTES.md-adjacent history — same call this
 * project's expiration-suggestion feature already uses).
 *
 * Returns null when the contract doesn't implement a public drop stage
 * (revert) or when startTime/endTime are unset (0) — an expected "not
 * available" outcome, not an error.
 */
export async function getPublicDropWindow(
  nftContract: Address
): Promise<PublicDropWindow | null> {
  try {
    const drop = await publicClient.readContract({
      address: SEADROP_ADDRESS,
      abi: SEADROP_ABI,
      functionName: "getPublicDrop",
      args: [nftContract],
    });
    if (!drop.startTime && !drop.endTime) return null;
    return {
      startTime: Number(drop.startTime),
      endTime: Number(drop.endTime),
      mintPriceWei: drop.mintPrice.toString(),
    };
  } catch {
    return null;
  }
}

export interface FireMintParams {
  serializedApproval: string;
  nftContract: Address;
  smartAccountAddress: Address;
  quantity: number;
  valueCapWei: bigint;
}

export interface FireMintResult {
  success: boolean;
  userOpHash: string;
  txHash: string | null;
  blockNumber: string | null;
  gasUsed: string | null;
  error?: string;
  // True ONLY for the "submitted but couldn't confirm the outcome within
  // the timeout" case — a real UserOperation may or may not have landed
  // on-chain. Deliberately distinct from a confirmed on-chain failure
  // (success: false, ambiguous: false/absent): callers must never treat
  // an ambiguous outcome as safe to retry, since the first attempt might
  // still land and a second submission would risk a genuine duplicate
  // spend/mint.
  ambiguous?: boolean;
}

/**
 * Submits a REAL mintPublic() UserOperation using an already-granted,
 * already-scoped session-key permission. This is the one function in this
 * codebase that spends real ETH — everything it does is re-verified against
 * the live chain immediately before submitting, never trusted from
 * caller-supplied values alone:
 *
 *  - feeRecipient is looked up fresh via getAllowedFeeRecipients (SeaDrop
 *    rejects an unregistered fee recipient outright when a collection
 *    restricts them — verified live: every collection checked so far
 *    returns exactly one canonical recipient).
 *  - mintPrice is read fresh via getPublicDrop and the resulting total cost
 *    is checked against valueCapWei BEFORE building the call — a price
 *    change between when the permission was granted and now can only ever
 *    make this abort, never overspend.
 *  - The CallPolicy embedded in the permission itself (nftContract EQUAL,
 *    minterIfNotPayer EQUAL smartAccountAddress, quantity LESS_THAN_OR_EQUAL,
 *    valueLimit) is enforced ON-CHAIN by the permission validator regardless
 *    of anything this function does — this is defense in depth, not the
 *    only layer.
 *
 * Throws only for setup failures (bad approval, ZeroDev not configured).
 * A failed/reverted mint attempt is returned as {success: false, error},
 * not thrown, so callers can log it as a real (non-exceptional) outcome.
 */
export async function fireMint(params: FireMintParams): Promise<FireMintResult> {
  if (!ZERODEV_PROJECT_ID) {
    throw new Error("ZERODEV_PROJECT_ID is not configured — cannot fire a mint");
  }

  const sessionKeyAccount = await deserializePermissionAccount(
    publicClient,
    entryPoint,
    KERNEL_V3_1,
    params.serializedApproval
  );

  if (sessionKeyAccount.address.toLowerCase() !== params.smartAccountAddress.toLowerCase()) {
    throw new Error(
      "Deserialized approval does not resolve to the expected smart account address"
    );
  }

  const [allowedFeeRecipients, publicDrop] = await Promise.all([
    publicClient.readContract({
      address: SEADROP_ADDRESS,
      abi: SEADROP_ABI,
      functionName: "getAllowedFeeRecipients",
      args: [params.nftContract],
    }),
    publicClient.readContract({
      address: SEADROP_ADDRESS,
      abi: SEADROP_ABI,
      functionName: "getPublicDrop",
      args: [params.nftContract],
    }),
  ]);

  // Falls back to the smart account's own address (always non-zero, always
  // valid) only when the collection genuinely doesn't restrict fee
  // recipients — SeaDrop rejects the zero address unconditionally, so an
  // empty allowedFeeRecipients list can't mean "any address including zero".
  const feeRecipient: Address =
    allowedFeeRecipients.length > 0 ? allowedFeeRecipients[0] : params.smartAccountAddress;

  const totalCostWei = publicDrop.mintPrice * BigInt(params.quantity);
  if (totalCostWei > params.valueCapWei) {
    return {
      success: false,
      userOpHash: "",
      txHash: null,
      blockNumber: null,
      gasUsed: null,
      error: `Real mint price (${totalCostWei} wei total) exceeds the granted spend cap (${params.valueCapWei} wei) — aborted before submitting, nothing spent`,
    };
  }

  const kernelClient = createKernelAccountClient({
    account: sessionKeyAccount,
    chain: mainnet,
    bundlerTransport: http(ZERODEV_BUNDLER_URL),
    paymaster: true,
  });

  const callData = encodeFunctionData({
    abi: SEADROP_ABI,
    functionName: "mintPublic",
    args: [params.nftContract, feeRecipient, params.smartAccountAddress, BigInt(params.quantity)],
  });

  let userOpHash: string;
  try {
    userOpHash = await kernelClient.sendUserOperation({
      calls: [{ to: SEADROP_ADDRESS, value: totalCostWei, data: callData }],
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { success: false, userOpHash: "", txHash: null, blockNumber: null, gasUsed: null, error: msg };
  }

  try {
    const receipt = await kernelClient.waitForUserOperationReceipt({
      hash: userOpHash as `0x${string}`,
      timeout: USER_OP_RECEIPT_TIMEOUT_MS,
    });
    return {
      success: receipt.success,
      userOpHash,
      txHash: receipt.receipt.transactionHash,
      blockNumber: receipt.receipt.blockNumber.toString(),
      gasUsed: receipt.receipt.gasUsed.toString(),
      error: receipt.success ? undefined : "UserOperation included on-chain but reported failure",
    };
  } catch (err: unknown) {
    // The UserOp was submitted (we have a real hash) but we couldn't
    // confirm its outcome within the timeout — NOT the same as "it
    // failed". Callers must treat this as unknown/pending, not a hard
    // failure, and can look the hash up later.
    const msg = err instanceof Error ? err.message : String(err);
    return {
      success: false,
      ambiguous: true,
      userOpHash,
      txHash: null,
      blockNumber: null,
      gasUsed: null,
      error: `Submitted but receipt not confirmed within timeout: ${msg}`,
    };
  }
}
