'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useAccount, useConfig, useDisconnect, useSignMessage, useSignTypedData, useSwitchChain } from 'wagmi';
import { getWalletClient, writeContract } from 'wagmi/actions';
import { useConnectModal } from '@rainbow-me/rainbowkit';
import { erc20Abi, getAddress, publicActions } from 'viem';
import { useEmileStore } from '@/store/useEmileStore';
import { buildSiweMessage, epcToWei, errorText, walletErrorText } from '@/lib/gf';
import { ROBINHOOD_CHAIN_ID } from '@/lib/gfChain';
import { ApiError, gfApi } from '@/lib/gfApi';
import type { OwnIdea, Problem } from '@/components/goforge/types';

export type Busy = 'login' | 'vote' | 'pay' | 'submit' | null;
export type ErrorKey = 'login' | 'vote' | 'pay' | 'submit';
export type Errors = Partial<Record<ErrorKey, string>>;

export interface SubmitResult {
  idea?: OwnIdea;
  problems?: Problem[];
  error?: string;
}

type Hex = `0x${string}`;

/** Everything the page does with a wallet: sign in, vote, pay the submit fee, submit. One place, one error message. */
export function useGfActions(refreshMe: () => Promise<void>) {
  const round = useEmileStore((s) => s.gfRound);
  const me = useEmileStore((s) => s.gfMe);
  const bumpGf = useEmileStore((s) => s.bumpGf);
  const [busy, setBusy] = useState<Busy>(null);
  const [errors, setErrors] = useState<Errors>({});
  const [votingFor, setVotingFor] = useState<string | null>(null);

  const config = useConfig();
  const { address, chainId, isConnected } = useAccount();
  const { openConnectModal } = useConnectModal();
  const { disconnectAsync } = useDisconnect();
  const { switchChainAsync } = useSwitchChain();
  const { signMessageAsync } = useSignMessage();
  const { signTypedDataAsync } = useSignTypedData();
  // Set when the user pressed Connect and the wallet picker is open: sign in as soon as a wallet is connected
  const pendingLogin = useRef(false);

  // An error belongs to the place the user acted: the wallet step, the vote cards or the form. Nothing floats at the top.
  const setError = useCallback((key: ErrorKey, message: string | null) => {
    setErrors((prev) => {
      const next = { ...prev };
      if (message) next[key] = message;
      else delete next[key];
      return next;
    });
  }, []);

  const fail = useCallback((key: ErrorKey, err: unknown) => {
    if (err instanceof ApiError) return setError(key, errorText(err.detail));
    const msg = walletErrorText(err);
    if (msg) setError(key, msg);
  }, [setError]);

  /** The wallet only signs for the chain it is on, so move it to Robinhood Chain first (RainbowKit adds it when missing). */
  const ensureChain = useCallback(async (target: number) => {
    if (chainId === target) return;
    await switchChainAsync({ chainId: target as typeof ROBINHOOD_CHAIN_ID });
  }, [chainId, switchChainAsync]);

  const signIn = useCallback(async (wallet: string) => {
    setError('login', null);
    setBusy('login');
    try {
      const n = await gfApi.nonce(wallet);
      await ensureChain(n.chain_id);
      const message = buildSiweMessage({
        domain: n.domain, address: n.address ?? getAddress(wallet), uri: n.uri, chainId: n.chain_id, nonce: n.nonce, issuedAt: n.issued_at, statement: n.statement,
      });
      const signature = await signMessageAsync({ message });
      useEmileStore.getState().setGfMe(await gfApi.siwe(message, signature));
    } catch (e) {
      fail('login', e);
    } finally {
      setBusy(null);
    }
  }, [ensureChain, fail, setError, signMessageAsync]);

  // Wallet picked in the RainbowKit modal: carry on to the sign-in message without a second click
  useEffect(() => {
    if (!pendingLogin.current || !isConnected || !address) return;
    pendingLogin.current = false;
    void signIn(address);
  }, [address, isConnected, signIn]);

  /** Connect (opens the RainbowKit picker when no wallet is connected yet), then sign in with Ethereum. */
  const login = useCallback(async () => {
    setError('login', null);
    if (!isConnected || !address) {
      pendingLogin.current = true;
      openConnectModal?.();
      return;
    }
    await signIn(address);
  }, [address, isConnected, openConnectModal, setError, signIn]);

  const logout = useCallback(async () => {
    try {
      await gfApi.logout();
    } finally {
      useEmileStore.getState().setGfMe({ wallet: null, x: null, is_team: false, my_vote: null, submitted_today: false });
      pendingLogin.current = false;
      try { await disconnectAsync(); } catch { /* already disconnected */ }
    }
  }, [disconnectAsync]);

  const vote = useCallback(async (ideaId: string) => {
    setError('vote', null);
    if (!isConnected || !address) {
      // The signed-in session survived a reload but the wallet did not: pick it again
      openConnectModal?.();
      return;
    }
    setBusy('vote');
    setVotingFor(ideaId);
    try {
      const typed = await gfApi.typedData(address.toLowerCase(), ideaId) as {
        types: Record<string, { name: string; type: string }[]>; primaryType: string;
        domain: { name: string; version: string; chainId: number | string };
        message: { round: string; ideaId: string; voter: string; signedAt: number };
      };
      // The vote message names the chain, and the wallet only signs it while connected to that chain
      await ensureChain(Number(typed.domain.chainId));
      // viem builds the EIP712Domain type itself, and wants uint256 as a bigint
      const { EIP712Domain: _domain, ...types } = typed.types;
      void _domain;
      const signature = await signTypedDataAsync({
        domain: { name: typed.domain.name, version: typed.domain.version, chainId: BigInt(typed.domain.chainId) },
        types, primaryType: 'Vote',
        message: { round: typed.message.round, ideaId: typed.message.ideaId, voter: typed.message.voter as Hex, signedAt: BigInt(typed.message.signedAt) },
      });
      await gfApi.vote({ voter: address.toLowerCase(), idea_id: ideaId, round: typed.message.round, signed_at: typed.message.signedAt, signature });
      await refreshMe();
      bumpGf();
    } catch (e) {
      fail('vote', e);
    } finally {
      setBusy(null);
      setVotingFor(null);
    }
  }, [address, bumpGf, ensureChain, fail, isConnected, openConnectModal, refreshMe, setError, signTypedDataAsync]);

  /** Burn the submit fee and wait until it is mined. Returns the tx hash, or null when it did not go through. */
  const payFee = useCallback(async (): Promise<string | null> => {
    const wallet = me?.wallet;
    if (!round || !wallet) return null;
    if (!isConnected || !address) {
      openConnectModal?.();
      return null;
    }
    setError('pay', null);
    setBusy('pay');
    try {
      const target = round.rules.chain_id;
      await ensureChain(target);
      // A client made after the switch, so it is bound to Robinhood Chain
      const client = await getWalletClient(config, { chainId: target as typeof ROBINHOOD_CHAIN_ID });
      const hash = await writeContract(config, {
        address: round.rules.epc_token as Hex, abi: erc20Abi, functionName: 'transfer',
        args: [round.rules.burn_address as Hex, epcToWei(round.rules.submit_fee_epc)],
        chainId: target as typeof ROBINHOOD_CHAIN_ID,
      });
      // Read the receipt through the wallet's own connection: no browser call to the public RPC
      const receipt = await client.extend(publicActions).waitForTransactionReceipt({ hash, timeout: 90_000 }).catch(() => null);
      if (!receipt || receipt.status !== 'success') {
        setError('pay', 'The fee transaction has not been confirmed yet. Wait a moment, then paste its hash and submit.');
      }
      return hash;
    } catch (e) {
      fail('pay', e);
      return null;
    } finally {
      setBusy(null);
    }
  }, [address, config, ensureChain, fail, isConnected, me, openConnectModal, round, setError]);

  const submit = useCallback(async (form: FormData): Promise<SubmitResult> => {
    setError('submit', null);
    setBusy('submit');
    try {
      const idea = await gfApi.submit(form);
      await refreshMe();
      bumpGf();
      return { idea };
    } catch (e) {
      if (e instanceof ApiError) {
        return { problems: e.detail.problems, error: e.detail.problems?.length ? undefined : errorText(e.detail) };
      }
      return { error: walletErrorText(e) ?? 'The submission failed.' };
    } finally {
      setBusy(null);
    }
  }, [bumpGf, refreshMe, setError]);

  return { busy, errors, setError, votingFor, login, logout, vote, payFee, submit, connected: isConnected, address };
}
