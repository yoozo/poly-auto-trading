import { useSyncExternalStore } from "react";

export type EthereumProvider = {
  request<T = unknown>(args: { method: string; params?: unknown[] }): Promise<T>;
  on?: (event: string, listener: (...args: unknown[]) => void) => void;
  removeListener?: (event: string, listener: (...args: unknown[]) => void) => void;
};

declare global {
  interface Window {
    ethereum?: EthereumProvider;
  }
}

type WalletSnapshot = {
  address: string | null;
  manuallyDisconnected: boolean;
};

let snapshot: WalletSnapshot = {
  address: null,
  manuallyDisconnected: false,
};
const listeners = new Set<() => void>();
let providerEventsBound = false;

function emit() {
  for (const listener of listeners) listener();
}

function setSnapshot(next: Partial<WalletSnapshot>) {
  snapshot = { ...snapshot, ...next };
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  bindProviderEvents();
  void refreshWalletConnection();
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot() {
  return snapshot;
}

function bindProviderEvents() {
  if (providerEventsBound || !window.ethereum?.on) return;
  providerEventsBound = true;
  window.ethereum.on("accountsChanged", (accounts) => {
    const nextAddress = Array.isArray(accounts) && typeof accounts[0] === "string" ? accounts[0] : null;
    setSnapshot({
      address: nextAddress,
      manuallyDisconnected: nextAddress ? false : snapshot.manuallyDisconnected,
    });
  });
}

export function useWalletConnection() {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export async function refreshWalletConnection() {
  if (snapshot.manuallyDisconnected || !window.ethereum) return snapshot;
  const accounts = await window.ethereum.request<string[]>({ method: "eth_accounts" }).catch(() => []);
  const address = accounts[0] ?? null;
  if (address !== snapshot.address) {
    setSnapshot({ address, manuallyDisconnected: false });
  }
  return snapshot;
}

export async function connectWallet() {
  if (!window.ethereum) throw new Error("未检测到 MetaMask");
  const accounts = await window.ethereum.request<string[]>({ method: "eth_requestAccounts" });
  const address = accounts[0] ?? null;
  if (!address) throw new Error("MetaMask 未返回钱包地址");
  setSnapshot({ address, manuallyDisconnected: false });
  return address;
}

export async function disconnectWallet({ revoke = false }: { revoke?: boolean } = {}) {
  if (revoke && window.ethereum) {
    await window.ethereum
      .request({
        method: "wallet_revokePermissions",
        params: [{ eth_accounts: {} }],
      })
      .catch(() => undefined);
  }
  setSnapshot({ address: null, manuallyDisconnected: true });
}
