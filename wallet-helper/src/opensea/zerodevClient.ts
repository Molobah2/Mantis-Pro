/**
 * Ethereum-mainnet ZeroDev smart-account plumbing for the OpenSea Auto-Mint
 * tool. Separate chain, separate account-abstraction stack from the rest of
 * wallet-helper (which is Abstract-chain/AGW-only) — deliberately kept in
 * its own module so the two never get confused.
 *
 * This file is READ-ONLY / non-firing: it derives a counterfactual smart
 * account address from an owner's EOA address alone. It never touches a
 * private key and never submits a transaction — session-key signing and
 * mint-firing are later, separate steps.
 */
import { http, type Address, createPublicClient } from "viem";
import { mainnet } from "viem/chains";
import { signerToEcdsaValidator } from "@zerodev/ecdsa-validator";
import { createKernelAccount, addressToEmptyAccount } from "@zerodev/sdk";
import { getEntryPoint, KERNEL_V3_1 } from "@zerodev/sdk/constants";

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
