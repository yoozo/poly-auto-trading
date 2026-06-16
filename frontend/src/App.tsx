import {
  AlertOutlined,
  BarChartOutlined,
  BellOutlined,
  LoginOutlined,
  LineChartOutlined,
  LogoutOutlined,
  MoonOutlined,
  SettingOutlined
} from "@ant-design/icons";
import { PageContainer, ProLayout } from "@ant-design/pro-components";
import { Alert, Button, ConfigProvider, Form, Input, Spin, Typography, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import { useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { api, setUnauthorizedHandler } from "./api/client";

const BTCWatchPage = lazy(() => import("./pages/BTCWatchPage"));
const MarketDetailPage = lazy(() => import("./pages/MarketDetailPage"));
const ReportsPage = lazy(() => import("./pages/ReportsPage"));
const SignalsPage = lazy(() => import("./pages/SignalsPage"));
const SystemStatusPage = lazy(() => import("./pages/SystemStatusPage"));
const TelegramNotificationsPage = lazy(() => import("./pages/TelegramNotificationsPage"));

type RouteKey = "/btc-watch" | "/signals" | "/reports" | "/reports/market-detail" | "/telegram" | "/settings";
type ThemeMode = "light" | "dark";
type AuthStatus = "checking" | "authenticated" | "anonymous";

const THEME_MODE_KEY = "poly-auto.themeMode";
const SIDER_COLLAPSED_KEY = "poly-auto.siderCollapsed";

const route = {
  path: "/",
  routes: [
    { path: "/btc-watch", name: "BTC 看盘", icon: <LineChartOutlined /> },
    { path: "/signals", name: "信号", icon: <AlertOutlined /> },
    { path: "/telegram", name: "Telegram 提醒", icon: <BellOutlined /> },
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
  const pathname = locationState.pathname;
  const searchParams = useMemo(() => new URLSearchParams(locationState.search), [locationState.search]);
  const pageTitle =
    pathname === "/btc-watch"
      ? false
      : pathname === "/reports/market-detail"
        ? false
        : route.routes.find((item) => item.path === pathname)?.name;
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
        <PageContainer title={pageTitle}>
          <Suspense fallback={<div className="route-loading"><Spin /> 加载中...</div>}>
            {renderPage(pathname, searchParams, navigate)}
          </Suspense>
        </PageContainer>
      </ProLayout>
      )}
    </ConfigProvider>
  );
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
  if (pathname === "/settings") return "/settings";
  return "/btc-watch";
}
