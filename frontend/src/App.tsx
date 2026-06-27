import {
  AlertOutlined,
  BarChartOutlined,
  BellOutlined,
  CloudDownloadOutlined,
  LoginOutlined,
  LineChartOutlined,
  LogoutOutlined,
  MoonOutlined,
  ReloadOutlined,
  SettingOutlined
} from "@ant-design/icons";
import { PageContainer, ProLayout } from "@ant-design/pro-components";
import { Alert, Button, ConfigProvider, Form, Input, Popover, Spin, Typography, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  setUnauthorizedHandler,
  type PolymarketAccountState,
  type PolymarketAccountStateWsMessage,
  type PolymarketCredentialProfile
} from "./api/client";
import { PerformanceMonitorTooltip } from "./components/PerformanceMonitorTooltip";
import { PolymarketCredentialManager } from "./components/PolymarketCredentialManager";
import { connectWallet, disconnectWallet, useWalletConnection } from "./hooks/useWalletConnection";

const BTCWatchPage = lazy(() => import("./pages/BTCWatchPage"));
const MarketDetailPage = lazy(() => import("./pages/MarketDetailPage"));
const ReportsPage = lazy(() => import("./pages/ReportsPage"));
const SignalsPage = lazy(() => import("./pages/SignalsPage"));
const SystemStatusPage = lazy(() => import("./pages/SystemStatusPage"));
const SystemTasksPage = lazy(() => import("./pages/SystemTasksPage"));
const TelegramNotificationsPage = lazy(() => import("./pages/TelegramNotificationsPage"));

type RouteKey = "/btc-watch" | "/signals" | "/reports" | "/reports/market-detail" | "/telegram" | "/system-tasks" | "/settings";
type ThemeMode = "light" | "dark";
type AuthStatus = "checking" | "authenticated" | "anonymous";

const THEME_MODE_KEY = "poly-auto.themeMode";
const SIDER_COLLAPSED_KEY = "poly-auto.siderCollapsed";
const EMPTY_ACCOUNT_STATE: PolymarketAccountState = {
  wallet: null,
  clob_address: null,
  balance: null,
  trading_restriction: null,
  condition_id: null,
  positions: [],
  orders: [],
  recent_trades: [],
  ws_state: "idle",
  last_positions_refresh_at: null,
  last_orders_refresh_at: null,
  last_trade_at: null,
  error: null,
};

const route = {
  path: "/",
  routes: [
    { path: "/btc-watch", name: "BTC 看盘", icon: <LineChartOutlined /> },
    { path: "/signals", name: "信号", icon: <AlertOutlined /> },
    { path: "/telegram", name: "Telegram 提醒", icon: <BellOutlined /> },
    { path: "/system-tasks", name: "系统任务", icon: <CloudDownloadOutlined /> },
    { path: "/reports", name: "收益报表", icon: <BarChartOutlined /> },
    { path: "/settings", name: "系统配置", icon: <SettingOutlined /> }
  ]
};

function renderPage(
  pathname: RouteKey,
  searchParams: URLSearchParams,
  navigate: (pathname: RouteKey, search?: string) => void,
) {
  if (pathname === "/btc-watch") return <BTCWatchPage />;
  if (pathname === "/signals") return <SignalsPage />;
  if (pathname === "/reports") {
    return (
      <ReportsPage
        onOpenMarketDetail={(accountId, marketId) =>
          navigate("/reports/market-detail", `?account=${encodeURIComponent(accountId)}&market=${encodeURIComponent(marketId)}`)
        }
      />
    );
  }
  if (pathname === "/reports/market-detail") {
    return (
      <MarketDetailPage
        accountId={searchParams.get("account")}
        marketId={searchParams.get("market")}
        onBack={() => navigate("/reports")}
      />
    );
  }
  if (pathname === "/telegram") return <TelegramNotificationsPage />;
  if (pathname === "/system-tasks") return <SystemTasksPage />;
  if (pathname === "/settings") return <SystemStatusPage />;
  return <BTCWatchPage />;
}

