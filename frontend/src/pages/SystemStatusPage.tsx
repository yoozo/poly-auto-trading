import { ReloadOutlined } from "@ant-design/icons";
import { Badge, Button, Card, Col, Empty, Row, Space, Statistic, Table, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { api, type ServiceEventRecord, type ServiceHealth } from "../api/client";

const stateColor: Record<string, "success" | "processing" | "default" | "error" | "warning"> = {
  running: "success",
  idle: "default",
  unknown: "warning",
  error: "error"
};

export default function SystemStatusPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 10_000 });
  const services = useQuery({
    queryKey: ["services"],
    queryFn: api.services,
    refetchInterval: 10_000
  });
  const events = useQuery({
    queryKey: ["service-events"],
    queryFn: () => api.serviceEvents({ limit: 80 }),
    refetchInterval: 10_000
  });

  return (
    <div className="page-stack">
      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <Card>
            <Statistic title="API" value={health.data?.checks.api.ok ? "OK" : "Unknown"} />
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <Statistic
              title="Database"
              value={health.data?.checks.database.ok ? "OK" : "Unavailable"}
              valueStyle={{ color: health.data?.checks.database.ok ? "#16a34a" : "#dc2626" }}
            />
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card>
            <Statistic title="Updated" value={health.data ? new Date(health.data.time).toLocaleTimeString() : "-"} />
          </Card>
        </Col>
      </Row>

      <Card title="服务状态">
        <Table<ServiceHealth>
          rowKey="name"
          loading={services.isFetching}
          dataSource={services.data ?? []}
          pagination={false}
          columns={[
            {
              title: "服务",
              dataIndex: "name"
            },
            {
              title: "状态",
              dataIndex: "state",
              render: (value: string) => <Badge status={stateColor[value] ?? "default"} text={value} />
            },
            {
              title: "更新时间",
              dataIndex: "last_update",
              render: (value: string) => new Date(value).toLocaleString()
            },
            {
              title: "错误",
              dataIndex: "last_error",
              render: (value: string | null) => value || <Typography.Text type="secondary">-</Typography.Text>
            },
            {
              title: "详情",
              dataIndex: "metadata",
              render: (_value: unknown, record) => renderServiceMetadata(record)
            }
          ]}
        />
      </Card>

      <Card
        title="服务事件"
        extra={
          <Button size="small" icon={<ReloadOutlined />} onClick={() => events.refetch()} loading={events.isFetching}>
            刷新
          </Button>
        }
      >
        <Table<ServiceEventRecord>
          rowKey="id"
          size="small"
          loading={events.isFetching}
          dataSource={events.data ?? []}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无服务事件" /> }}
          pagination={{ pageSize: 8, size: "small" }}
          columns={[
            {
              title: "时间",
              dataIndex: "created_at",
              width: 180,
              render: (value: string) => new Date(value).toLocaleString()
            },
            {
              title: "服务",
              dataIndex: "service",
              width: 140,
              render: (value: string) => <Tag>{value}</Tag>
            },
            {
              title: "级别",
              dataIndex: "level",
              width: 100,
              render: (value: string) => <Tag color={eventLevelColor(value)}>{value}</Tag>
            },
            {
              title: "消息",
              dataIndex: "message"
            },
            {
              title: "详情",
              dataIndex: "payload",
              render: (value: Record<string, unknown>) => (
                <Typography.Text code>{Object.keys(value).length ? JSON.stringify(value) : "-"}</Typography.Text>
              )
            }
          ]}
        />
      </Card>

      <Card title="权限 / 配置预留">
        <Space wrap size={8}>
          <Tag color="default">RBAC 未启用</Tag>
          <Tag color="default">策略配置未启用</Tag>
          <Typography.Text type="secondary">当前版本仅预留入口，后续可接入用户、角色和系统级参数。</Typography.Text>
        </Space>
      </Card>
    </div>
  );
}

function renderServiceMetadata(record: ServiceHealth) {
  if (record.name === "kline_backfill" || record.name === "indicator_backfill") {
    const metadata = record.metadata as { task_id?: number; symbol?: string; current_interval?: string; state?: string };
    return (
      <Typography.Text>
        #{metadata.task_id || "-"} / {metadata.symbol || "BTCUSDT"} / {metadata.current_interval || metadata.state || record.state}
      </Typography.Text>
    );
  }
  if (record.name !== "telegram") {
    return <Typography.Text type="secondary">-</Typography.Text>;
  }
  const configured = Boolean(record.metadata?.configured);
  const enabled = Boolean(record.metadata?.enabled);
  const lastDelivery = record.metadata?.last_delivery as { title?: string; status?: string } | undefined;
  return (
    <Typography.Text>
      {configured ? "已配置" : "未配置"} / {enabled ? "已开启" : "已关闭"}
      {lastDelivery?.title ? ` / ${lastDelivery.title}: ${lastDelivery.status || "-"}` : ""}
    </Typography.Text>
  );
}

function eventLevelColor(level: string) {
  if (level === "error") return "error";
  if (level === "warning") return "warning";
  if (level === "info") return "processing";
  return "default";
}
