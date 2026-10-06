// Run with: node --test src/lib/gf.test.ts   (Node 22+, runs TypeScript directly)
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  buildSiweMessage, epcToWei, erc20TransferData, errorText, isTxHash, normalizeTicker, secondsUntil, validateIdea,
  validateImageMeta, walletErrorText, xProfileUrl,
} from './gf.ts';

const BURN = '0x000000000000000000000000000000000000dEaD';

describe('SIWE message', () => {
  it('matches the EIP-4361 text the backend parses', () => {
    const msg = buildSiweMessage({
      domain: 'epochlabs.run', address: '0x1111111111111111111111111111111111111111', uri: 'https://epochlabs.run/goforge', chainId: 4663,
      nonce: 'abcdef1234567890', issuedAt: '2026-10-06T14:00:00.000Z', statement: 'Sign in to GoForge.',
    });
    assert.equal(msg, [
      'epochlabs.run wants you to sign in with your Ethereum account:',
      '0x1111111111111111111111111111111111111111',
      '',
      'Sign in to GoForge.',
      '',
      'URI: https://epochlabs.run/goforge',
      'Version: 1',
      'Chain ID: 4663',
      'Nonce: abcdef1234567890',
      'Issued At: 2026-10-06T14:00:00.000Z',
    ].join('\n'));
  });
});

describe('EPC fee transfer', () => {
  it('converts whole EPC to wei without float error', () => {
    assert.equal(epcToWei(1000), 1000n * 10n ** 18n);
    assert.equal(epcToWei(0.1), 10n ** 17n);
    assert.equal(epcToWei(1234.5678, 6), 1234567800n);
    assert.equal(epcToWei(0), 0n);
  });

  it('encodes transfer(address,uint256) to the burn address', () => {
    const data = erc20TransferData(BURN, epcToWei(1000));
    assert.equal(data.length, 2 + 8 + 64 + 64);
    assert.ok(data.startsWith('0xa9059cbb'));
    assert.equal(data.slice(10, 74), '000000000000000000000000000000000000000000000000000000000000dead'.padStart(64, '0'));
    assert.equal(BigInt('0x' + data.slice(74)), 1000n * 10n ** 18n);
  });

  it('refuses a malformed recipient', () => {
    assert.throws(() => erc20TransferData('0x123', 1n));
  });

  it('recognises a tx hash', () => {
    assert.ok(isTxHash('0x' + 'ab'.repeat(32)));
    assert.ok(isTxHash('  0x' + 'AB'.repeat(32) + ' '));
    assert.ok(!isTxHash('0x123'));
    assert.ok(!isTxHash(''));
  });
});

describe('idea form', () => {
  const ok = { name: 'Spore Keeper', ticker: 'SPORE', lore: 'x'.repeat(60) };

  it('accepts a valid idea', () => assert.deepEqual(validateIdea(ok), {}));

  it('flags every bound', () => {
    assert.ok(validateIdea({ ...ok, name: 'A' }).name);
    assert.ok(validateIdea({ ...ok, name: 'x'.repeat(33) }).name);
    assert.equal(validateIdea({ ...ok, name: 'Ab' }).name, undefined);
    assert.ok(validateIdea({ ...ok, ticker: 'A' }).ticker);
    assert.ok(validateIdea({ ...ok, ticker: 'ABCDEFGHIJK' }).ticker);
    assert.ok(validateIdea({ ...ok, lore: 'x'.repeat(49) }).lore);
    assert.ok(validateIdea({ ...ok, lore: 'x'.repeat(1001) }).lore);
    assert.equal(validateIdea({ ...ok, lore: 'x'.repeat(50) }).lore, undefined);
    assert.equal(validateIdea({ ...ok, lore: 'x'.repeat(1000) }).lore, undefined);
  });

  it('counts the trimmed lore and name', () => {
    assert.ok(validateIdea({ ...ok, lore: ' '.repeat(40) + 'x'.repeat(30) }).lore);
    assert.ok(validateIdea({ ...ok, name: ' A ' }).name);
  });

  it('upper-cases the ticker and drops symbols, including a dollar sign', () => {
    assert.equal(normalizeTicker('$spore!'), 'SPORE');
    assert.equal(normalizeTicker('a-b c1'), 'ABC1');
    assert.equal(normalizeTicker('abcdefghijklmnop'), 'ABCDEFGHIJ');
  });
});

describe('image rules', () => {
  const good = { type: 'image/png', size: 1000, width: 512, height: 512 };

  it('accepts a square 512 PNG, JPG and WebP', () => {
    for (const type of ['image/png', 'image/jpeg', 'image/webp']) assert.equal(validateImageMeta({ ...good, type }), null);
  });

  it('rejects the wrong format, size, ratio and resolution', () => {
    assert.match(validateImageMeta({ ...good, type: 'image/gif' })!, /PNG, JPG or WebP/);
    assert.match(validateImageMeta({ ...good, size: 2 * 1024 * 1024 + 1 })!, /2 MB/);
    assert.match(validateImageMeta({ ...good, width: 600 })!, /square/);
    assert.match(validateImageMeta({ ...good, width: 511, height: 511 })!, /512x512/);
    assert.equal(validateImageMeta({ ...good, size: 2 * 1024 * 1024 }), null);
  });
});

describe('countdown and errors', () => {
  it('never goes negative', () => {
    const now = Date.parse('2026-10-06T12:00:00Z');
    assert.equal(secondsUntil('2026-10-06T12:00:10Z', now), 10);
    assert.equal(secondsUntil('2026-10-06T11:00:00Z', now), 0);
  });

  it('prefers the server message and falls back to a known code', () => {
    assert.equal(errorText({ code: 'slots_full', message: 'All 100 slots for today are taken.' }), 'All 100 slots for today are taken.');
    assert.equal(errorText({ code: 'slots_full' }), 'All slots for today are taken.');
    assert.equal(errorText({ code: 'nope' }), 'Something went wrong. Try again.');
    assert.equal(errorText(undefined, 'custom'), 'custom');
  });

  it('treats a closed wallet prompt as silence, not an error', () => {
    assert.equal(walletErrorText({ code: 4001 }), null);
    assert.match(walletErrorText({ code: -32002 })!, /already open/);
    assert.equal(walletErrorText({ message: 'x'.repeat(500) })!.length, 160);
    assert.equal(walletErrorText(undefined), 'The wallet request failed.');
  });

  it('builds a profile link from a handle', () => {
    assert.equal(xProfileUrl('sporefan'), 'https://x.com/sporefan');
    assert.equal(xProfileUrl('@sporefan'), 'https://x.com/sporefan');
    assert.equal(xProfileUrl(null), null);
  });
});
