import { Badge, Card, Col, Row, Statistic, Table, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { api, type ServiceHealth } from "../api/client";

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
              render: (value: string) => (
                <Badge status={stateColor[value] ?? "default"} text={value} />
              )
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
            }
          ]}
        />
      </Card>
    </div>
  );
}
