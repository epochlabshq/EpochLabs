'use client';

import React, { useState } from 'react';
import '@rainbow-me/rainbowkit/styles.css';
import { connectorsForWallets, darkTheme, getDefaultConfig, RainbowKitProvider } from '@rainbow-me/rainbowkit';
import { coinbaseWallet, injectedWallet } from '@rainbow-me/rainbowkit/wallets';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createConfig, http, WagmiProvider } from 'wagmi';
import { robinhoodChain, WALLETCONNECT_PROJECT_ID } from '@/lib/gfChain';

const APP_NAME = 'GoForge by Epoch Labs';

function makeConfig() {
  // With a WalletConnect project id: the full RainbowKit wallet list (Rainbow, MetaMask, Coinbase, WalletConnect QR...).
  if (WALLETCONNECT_PROJECT_ID) {
    return getDefaultConfig({ appName: APP_NAME, projectId: WALLETCONNECT_PROJECT_ID, chains: [robinhoodChain], ssr: true });
  }
  // Without one: browser wallets only (every installed extension is listed through EIP-6963), plus Coinbase Wallet.
  const connectors = connectorsForWallets(
    [{ groupName: 'Wallets', wallets: [injectedWallet, coinbaseWallet] }],
    { appName: APP_NAME, projectId: 'browser-wallets-only' },
  );
  return createConfig({ chains: [robinhoodChain], connectors, transports: { [robinhoodChain.id]: http() }, ssr: true });
}

// The dark amber of the site, so the connect modal looks like part of the page
const theme = darkTheme({ accentColor: '#E99F30', accentColorForeground: '#2A1C0D', borderRadius: 'medium', fontStack: 'system' });

/** Wallet connection for /goforge only (RainbowKit + wagmi + viem), so no other page pays for it. */
export const Web3Provider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [config] = useState(makeConfig);
  const [queryClient] = useState(() => new QueryClient());
  return (
    <WagmiProvider config={config}>
      <QueryClientProvider client={queryClient}>
        <RainbowKitProvider theme={theme} modalSize="compact" appInfo={{ appName: APP_NAME }}>
          {children}
        </RainbowKitProvider>
      </QueryClientProvider>
    </WagmiProvider>
  );
};