export default function App() {
  const queryClient = useQueryClient();
  const [locationState, setLocationState] = useState(() => readLocationState());
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => readThemeMode());
  const [siderCollapsed, setSiderCollapsed] = useState(() => readSiderCollapsed());
  const [authStatus, setAuthStatus] = useState<AuthStatus>("checking");
  const [authError, setAuthError] = useState("");
  const walletConnection = useWalletConnection();
  const walletConnected = Boolean(walletConnection.address);
  const { data: credentialData } = useQuery({
    queryKey: ["polymarket-credentials"],
    queryFn: api.polymarketCredentials,
    enabled: authStatus === "authenticated" && walletConnected,
    refetchOnWindowFocus: false,
  });
  const activeWalletProfile = useMemo(
    () => credentialData?.profiles.find((profile) => profile.id === credentialData.active_id) ?? null,
    [credentialData],
  );
  const activeWalletMatches = Boolean(
    walletConnection.address &&
      activeWalletProfile &&
      normalizeAddress(activeWalletProfile.signer_address) === normalizeAddress(walletConnection.address),
  );
  const accountStateQueryKey = useMemo(
    () => ["polymarket-account-state", "global", activeWalletProfile?.id ?? "none"] as const,
    [activeWalletProfile?.id],
  );
  const { data: accountStateSnapshot = EMPTY_ACCOUNT_STATE } = useQuery({
    queryKey: accountStateQueryKey,
    queryFn: () => api.polymarketAccountState(),
    enabled: authStatus === "authenticated" && activeWalletMatches,
    refetchOnWindowFocus: false,
    refetchOnReconnect: true,
  });
  const [accountState, setAccountState] = useState<PolymarketAccountState>(EMPTY_ACCOUNT_STATE);
  const pathname = locationState.pathname;
  const searchParams = useMemo(() => new URLSearchParams(locationState.search), [locationState.search]);
  const pageTitle =
    pathname === "/btc-watch"
      ? false
      : pathname === "/reports/market-detail"
        ? false
        : route.routes.find((item) => item.path === pathname)?.name;
  const pageContainerClassName = pathname === "/btc-watch" ? "app-page-container-watch" : undefined;
  const navigate = useCallback((nextPathname: RouteKey, search = "") => {
    const next = { pathname: nextPathname, search };
    window.history.pushState(null, "", `${nextPathname}${search}`);
    setLocationState(next);
  }, []);

  useEffect(() => {
    localStorage.setItem(THEME_MODE_KEY, themeMode);
    document.body.dataset.theme = themeMode;
  }, [themeMode]);

  useEffect(() => {
    const onPopState = () => setLocationState(readLocationState());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      queryClient.clear();
      setAuthStatus("anonymous");
    });
    return () => setUnauthorizedHandler(null);
  }, [queryClient]);

  useEffect(() => {
    if (!activeWalletMatches) {
      setAccountState(EMPTY_ACCOUNT_STATE);
      queryClient.removeQueries({ queryKey: ["polymarket-account-state"] });
      return;
    }
    setAccountState(accountStateSnapshot);
  }, [accountStateSnapshot, activeWalletMatches, queryClient]);

  useEffect(() => {
    if (authStatus !== "authenticated" || !activeWalletMatches) {
      setAccountState(EMPTY_ACCOUNT_STATE);
      return;
    }
    let socket: WebSocket | null = null;
    let connectTimer = 0;
    let reconnectTimer = 0;
    let closedByEffect = false;

    const connect = () => {
      if (closedByEffect) return;
      socket = new WebSocket(api.polymarketAccountStateWsUrl());
      socket.onmessage = (event) => {
        const message = parsePolymarketAccountMessage(event.data);
        if (!message || message.condition_id !== null) return;
        queryClient.setQueryData(accountStateQueryKey, message.state);
        setAccountState(message.state);
      };
      socket.onclose = () => {
        if (closedByEffect) return;
        reconnectTimer = window.setTimeout(connect, 1000);
      };
    };

    // 顶栏展示账户级快照，不跟随具体 market 过滤；condition 过滤仍留给 BTC 页面局部面板。
    connectTimer = window.setTimeout(connect, 0);
    return () => {
      closedByEffect = true;
      if (connectTimer) window.clearTimeout(connectTimer);
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [accountStateQueryKey, activeWalletMatches, activeWalletProfile?.id, authStatus, queryClient]);

  useEffect(() => {
    let mounted = true;
    api.authSession()
      .then((session) => {
        if (!mounted) return;
        setAuthStatus(session.authenticated ? "authenticated" : "anonymous");
        setAuthError(session.configured ? "" : "认证未配置，请先在后端 .env 设置 AUTH_PASSWORD 和 AUTH_SESSION_SECRET");
      })
      .catch((error: Error) => {
        if (!mounted) return;
        setAuthStatus("anonymous");
        setAuthError(error.message);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const handleLogin = useCallback(async (password: string) => {
    const session = await api.login(password);
    setAuthStatus(session.authenticated ? "authenticated" : "anonymous");
    setAuthError("");
  }, []);

  const handleLogout = useCallback(async () => {
    await api.logout().catch(() => undefined);
    queryClient.clear();
    setAuthStatus("anonymous");
  }, [queryClient]);

  const authContent =
    authStatus === "checking" ? (
      <div className="auth-loading">
        <Spin /> 检查登录状态...
      </div>
    ) : (
      <LoginPage error={authError} onLogin={handleLogin} />
    );

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: themeMode === "dark" ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: {
          borderRadius: 6,
          colorPrimary: "#1677ff"
        }
      }}
    >
      <div className={`app-shell app-shell-${themeMode}`} data-theme={themeMode}>
        {authStatus !== "authenticated" ? (
          authContent
        ) : (
          <ProLayout
            title="Poly Auto"
            logo={false}
            route={route}
            location={{ pathname: pathname === "/reports/market-detail" ? "/reports" : pathname }}
            collapsed={siderCollapsed}
            breakpoint={false}
            onCollapse={(collapsed) => {
              setSiderCollapsed(collapsed);
              localStorage.setItem(SIDER_COLLAPSED_KEY, collapsed ? "1" : "0");
            }}
            menuItemRender={(item, dom) => (
              <button
                className="menu-link"
                type="button"
                onClick={() => navigate((item.path || "/btc-watch") as RouteKey)}
              >
                {dom}
              </button>
            )}
            actionsRender={() => [
              <AccountHeaderSummary
                key="account"
                accountState={accountState}
                activeProfile={activeWalletMatches ? activeWalletProfile : null}
                connectedAddress={walletConnection.address}
              />,
              <Button
                key="theme"
                type="text"
                icon={<MoonOutlined />}
                onClick={() => setThemeMode((value) => (value === "dark" ? "light" : "dark"))}
              >
                {themeMode === "dark" ? "浅色" : "深色"}
              </Button>,
              <Button key="logout" type="text" icon={<LogoutOutlined />} onClick={handleLogout}>
                退出
              </Button>
            ]}
            layout="mix"
            contentStyle={{ padding: 0 }}
          >
            <PageContainer className={pageContainerClassName} title={pageTitle}>
              <Suspense fallback={<div className="route-loading"><Spin /> 加载中...</div>}>
                {renderPage(pathname, searchParams, navigate)}
              </Suspense>
            </PageContainer>
            <PerformanceMonitorTooltip />
          </ProLayout>
        )}
      </div>
    </ConfigProvider>
  );
}

function AccountHeaderSummary({
  accountState,
  activeProfile,
  connectedAddress,
}: {
  accountState: PolymarketAccountState;
  activeProfile: PolymarketCredentialProfile | null;
  connectedAddress: string | null;
}) {
  const queryClient = useQueryClient();
  const [popoverOpen, setPopoverOpen] = useState(false);
  const accountStateQueryKey = useMemo(
    () => ["polymarket-account-state", "global", activeProfile?.id ?? "none"] as const,
    [activeProfile?.id],
  );
  const refreshAccountMutation = useMutation({
    mutationFn: api.refreshPolymarketAccountState,
    onSuccess: (state) => {
      queryClient.setQueryData(accountStateQueryKey, state);
      queryClient.invalidateQueries({ queryKey: ["polymarket-account-state"] });
    },
  });
  const activateCredentialMutation = useMutation({
    mutationFn: api.activatePolymarketCredential,
    onSuccess: (data) => {
      queryClient.removeQueries({ queryKey: ["polymarket-account-state"] });
      queryClient.setQueryData(["polymarket-credentials"], data);
      queryClient.invalidateQueries({ queryKey: ["polymarket-account-state"] });
    },
  });
  const handleWalletConnect = async () => {
    try {
      const address = await connectWallet();
      const credentialData = await queryClient.fetchQuery({
        queryKey: ["polymarket-credentials"],
        queryFn: api.polymarketCredentials,
      });
      queryClient.removeQueries({ queryKey: ["polymarket-account-state"] });
      const matchingProfile = credentialData.profiles.find(
        (profile) => normalizeAddress(profile.signer_address) === normalizeAddress(address),
      );
      if (!matchingProfile) {
        setPopoverOpen(true);
        return;
      }
      if (matchingProfile.id !== credentialData.active_id) {
        activateCredentialMutation.mutate(matchingProfile.id);
      }
      setPopoverOpen(false);
    } catch {
      setPopoverOpen(false);
    }
  };
  const handleWalletLogout = async () => {
    await disconnectWallet({ revoke: true });
    setPopoverOpen(false);
    queryClient.removeQueries({ queryKey: ["polymarket-account-state"] });
    queryClient.invalidateQueries({ queryKey: ["polymarket-credentials"] });
  };
  useEffect(() => {
    if (!connectedAddress) return;
    let cancelled = false;
    queryClient
      .fetchQuery({
        queryKey: ["polymarket-credentials"],
        queryFn: api.polymarketCredentials,
      })
      .then((credentialData) => {
        if (cancelled) return;
        const matchingProfile = credentialData.profiles.find(
          (profile) => normalizeAddress(profile.signer_address) === normalizeAddress(connectedAddress),
        );
        if (matchingProfile && matchingProfile.id !== credentialData.active_id) {
          activateCredentialMutation.mutate(matchingProfile.id);
        }
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [connectedAddress, queryClient]);
  if (!connectedAddress) {
    return (
      <div className="app-account-control">
        <button
          className="app-account-summary app-account-summary-guest"
          type="button"
          onClick={() => void handleWalletConnect()}
        >
          <div className="app-account-metrics">
            <span className="app-account-metric-label">Guest</span>
            <strong>游客模式</strong>
            <span className="app-account-login-action">
              <LoginOutlined />
              登录
            </span>
          </div>
          <span className="app-account-meta">连接后显示账户数据</span>
        </button>
      </div>
    );
  }
  const hasSyncError = Boolean(accountState.error);
  const hasBalanceSnapshot = Boolean(accountState.balance?.updated_at);
  const hasPositionsSnapshot = Boolean(accountState.last_positions_refresh_at);
  const cash = hasBalanceSnapshot ? accountState.balance?.cash ?? null : null;
  const portfolio = hasBalanceSnapshot || hasPositionsSnapshot ? accountPortfolioValue(accountState, hasPositionsSnapshot) : null;
  const accountLabel = accountState.wallet ?? accountState.clob_address;
  const shortAccountLabel = accountLabel ? shortAddress(accountLabel) : null;
  const profileLabel = activeProfile?.label || "MetaMask";
  const metaText = accountLabel
    ? hasSyncError
      ? `${shortAccountLabel} · 部分同步失败`
      : hasBalanceSnapshot || hasPositionsSnapshot
        ? `${shortAccountLabel}`
        : `${shortAccountLabel} · 等待同步`
    : "Account not configured";
  const accountSummary = (
    <button className="app-account-summary" type="button" onClick={() => setPopoverOpen((value) => !value)}>
      <span className="app-account-profile-label" title={profileLabel}>
        {profileLabel}
      </span>
      <div className="app-account-metrics">
        <span className="app-account-metric-label">Portfolio</span>
        <strong>{formatCurrency(portfolio)}</strong>
        <span className="app-account-separator">·</span>
        <span className="app-account-metric-label">Cash</span>
        <strong>{formatCurrency(cash)}</strong>
      </div>
      <span className="app-account-meta" title={accountState.error ?? undefined}>{metaText}</span>
    </button>
  );
  const accountSummaryNode = (
    <Popover
      trigger={[]}
      open={popoverOpen}
      onOpenChange={setPopoverOpen}
      placement="bottomRight"
      overlayClassName={activeProfile ? "app-account-popover app-account-popover-compact" : "app-account-popover"}
      content={<PolymarketCredentialManager variant="popover" />}
    >
      {accountSummary}
    </Popover>
  );
  return (
    <div className="app-account-control">
      {accountSummaryNode}
      <div className="app-account-actions">
        <Button
          aria-label="刷新账户"
          className="app-account-refresh"
          icon={<ReloadOutlined />}
          loading={refreshAccountMutation.isPending}
          size="small"
          type="text"
          disabled={!connectedAddress}
          onClick={() => refreshAccountMutation.mutate()}
        />
        <Button
          aria-label="登出钱包"
          className="app-account-logout"
          icon={<LogoutOutlined />}
          size="small"
          type="text"
          onClick={() => void handleWalletLogout()}
        >
          登出
        </Button>
      </div>
    </div>
  );
}

function parsePolymarketAccountMessage(value: string) {
  try {
    const message = JSON.parse(value) as PolymarketAccountStateWsMessage;
    if (message.type !== "polymarket.account_state.snapshot") return null;
    return message;
  } catch {
    return null;
  }
}

function accountPortfolioValue(accountState: PolymarketAccountState, includePositions: boolean) {
  const cash = accountState.balance?.cash;
  const positionsValue = includePositions
    ? accountState.positions.reduce((sum, position) => sum + (position.current_value ?? 0), 0)
    : 0;
  if (cash == null && !includePositions) return null;
  return (cash ?? 0) + positionsValue;
}

function formatCurrency(value: number | null) {
  if (value == null) return "-";
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

function normalizeAddress(value: string | null | undefined) {
  return value?.toLowerCase() ?? "";
}

function shortAddress(value: string) {
  if (value.length <= 12) return value;
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

function LoginPage({ error, onLogin }: { error: string; onLogin: (password: string) => Promise<void> }) {
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState(error);

  useEffect(() => {
    setMessage(error);
  }, [error]);

  return (
    <main className="login-page">
      <section className="login-panel">
        <Typography.Title level={2}>Poly Auto</Typography.Title>
        <Typography.Text type="secondary">请输入访问密码</Typography.Text>
        {message ? <Alert type="error" showIcon message={message} /> : null}
        <Form
          layout="vertical"
          onFinish={async ({ password }) => {
            setSubmitting(true);
            setMessage("");
            try {
              await onLogin(password);
            } catch (err) {
              setMessage(err instanceof Error ? err.message : "登录失败");
            } finally {
              setSubmitting(false);
            }
          }}
        >
          <Form.Item name="password" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password size="large" autoFocus placeholder="访问密码" />
          </Form.Item>
          <Button
            block
            size="large"
            type="primary"
            htmlType="submit"
            icon={<LoginOutlined />}
            loading={submitting}
          >
            登录
          </Button>
        </Form>
      </section>
    </main>
  );
}

function readThemeMode(): ThemeMode {
  return localStorage.getItem(THEME_MODE_KEY) === "dark" ? "dark" : "light";
}

function readSiderCollapsed() {
  return localStorage.getItem(SIDER_COLLAPSED_KEY) === "1";
}

function readLocationState(): { pathname: RouteKey; search: string } {
  const pathname = normalizePathname(window.location.pathname);
  return { pathname, search: window.location.search };
}

function normalizePathname(pathname: string): RouteKey {
  if (pathname === "/btc-watch") return "/btc-watch";
  if (pathname === "/signals") return "/signals";
  if (pathname === "/reports") return "/reports";
  if (pathname === "/reports/market-detail") return "/reports/market-detail";
  if (pathname === "/telegram") return "/telegram";
  if (pathname === "/system-tasks") return "/system-tasks";
  if (pathname === "/settings") return "/settings";
  return "/btc-watch";
}
