import { Activity, BarChart3, Bell, CandlestickChart, Gauge, LineChart, ListChecks, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import type { PolyMarket } from "./api/client";
import Dashboard from "./pages/Dashboard";
import MarketDetail from "./pages/MarketDetail";
import Markets from "./pages/Markets";
import Orders from "./pages/Orders";
import Signals from "./pages/Signals";
import Stats from "./pages/Stats";

type Page = "dashboard" | "markets" | "marketDetail" | "signals" | "orders" | "stats";

const navItems: Array<{ id: Page; label: string; icon: LucideIcon }> = [
  { id: "dashboard", label: "Dashboard", icon: Gauge },
  { id: "markets", label: "Markets", icon: CandlestickChart },
  { id: "signals", label: "Signals", icon: Activity },
  { id: "orders", label: "Orders", icon: ListChecks },
  { id: "stats", label: "Stats", icon: BarChart3 }
];

function renderPage(page: Page, selectedMarket: PolyMarket | null, openMarket: (market: PolyMarket) => void, backToMarkets: () => void) {
  if (page === "markets") return <Markets onOpenMarket={openMarket} />;
  if (page === "marketDetail" && selectedMarket) return <MarketDetail market={selectedMarket} onBack={backToMarkets} />;
  if (page === "signals") return <Signals />;
  if (page === "orders") return <Orders />;
  if (page === "stats") return <Stats />;
  return <Dashboard />;
}

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");
  const [selectedMarket, setSelectedMarket] = useState<PolyMarket | null>(null);
  const [apiClock, setApiClock] = useState(new Date());

  useEffect(() => {
    const timer = window.setInterval(() => setApiClock(new Date()), 30000);
    return () => window.clearInterval(timer);
  }, []);

  const openMarket = (market: PolyMarket) => {
    setSelectedMarket(market);
    setPage("marketDetail");
  };

  const backToMarkets = () => setPage("markets");

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <LineChart size={24} />
          <div>
            <strong>Poly Auto</strong>
            <span>BTC 5m/15m</span>
          </div>
        </div>
        <nav className="nav-list">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={page === item.id ? "nav-button active" : "nav-button"}
                key={item.id}
                onClick={() => setPage(item.id)}
                title={item.label}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
        <div className="sidebar-foot">
          <Bell size={16} />
          <span>Read-only monitor</span>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div>
            <p className="eyebrow">Polymarket execution dashboard</p>
            <h1>{page === "marketDetail" ? "Market" : navItems.find((item) => item.id === page)?.label}</h1>
          </div>
          <time>{apiClock.toLocaleTimeString()}</time>
        </header>
        {renderPage(page, selectedMarket, openMarket, backToMarkets)}
      </main>
    </div>
  );
}
