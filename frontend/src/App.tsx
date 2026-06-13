import {
  AlertOutlined,
  BarChartOutlined,
  BellOutlined,
  DashboardOutlined,
  LineChartOutlined,
  SettingOutlined
} from "@ant-design/icons";
import { PageContainer, ProLayout } from "@ant-design/pro-components";
import { useState } from "react";
import BTCWatchPage from "./pages/BTCWatchPage";
import DashboardPage from "./pages/DashboardPage";
import ReportsPage from "./pages/ReportsPage";
import SignalsPage from "./pages/SignalsPage";
import SystemStatusPage from "./pages/SystemStatusPage";
import TelegramNotificationsPage from "./pages/TelegramNotificationsPage";

type RouteKey = "/dashboard" | "/btc-watch" | "/signals" | "/reports" | "/telegram" | "/settings";

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
  const pageTitle = pathname === "/btc-watch" ? false : route.routes.find((item) => item.path === pathname)?.name;

  return (
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
      layout="mix"
      contentStyle={{ padding: 0 }}
    >
      <PageContainer title={pageTitle}>
        {renderPage(pathname)}
      </PageContainer>
    </ProLayout>
  );
}
