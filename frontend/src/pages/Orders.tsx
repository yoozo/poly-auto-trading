import { useCallback, useState } from "react";
import { api, type Notification, type Order } from "../api/client";
import { usePolling } from "../hooks/usePolling";

export default function Orders() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const { lastRefresh, error } = usePolling(
    useCallback(async () => {
      const [nextOrders, nextNotifications] = await Promise.all([api.orders(), api.notifications()]);
      setOrders(nextOrders);
      setNotifications(nextNotifications);
    }, [])
  );

  return (
    <div className="content-grid">
      <section className="panel wide">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Execution</p>
            <h2>Orders</h2>
          </div>
          <span className="metric">{orders.length} records · {lastRefresh ? lastRefresh.toLocaleTimeString() : "loading"}</span>
        </div>
        {error && <p className="error-text">{error}</p>}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Order</th>
                <th>Market</th>
                <th>Side</th>
                <th>Price</th>
                <th>Filled</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => (
                <tr key={order.id}>
                  <td className="mono">{order.id}</td>
                  <td>{order.market_id}</td>
                  <td>{order.side}</td>
                  <td>{order.price.toFixed(2)}</td>
                  <td>{order.filled_size} / {order.size}</td>
                  <td><span className={`badge ${order.status}`}>{order.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Telegram</p>
            <h2>Notifications</h2>
          </div>
        </div>
        <div className="notification-list">
          {notifications.map((note) => (
            <article key={note.id}>
              <strong>{note.event_type}</strong>
              <p>{note.message}</p>
              <span>{new Date(note.sent_at).toLocaleTimeString()} · {note.status}</span>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
