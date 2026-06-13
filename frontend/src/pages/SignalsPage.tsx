import { Card, Space, Table, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { api, type SignalRecord } from "../api/client";

export default function SignalsPage() {
  const signals = useQuery({
    queryKey: ["signals"],
    queryFn: () => api.signals(undefined, 50),
    refetchInterval: 10_000
  });

  return (
    <div className="page-stack signals-page">
      <Card title="信号">
        <Table<SignalRecord>
          rowKey="id"
          size="small"
          loading={signals.isFetching}
          dataSource={signals.data ?? []}
          pagination={false}
          scroll={{ x: 940 }}
          expandable={{
            expandedRowRender: (record) => <SignalDetails signal={record} />
          }}
          columns={[
            { title: "时间", dataIndex: "occurred_at", width: 170, render: formatDateTime },
            { title: "目标", dataIndex: "target_key", width: 140 },
            { title: "信号", dataIndex: "signal_label", width: 220 },
            { title: "买卖", dataIndex: "action", width: 90, render: renderAction },
            { title: "方向", dataIndex: "direction", width: 90, render: renderDirection },
            { title: "类型", dataIndex: "signal_key", width: 160 },
            { title: "评分", dataIndex: "score", width: 90, render: formatNumber },
            { title: "创建时间", dataIndex: "created_at", width: 170, render: formatDateTime }
          ]}
        />
      </Card>
    </div>
  );
}

function SignalDetails({ signal }: { signal: SignalRecord }) {
  return (
    <Space direction="vertical" size={8} className="signal-details">
      <div>
        <Typography.Text strong>Metadata</Typography.Text>
        <pre className="json-preview">{formatJson(signal.metadata)}</pre>
      </div>
      <div>
        <Typography.Text strong>Input Snapshot</Typography.Text>
        <pre className="json-preview">{formatJson(signal.input_snapshot)}</pre>
      </div>
    </Space>
  );
}

function renderAction(value: SignalRecord["action"]) {
  const color = value === "buy" ? "green" : value === "sell" ? "red" : "default";
  const label = value === "buy" ? "买入" : value === "sell" ? "卖出" : "观望";
  return <Tag color={color}>{label}</Tag>;
}

function renderDirection(value: SignalRecord["direction"]) {
  const color = value === "long" ? "green" : value === "short" ? "red" : "default";
  const label = value === "long" ? "做多" : value === "short" ? "做空" : "中性";
  return <Tag color={color}>{label}</Tag>;
}

function formatDateTime(value: string) {
  return new Date(value).toLocaleString();
}

function formatNumber(value: number | null) {
  if (value === null || value === undefined) return "-";
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function formatJson(value: Record<string, unknown>) {
  return JSON.stringify(value, null, 2);
}
