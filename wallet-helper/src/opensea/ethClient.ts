/**
 * Ethereum-mainnet plumbing for the OpenSea Auto-Mint tool. Separate chain,
 * separate stack from the rest of wallet-helper (which is Abstract-chain/
 * AGW-only) — deliberately kept in its own module so the two never get
 * confused.
 *
 * This project originally fired mints through ERC-4337 smart accounts
 * (ZeroDev) for on-chain-enforced spend limits. Replaced with direct,
 * plain-EOA signed transactions for raw firing speed — an explicit,
 * informed tradeoff: no bundler hop, but also no on-chain CallPolicy
 * backstop. fireMint at the bottom is the one function in this file that
 * spends real ETH; everything above it is read-only or signature-
 * verification-only.
 */
import {
  http,
  type Address,
  createPublicClient,
  createWalletClient,
  encodeFunctionData,
  verifyMessage,
} from "viem";
import { privateKeyToAccount } from "viem/accounts";
import { mainnet } from "viem/chains";

// eth.llamarpc.com (the original default here) had a real, hours-long
// outage in production (Cloudflare 521) — publicnode has been reliable
// across every test run during this feature's development. Still just a
// free public RPC; RPC_ETHEREUM_MAINNET overrides it with a paid/low-
// latency provider whenever that's set up. For a speed-critical firing
// path like this one, a dedicated low-latency RPC matters — a free shared
// endpoint's response time is itself part of the race.
const ETH_RPC =
  process.env.RPC_ETHEREUM_MAINNET ?? "https://ethereum-rpc.publicnode.com";

// Stateless config — one client is reused across calls rather than
// reconstructed per request.
const publicClient = createPublicClient({ chain: mainnet, transport: http(ETH_RPC) });

/**
 * Verifies a plain EOA signature over an arbitrary message actually
 * recovers to the claimed owner address — used to authorize grant/arm/
 * cancel actions (opensea_automint/routes.py), which otherwise would
 * accept a bare, self-reported ownerAddress with no proof of control
 * (Ethereum addresses are public; anyone could otherwise act on someone
 * else's behalf).
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

/**
 * Confirms a private key actually corresponds to the address a grant
 * request claims for it — closes the gap where a client could send a
 * validly-signed grant authorization for a session address they claim,
 * alongside a DIFFERENT private key entirely (accidentally or otherwise),
 * which would silently store a key that can never actually mint as the
 * address the owner thinks they authorized.
 */
export function verifySessionKeyMatchesAddress(
  sessionPrivateKey: `0x${string}`,
  claimedSessionAddress: Address
): boolean {
  try {
    const derived = privateKeyToAccount(sessionPrivateKey).address;
    return derived.toLowerCase() === claimedSessionAddress.toLowerCase();
  } catch {
    return false;
  }
}

