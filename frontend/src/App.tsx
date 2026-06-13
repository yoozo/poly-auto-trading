import {
  AlertOutlined,
  BarChartOutlined,
  BellOutlined,
  DashboardOutlined,
  LineChartOutlined,
  MoonOutlined,
  SettingOutlined
} from "@ant-design/icons";
import { PageContainer, ProLayout } from "@ant-design/pro-components";
import { Button, ConfigProvider, Spin, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import { lazy, Suspense, useEffect, useState } from "react";

const BTCWatchPage = lazy(() => import("./pages/BTCWatchPage"));
const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const ReportsPage = lazy(() => import("./pages/ReportsPage"));
const SignalsPage = lazy(() => import("./pages/SignalsPage"));
const SystemStatusPage = lazy(() => import("./pages/SystemStatusPage"));
const TelegramNotificationsPage = lazy(() => import("./pages/TelegramNotificationsPage"));

type RouteKey = "/dashboard" | "/btc-watch" | "/signals" | "/reports" | "/telegram" | "/settings";
type ThemeMode = "light" | "dark";

const THEME_MODE_KEY = "poly-auto.themeMode";

const route = {
  path: "/",
  routes: [
    { path: "/dashboard", name: "总览", icon: <DashboardOutlined /> },
    { path: "/btc-watch", name: "BTC 看盘", icon: <LineChartOutlined /> },
    { path: "/signals", name: "信号", icon: <AlertOutlined /> },
    { path: "/telegram", name: "Telegram 提醒", icon: <BellOutlined /> },
    { path: "/reports", name: "收益报表", icon: <BarChartOutlined /> },
    { path: "/settings", name: "系统配置", icon: <SettingOutlined /> }
  ]
};

function renderPage(pathname: RouteKey) {
  if (pathname === "/btc-watch") return <BTCWatchPage />;
  if (pathname === "/signals") return <SignalsPage />;
  if (pathname === "/reports") return <ReportsPage />;
  if (pathname === "/telegram") return <TelegramNotificationsPage />;
  if (pathname === "/settings") return <SystemStatusPage />;
  return <DashboardPage />;
}

export default function App() {
  const [pathname, setPathname] = useState<RouteKey>("/dashboard");
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => readThemeMode());
  const pageTitle = pathname === "/btc-watch" ? false : route.routes.find((item) => item.path === pathname)?.name;

  useEffect(() => {
    localStorage.setItem(THEME_MODE_KEY, themeMode);
    document.body.dataset.theme = themeMode;
  }, [themeMode]);

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
      <ProLayout
        title="Poly Auto"
        logo={false}
        route={route}
        location={{ pathname }}
        menuItemRender={(item, dom) => (
          <button
            className="menu-link"
            type="button"
            onClick={() => setPathname((item.path || "/dashboard") as RouteKey)}
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
          </Button>
        ]}
        layout="mix"
        contentStyle={{ padding: 0 }}
      >
        <PageContainer title={pageTitle}>
          <Suspense fallback={<div className="route-loading"><Spin /> 加载中...</div>}>
            {renderPage(pathname)}
          </Suspense>
        </PageContainer>
      </ProLayout>
    </ConfigProvider>
  );
}

function readThemeMode(): ThemeMode {
  return localStorage.getItem(THEME_MODE_KEY) === "dark" ? "dark" : "light";
}
