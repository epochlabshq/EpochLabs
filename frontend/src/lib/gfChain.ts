// Robinhood Chain for wagmi / viem. The chain id and the burn / EPC addresses still come from /api/goforge/round, so a
// change on the backend is never fought by the page: only the chain definition (RPC, explorer) lives here.
import { defineChain } from 'viem';

export const ROBINHOOD_CHAIN_ID = 4663;

export const robinhoodChain = defineChain({
  id: ROBINHOOD_CHAIN_ID,
  name: 'Robinhood Chain',
  nativeCurrency: { name: 'Ether', symbol: 'ETH', decimals: 18 },
  rpcUrls: { default: { http: ['https://rpc.mainnet.chain.robinhood.com'] } },
  blockExplorers: { default: { name: 'Blockscout', url: 'https://robinhoodchain.blockscout.com' } },
});

/** WalletConnect (QR and mobile wallets) needs a project id from cloud.reown.com. Without one, installed browser wallets still work. */
export const WALLETCONNECT_PROJECT_ID = process.env.NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID ?? '';
