import { Card, Col, Row, Statistic } from "antd";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export default function DashboardPage() {
  const { data } = useQuery({ queryKey: ["health"], queryFn: api.health });

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} md={8}>
        <Card>
          <Statistic title="API 状态" value={data?.status ?? "unknown"} />
        </Card>
      </Col>
      <Col xs={24} md={8}>
        <Card>
          <Statistic title="交易模式" value="Monitor" />
        </Card>
      </Col>
      <Col xs={24} md={8}>
        <Card>
          <Statistic title="标的" value="BTCUSDT" />
        </Card>
      </Col>
    </Row>
  );
}