// Same canonical address used in the browser-side transaction construction
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
  // Verified against ProjectOpenSea/seadrop's canonical ISeaDrop.sol +
  // SeaDropStructs.sol on GitHub (2026-08-09) — MintParams field order
  // matters for correct ABI encoding and must match the struct exactly.
  {
    name: "mintSigned",
    type: "function",
    stateMutability: "payable",
    inputs: [
      { name: "nftContract", type: "address" },
      { name: "feeRecipient", type: "address" },
      { name: "minterIfNotPayer", type: "address" },
      { name: "quantity", type: "uint256" },
      {
        name: "mintParams",
        type: "tuple",
        components: [
          { name: "mintPrice", type: "uint256" },
          { name: "maxTotalMintableByWallet", type: "uint256" },
          { name: "startTime", type: "uint256" },
          { name: "endTime", type: "uint256" },
          { name: "dropStageIndex", type: "uint256" },
          { name: "maxTokenSupplyForStage", type: "uint256" },
          { name: "feeBps", type: "uint256" },
          { name: "restrictFeeRecipients", type: "bool" },
        ],
      },
      { name: "salt", type: "uint256" },
      { name: "signature", type: "bytes" },
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

const RECEIPT_TIMEOUT_MS = 60_000;

// How aggressively to bid over the network's own suggested fee — this is a
// competitive race against other bots, so under-pricing gas loses
// regardless of how fast we submit. Multiplies BOTH maxFeePerGas and
// maxPriorityFeePerGas uniformly (keeps maxFee >= priorityFee, which must
// always hold). Overridable via env for tuning without a redeploy of logic.
const GAS_PRIORITY_MULTIPLIER = BigInt(process.env.GAS_PRIORITY_MULTIPLIER ?? "2");

// A generous buffer over whatever estimateGas reports, so a slightly-off
// estimate (state can shift between estimation and inclusion) doesn't cause
// an otherwise-valid transaction to run out of gas.
const GAS_ESTIMATE_BUFFER_PERCENT = 20n;

export interface PublicDropWindow {
  startTime: number;
  endTime: number;
  mintPriceWei: string;
}

/**
 * Read-only, no-wallet-required lookup of a collection's real on-chain
 * public mint window — used by the firing watcher to know precisely when a
 * drop actually goes live/ends, rather than relying on scraped page text.
 * Verified live against real collections before being relied on anywhere.
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

// Emitted by SeaDrop itself every time ANY collection (re)configures its
// public mint stage — the real source of truth for "which collections are
// using SeaDrop for a public mint," independent of whatever OpenSea's own
// /drops listing page happens to curate/feature. Verified against
// ProjectOpenSea/seadrop's SeaDropErrorsAndEvents.sol.
const PUBLIC_DROP_UPDATED_EVENT = {
  type: "event",
  name: "PublicDropUpdated",
  inputs: [
    { name: "nftContract", type: "address", indexed: true },
    {
      name: "publicDrop",
      type: "tuple",
      indexed: false,
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
} as const;

// Verified live 2026-08-10 against the real SeaDrop contract on this
// project's configured RPC: eth_getLogs reliably succeeded for ranges up
// to ~100 blocks behind the current tip, started failing around ~130
// blocks behind, and failed consistently ~2000 blocks behind ("Archive
// requests require a personal token" — a real error from this specific
// free-tier RPC). Not a documented hard limit, just observed behavior of
// a free, load-balanced RPC's backend nodes — kept well under the
// confirmed-working distance-from-tip, not just chunk size, since an
// older chunk near the START of a large catch-up range is what actually
// fails first.
const GET_LOGS_CHUNK_BLOCKS = 60n;

// Only used when no prior scan position is known (first run, or a
// self-healing reset — see drops.py's discover_new_seadrop_collections).
// Deliberately small and inside the confirmed-working near-tip range
// (see GET_LOGS_CHUNK_BLOCKS) — this is NOT sized to backfill much
// history; the goal is catching new/upcoming drops going forward at the
// 3-minute poll cadence (agent.py), not indexing every collection that's
// ever used SeaDrop.
const INITIAL_LOOKBACK_BLOCKS = 60n;

export interface PublicDropUpdate {
  nftContract: Address;
  startTime: number;
  endTime: number;
  mintPriceWei: string;
}

export interface RecentPublicDropUpdatesResult {
  updates: PublicDropUpdate[];
  scannedToBlock: string;
}

/**
 * Discovers every collection that has (re)configured its SeaDrop public
 * mint stage since fromBlock (or, when fromBlock is null, since
 * INITIAL_LOOKBACK_BLOCKS before the current tip — see above). Chunks the
 * block range to stay within what this RPC has actually demonstrated it
 * can handle reliably, and stops early — returning whatever it found so
 * far, plus the last block it successfully scanned — the moment one chunk
 * fails, rather than losing all progress to one bad request. Callers
 * persist scannedToBlock and resume from there on the next call; nothing
 * is ever silently skipped or re-scanned.
 */
export async function getRecentPublicDropUpdates(
  fromBlock: bigint | null
): Promise<RecentPublicDropUpdatesResult> {
  const latestBlock = await publicClient.getBlockNumber();
  const start =
    fromBlock !== null
      ? fromBlock
      : latestBlock > INITIAL_LOOKBACK_BLOCKS
        ? latestBlock - INITIAL_LOOKBACK_BLOCKS
        : 0n;

  const updates: PublicDropUpdate[] = [];
  let cursor = start;
  let scannedToBlock = start > 0n ? start - 1n : 0n;

  while (cursor <= latestBlock) {
    const chunkEnd =
      cursor + GET_LOGS_CHUNK_BLOCKS > latestBlock ? latestBlock : cursor + GET_LOGS_CHUNK_BLOCKS;
    try {
      const logs = await publicClient.getLogs({
        address: SEADROP_ADDRESS,
        event: PUBLIC_DROP_UPDATED_EVENT,
        fromBlock: cursor,
        toBlock: chunkEnd,
      });
      for (const log of logs) {
        if (!log.args.nftContract || !log.args.publicDrop) continue;
        updates.push({
          nftContract: log.args.nftContract,
          startTime: Number(log.args.publicDrop.startTime),
          endTime: Number(log.args.publicDrop.endTime),
          mintPriceWei: log.args.publicDrop.mintPrice.toString(),
        });
      }
      scannedToBlock = chunkEnd;
      cursor = chunkEnd + 1n;
    } catch {
      // Stop here with whatever succeeded so far — the next call resumes
      // right after the last successfully-scanned block.
      break;
    }
  }

  return { updates, scannedToBlock: scannedToBlock.toString() };
}

export interface FireMintParams {
  sessionPrivateKey: `0x${string}`;
  nftContract: Address;
  quantity: number;
  valueCapWei: bigint;
}

export interface FireMintResult {
  success: boolean;
  txHash: string | null;
  blockNumber: string | null;
  gasUsed: string | null;
  error?: string;
  // True ONLY for the "submitted but couldn't confirm receipt within the
  // timeout" case — a real transaction may or may not have landed on-chain.
  // Deliberately distinct from a confirmed on-chain failure: callers must
  // never treat an ambiguous outcome as safe to retry, since the first
  // attempt might still land and a second submission would risk a genuine
  // duplicate spend/mint.
  ambiguous?: boolean;
}

/**
 * Submits a REAL, directly-signed mintPublic() transaction from a session
 * key's own EOA. This is the one function in this codebase that spends
 * real ETH — everything it does is re-verified against the live chain
 * immediately before signing, never trusted from caller-supplied values:
 *
 *  - feeRecipient is looked up fresh via getAllowedFeeRecipients.
 *  - mintPrice is read fresh via getPublicDrop and the resulting total cost
 *    is checked against valueCapWei BEFORE building the call.
 *  - Gas is ESTIMATED (a real simulation call) before ever sending. This is
 *    NOT optional the way it effectively was under the old ERC-4337 design
 *    (a bundler simulates and silently refuses to broadcast a would-revert
 *    UserOperation, so nothing was ever spent on a doomed attempt) — a raw
 *    transaction has no such built-in protection. If estimateGas fails
 *    (the call would revert — sold out, quantity exhausted, wrong price),
 *    this returns a definite failure WITHOUT ever calling sendTransaction,
 *    so a certain-to-fail mint never wastes real gas.
 *
 * There is NO on-chain enforcement of quantity/price/target-contract
 * limits in this model (that was the ERC-4337 CallPolicy's job) — the
 * checks in this function and in opensea_automint/firing.py ARE the only
 * safety net.
 *
 * Throws only for setup failures (bad private key). A failed/reverted/
 * would-revert mint attempt is returned as {success: false, error}, not
 * thrown, so callers can log it as a real (non-exceptional) outcome.
 */
/**
 * Shared tail end of both fireMint and fireSignedMint: estimate gas (the
 * one and only pre-flight safety check — see fireMint's docstring for why
 * this replaces the ERC-4337 bundler's simulation), sign, broadcast, and
 * wait for a receipt. Everything before this point (reading fee
 * recipients/price, building callData, checking the spend cap) differs by
 * mint path; everything from here on is identical.
 */
async function estimateSignAndSend(
  account: ReturnType<typeof privateKeyToAccount>,
  callData: `0x${string}`,
  valueWei: bigint,
  nonce: number,
  maxFeePerGas: bigint,
  maxPriorityFeePerGas: bigint
): Promise<FireMintResult> {
  let gas: bigint;
  try {
    const estimated = await publicClient.estimateGas({
      account: account.address,
      to: SEADROP_ADDRESS,
      data: callData,
      value: valueWei,
    });
    gas = (estimated * (100n + GAS_ESTIMATE_BUFFER_PERCENT)) / 100n;
  } catch (err: unknown) {
    // The call would revert — sold out, quantity limit hit, price changed
    // underneath us, drop genuinely isn't open yet, or (for a signed mint)
    // the signature/salt/mintParams don't match what the signer actually
    // authorized. Nothing was ever sent; this is a safe, definite
    // (non-ambiguous) failure.
    const msg = err instanceof Error ? err.message : String(err);
    return {
      success: false,
      txHash: null,
      blockNumber: null,
      gasUsed: null,
      error: `Gas estimation failed (would revert), nothing sent: ${msg}`,
    };
  }

  const walletClient = createWalletClient({ account, chain: mainnet, transport: http(ETH_RPC) });

  let txHash: `0x${string}`;
  try {
    txHash = await walletClient.sendTransaction({
      to: SEADROP_ADDRESS,
      data: callData,
      value: valueWei,
      nonce,
      gas,
      maxFeePerGas,
      maxPriorityFeePerGas,
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { success: false, txHash: null, blockNumber: null, gasUsed: null, error: msg };
  }

  try {
    const receipt = await publicClient.waitForTransactionReceipt({
      hash: txHash,
      timeout: RECEIPT_TIMEOUT_MS,
    });
    return {
      success: receipt.status === "success",
      txHash,
      blockNumber: receipt.blockNumber.toString(),
      gasUsed: receipt.gasUsed.toString(),
      error: receipt.status === "success" ? undefined : "Transaction included on-chain but reverted",
    };
  } catch (err: unknown) {
    // The transaction was broadcast (we have a real hash) but we couldn't
    // confirm its outcome within the timeout — NOT the same as "it
    // failed". Callers must treat this as unknown/pending, not a hard
    // failure, and can look the hash up later.
    const msg = err instanceof Error ? err.message : String(err);
    return {
      success: false,
      ambiguous: true,
      txHash,
      blockNumber: null,
      gasUsed: null,
      error: `Submitted but receipt not confirmed within timeout: ${msg}`,
    };
  }
}

export async function fireMint(params: FireMintParams): Promise<FireMintResult> {
  const account = privateKeyToAccount(params.sessionPrivateKey);

  const [allowedFeeRecipients, publicDrop, nonce, feesPerGas] = await Promise.all([
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
    publicClient.getTransactionCount({ address: account.address, blockTag: "pending" }),
    publicClient.estimateFeesPerGas(),
  ]);

  // Falls back to the session key's own address (always non-zero, always
  // valid) only when the collection genuinely doesn't restrict fee
  // recipients — SeaDrop rejects the zero address unconditionally, so an
  // empty allowedFeeRecipients list can't mean "any address including zero".
  const feeRecipient: Address =
    allowedFeeRecipients.length > 0 ? allowedFeeRecipients[0] : account.address;

  const totalCostWei = publicDrop.mintPrice * BigInt(params.quantity);
  if (totalCostWei > params.valueCapWei) {
    return {
      success: false,
      txHash: null,
      blockNumber: null,
      gasUsed: null,
      error: `Real mint price (${totalCostWei} wei total) exceeds the granted spend cap (${params.valueCapWei} wei) — aborted before submitting, nothing spent`,
    };
  }

  const callData = encodeFunctionData({
    abi: SEADROP_ABI,
    functionName: "mintPublic",
    args: [params.nftContract, feeRecipient, account.address, BigInt(params.quantity)],
  });

  return estimateSignAndSend(
    account, callData, totalCostWei, nonce,
    feesPerGas.maxFeePerGas * GAS_PRIORITY_MULTIPLIER,
    feesPerGas.maxPriorityFeePerGas * GAS_PRIORITY_MULTIPLIER
  );
}

// The 8 MintParams fields exactly as OpenSea's backend signed them — these
// come from wherever the signature itself came from (opensea_session.py's
// authenticated GraphQL replay), NOT reconstructed here. Any mismatch
// (even a single field, even field ORDER) makes ecrecover on-chain resolve
// to the wrong address and the mint revert — see SeaDropStructs.sol's
// MintParams, verified against ProjectOpenSea/seadrop on GitHub.
export interface SignedMintParamsInput {
  mintPrice: bigint;
  maxTotalMintableByWallet: bigint;
  startTime: bigint;
  endTime: bigint;
  dropStageIndex: bigint;
  maxTokenSupplyForStage: bigint;
  feeBps: bigint;
  restrictFeeRecipients: boolean;
}

export interface FireSignedMintParams {
  sessionPrivateKey: `0x${string}`;
  nftContract: Address;
  quantity: number;
  valueCapWei: bigint;
  mintParams: SignedMintParamsInput;
  salt: bigint;
  signature: `0x${string}`;
}

/**
 * Submits a REAL, directly-signed mintSigned() transaction — the allowlist/
 * presale counterpart to fireMint's mintPublic(). Same safety properties
 * (price-cap check before ever building the call, gas-estimation as the
 * pre-flight revert check, no on-chain enforcement beyond what this
 * function and firing.py check) — the only structural difference is that
 * mintParams/salt/signature are caller-supplied (fetched fresh from
 * OpenSea's own backend right before this is called, per
 * opensea_session.py), not read from an on-chain getter the way
 * getPublicDrop is. A stale or mismatched signature simply fails gas
 * estimation like any other would-revert call — nothing is spent chasing
 * bad signed-mint data.
 *
 * Per SeaDrop's own interface docs: "a signature can only be used once" —
 * once consumed by ANY successful mintSigned call (this one or otherwise),
 * retrying with the same signature will fail gas estimation and return a
 * safe, definite failure, not an ambiguous one.
 */
export async function fireSignedMint(params: FireSignedMintParams): Promise<FireMintResult> {
  const account = privateKeyToAccount(params.sessionPrivateKey);

  const [allowedFeeRecipients, nonce, feesPerGas] = await Promise.all([
    publicClient.readContract({
      address: SEADROP_ADDRESS,
      abi: SEADROP_ABI,
      functionName: "getAllowedFeeRecipients",
      args: [params.nftContract],
    }),
    publicClient.getTransactionCount({ address: account.address, blockTag: "pending" }),
    publicClient.estimateFeesPerGas(),
  ]);

  const feeRecipient: Address =
    allowedFeeRecipients.length > 0 ? allowedFeeRecipients[0] : account.address;

  const totalCostWei = params.mintParams.mintPrice * BigInt(params.quantity);
  if (totalCostWei > params.valueCapWei) {
    return {
      success: false,
      txHash: null,
      blockNumber: null,
      gasUsed: null,
      error: `Real mint price (${totalCostWei} wei total) exceeds the granted spend cap (${params.valueCapWei} wei) — aborted before submitting, nothing spent`,
    };
  }

  const callData = encodeFunctionData({
    abi: SEADROP_ABI,
    functionName: "mintSigned",
    args: [
      params.nftContract,
      feeRecipient,
      account.address,
      BigInt(params.quantity),
      {
        mintPrice: params.mintParams.mintPrice,
        maxTotalMintableByWallet: params.mintParams.maxTotalMintableByWallet,
        startTime: params.mintParams.startTime,
        endTime: params.mintParams.endTime,
        dropStageIndex: params.mintParams.dropStageIndex,
        maxTokenSupplyForStage: params.mintParams.maxTokenSupplyForStage,
        feeBps: params.mintParams.feeBps,
        restrictFeeRecipients: params.mintParams.restrictFeeRecipients,
      },
      params.salt,
      params.signature,
    ],
  });

  return estimateSignAndSend(
    account, callData, totalCostWei, nonce,
    feesPerGas.maxFeePerGas * GAS_PRIORITY_MULTIPLIER,
    feesPerGas.maxPriorityFeePerGas * GAS_PRIORITY_MULTIPLIER
  );
}
