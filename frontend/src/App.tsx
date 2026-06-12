import {
  BarChartOutlined,
  DashboardOutlined,
  LineChartOutlined,
  SettingOutlined
} from "@ant-design/icons";
import { PageContainer, ProLayout } from "@ant-design/pro-components";
import { useState } from "react";
import BTCWatchPage from "./pages/BTCWatchPage";
import DashboardPage from "./pages/DashboardPage";
import SystemStatusPage from "./pages/SystemStatusPage";

type RouteKey = "/dashboard" | "/btc-watch" | "/reports" | "/settings";

const route = {
  path: "/",
  routes: [
    { path: "/dashboard", name: "总览", icon: <DashboardOutlined /> },
    { path: "/btc-watch", name: "BTC 看盘", icon: <LineChartOutlined /> },
    { path: "/reports", name: "收益报表", icon: <BarChartOutlined /> },
    { path: "/settings", name: "系统配置", icon: <SettingOutlined /> }
  ]
};

function renderPage(pathname: RouteKey) {
  if (pathname === "/btc-watch") return <BTCWatchPage />;
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
