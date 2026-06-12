import { ReloadOutlined } from "@ant-design/icons";
import { Button, Card, Segmented, Space, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type CandleInterval } from "../api/client";
import KlineChart from "../components/KlineChart";

const intervals: CandleInterval[] = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];

export default function BTCWatchPage() {
  const [interval, setInterval] = useState<CandleInterval>("1m");
  const { data = [], error, isFetching, refetch } = useQuery({
    queryKey: ["candles", interval],
    queryFn: () => api.candles(interval, 300),
    refetchInterval: 10_000
  });

  const latest = data.at(-1);

  return (
    <div className="watch-page">
      <Card
        className="watch-toolbar"
        bodyStyle={{ padding: 12 }}
      >
        <Space wrap>
          <Typography.Text strong>BTCUSDT</Typography.Text>
          <Segmented
            options={intervals}
            value={interval}
            onChange={(value) => setInterval(value as CandleInterval)}
          />
          <Button icon={<ReloadOutlined />} loading={isFetching} onClick={() => refetch()}>
            刷新
          </Button>
          {latest && (
            <Typography.Text type="secondary">
              最新 {latest.close.toLocaleString("en-US", { maximumFractionDigits: 2 })}
            </Typography.Text>
          )}
          {error instanceof Error && <Typography.Text type="danger">{error.message}</Typography.Text>}
        </Space>
      </Card>
      <Card className="watch-chart-card" bodyStyle={{ padding: 0 }}>
        <KlineChart candles={data} />
      </Card>
    </div>
  );
}

