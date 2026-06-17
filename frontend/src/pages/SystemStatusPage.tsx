import { CloudDownloadOutlined, ReloadOutlined } from "@ant-design/icons";
import { Badge, Button, Card, Col, Empty, Row, Space, Statistic, Table, Tag, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type CandleBackfillProgressStatus,
  type CandleBackfillStatus,
  type IndicatorBackfillProgressStatus,
  type IndicatorBackfillStatus,
  type ServiceEventRecord,
  type ServiceHealth
} from "../api/client";

const stateColor: Record<string, "success" | "processing" | "default" | "error" | "warning"> = {
  running: "success",
  idle: "default",
  unknown: "warning",
  error: "error"
};

export default function SystemStatusPage() {
  const queryClient = useQueryClient();
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
  const candleBackfill = useQuery({
    queryKey: ["candle-backfill"],
    queryFn: api.candleBackfillStatus,
    refetchInterval: (query) => (query.state.data?.state === "running" ? 3_000 : 10_000)
  });
  const indicatorBackfill = useQuery({
    queryKey: ["indicator-backfill"],
    queryFn: api.indicatorBackfillStatus,
    refetchInterval: (query) => (query.state.data?.state === "running" ? 3_000 : 10_000)
  });
  const startCandleBackfill = useMutation({
    mutationFn: api.startCandleBackfill,
    onSuccess: async (status) => {
      message.success(status.state === "running" ? "K 线下载任务已启动" : "K 线下载任务已在运行");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["candle-backfill"] }),
        queryClient.invalidateQueries({ queryKey: ["services"] }),
        queryClient.invalidateQueries({ queryKey: ["service-events"] })
      ]);
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : "启动 K 线下载失败");
    }
  });
  const startIndicatorBackfill = useMutation({
    mutationFn: api.startIndicatorBackfill,
    onSuccess: async (status) => {
      message.success(status.state === "running" ? "指标计算任务已启动" : "指标计算任务已恢复");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["indicator-backfill"] }),
        queryClient.invalidateQueries({ queryKey: ["services"] }),
        queryClient.invalidateQueries({ queryKey: ["service-events"] })
      ]);
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : "启动指标计算失败");
    }
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
        title="K 线数据"
        extra={
          <Space>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => candleBackfill.refetch()}
              loading={candleBackfill.isFetching}
            >
              刷新
            </Button>
            <Button
              type="primary"
              icon={<CloudDownloadOutlined />}
              loading={startCandleBackfill.isPending}
              onClick={() => startCandleBackfill.mutate()}
            >
              {candleBackfill.data?.state === "idle" || candleBackfill.data?.state === "completed" ? "一键下载" : "继续下载"}
            </Button>
          </Space>
        }
      >
        {renderCandleBackfill(candleBackfill.data)}
      </Card>

      <Card
        title="指标数据"
        extra={
          <Space>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => indicatorBackfill.refetch()}
              loading={indicatorBackfill.isFetching}
            >
              刷新
            </Button>
            <Button
              type="primary"
              icon={<CloudDownloadOutlined />}
              loading={startIndicatorBackfill.isPending}
              onClick={() => startIndicatorBackfill.mutate()}
            >
              {indicatorBackfill.data?.state === "idle" || indicatorBackfill.data?.state === "completed" ? "一键计算" : "继续计算"}
            </Button>
          </Space>
        }
      >
        {renderIndicatorBackfill(indicatorBackfill.data)}
      </Card>

      <Card
        title="服务事件"
        extra={<Button size="small" onClick={() => events.refetch()} loading={events.isFetching}>重试</Button>}
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
  if (record.name === "kline_backfill") {
    const metadata = record.metadata as Partial<CandleBackfillStatus>;
    return (
      <Typography.Text>
        #{metadata.task_id || "-"} / {metadata.symbol || "BTCUSDT"} / {metadata.current_interval || metadata.state || record.state}
      </Typography.Text>
    );
  }
  if (record.name === "indicator_backfill") {
    const metadata = record.metadata as Partial<IndicatorBackfillStatus>;
    return (
      <Typography.Text>
        #{metadata.task_id || "-"} / {metadata.symbol || "BTCUSDT"} / {metadata.current_interval || metadata.state || record.state}
      </Typography.Text>
    );
  }
  if (record.name !== "telegram") {
    return <Typography.Text type="secondary">-</Typography.Text>;
  }
  // 目前只有 telegram health 带业务 metadata：配置状态、开关状态和最近一次通知投递结果。
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

function renderCandleBackfill(status?: CandleBackfillStatus) {
  if (!status) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 K 线下载状态" />;
  }
  const progressRows: CandleBackfillProgressStatus[] = status.progress.length
    ? status.progress
    : Object.entries(status.fetched).map(([interval, count]) => ({
        interval,
        status: "pending" as const,
        next_start_ms: 0,
        end_ms: status.end_ms ?? 0,
        inserted_count: count,
        last_error: "",
        started_at: null,
        finished_at: null
      })) as CandleBackfillProgressStatus[];
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Row gutter={[16, 16]}>
        <Col xs={24} md={6}>
          <Statistic title="状态" value={backfillStateText(status.state)} />
        </Col>
        <Col xs={24} md={6}>
          <Statistic title="任务" value={status.task_id ? `#${status.task_id}` : "-"} />
        </Col>
        <Col xs={24} md={6}>
          <Statistic title="当前周期" value={status.current_interval || "-"} />
        </Col>
        <Col xs={24} md={6}>
          <Statistic title="已写入" value={status.total_inserted || progressRows.reduce((sum, row) => sum + row.inserted_count, 0)} />
        </Col>
      </Row>
      <Space wrap>
        <Tag color={backfillStateColor(status.state)}>{backfillStateText(status.state)}</Tag>
        <Tag>{status.symbol || "BTCUSDT"}</Tag>
        {status.started_at ? <Tag>开始 {new Date(status.started_at).toLocaleString()}</Tag> : null}
        {status.finished_at ? <Tag>结束 {new Date(status.finished_at).toLocaleString()}</Tag> : null}
        {status.current_start_ms !== null ? <Tag>游标 {new Date(status.current_start_ms).toLocaleString()}</Tag> : null}
        {status.error ? <Tag color="error">{status.error}</Tag> : null}
      </Space>
      <Table
        rowKey="interval"
        size="small"
        pagination={false}
        dataSource={progressRows}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未开始下载" /> }}
        columns={[
          {
            title: "周期",
            dataIndex: "interval",
            width: 120,
            render: (value: string) => <Tag>{value}</Tag>
          },
          {
            title: "状态",
            dataIndex: "status",
            width: 120,
            render: (value: string) => <Tag color={progressStateColor(value)}>{progressStateText(value)}</Tag>
          },
          {
            title: "断点",
            dataIndex: "next_start_ms",
            render: (value: number) => (value > 0 ? new Date(value).toLocaleString() : "起点")
          },
          {
            title: "已写入 K 线",
            dataIndex: "inserted_count",
            render: (value: number) => value.toLocaleString()
          },
          {
            title: "错误",
            dataIndex: "last_error",
            render: (value: string) => value || <Typography.Text type="secondary">-</Typography.Text>
          }
        ]}
      />
      <Typography.Text type="secondary">
        任务会按服务端配置的 Binance 周期逐个下载，每轮并发 10 页、每页最多 1000 根；失败或服务重启后会从断点继续。
      </Typography.Text>
    </Space>
  );
}

function backfillStateText(state: CandleBackfillStatus["state"]) {
  if (state === "running") return "下载中";
  if (state === "completed") return "已完成";
  if (state === "error") return "失败";
  return "空闲";
}

function backfillStateColor(state: CandleBackfillStatus["state"]) {
  if (state === "running") return "processing";
  if (state === "completed") return "success";
  if (state === "error") return "error";
  return "default";
}

function progressStateText(state: string) {
  if (state === "running") return "下载中";
  if (state === "completed") return "已完成";
  if (state === "error") return "失败";
  return "等待";
}

function progressStateColor(state: string) {
  if (state === "running") return "processing";
  if (state === "completed") return "success";
  if (state === "error") return "error";
  return "default";
}

function renderIndicatorBackfill(status?: IndicatorBackfillStatus) {
  if (!status) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无指标计算状态" />;
  }
  const progressRows: IndicatorBackfillProgressStatus[] = status.progress;
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Row gutter={[16, 16]}>
        <Col xs={24} md={6}>
          <Statistic title="状态" value={backfillStateText(status.state)} />
        </Col>
        <Col xs={24} md={6}>
          <Statistic title="任务" value={status.task_id ? `#${status.task_id}` : "-"} />
        </Col>
        <Col xs={24} md={6}>
          <Statistic title="当前周期" value={status.current_interval || "-"} />
        </Col>
        <Col xs={24} md={6}>
          <Statistic title="已写入" value={status.total_inserted || progressRows.reduce((sum, row) => sum + row.inserted_count, 0)} />
        </Col>
      </Row>
      <Space wrap>
        <Tag color={backfillStateColor(status.state)}>{backfillStateText(status.state)}</Tag>
        <Tag>{status.symbol || "BTCUSDT"}</Tag>
        {status.started_at ? <Tag>开始 {new Date(status.started_at).toLocaleString()}</Tag> : null}
        {status.finished_at ? <Tag>结束 {new Date(status.finished_at).toLocaleString()}</Tag> : null}
        {status.current_start_ms !== null ? <Tag>断点 {new Date(status.current_start_ms).toLocaleString()}</Tag> : null}
        {status.error ? <Tag color="error">{status.error}</Tag> : null}
      </Space>
      <Table
        rowKey="interval"
        size="small"
        pagination={false}
        dataSource={progressRows}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未开始计算" /> }}
        columns={[
          {
            title: "周期",
            dataIndex: "interval",
            width: 120,
            render: (value: string) => <Tag>{value}</Tag>
          },
          {
            title: "状态",
            dataIndex: "status",
            width: 120,
            render: (value: string) => <Tag color={progressStateColor(value)}>{progressStateText(value)}</Tag>
          },
          {
            title: "断点",
            dataIndex: "next_start_ms",
            render: (value: number) => (value > 0 ? new Date(value).toLocaleString() : "起点")
          },
          {
            title: "已写入指标",
            dataIndex: "inserted_count",
            render: (value: number) => value.toLocaleString()
          },
          {
            title: "错误",
            dataIndex: "last_error",
            render: (value: string) => value || <Typography.Text type="secondary">-</Typography.Text>
          }
        ]}
      />
      <Typography.Text type="secondary">
        指标会从已下载的 K 线分批计算并保存 RSI、RSI EMA、RSI-EMA diff 和 Bollinger Bands。
      </Typography.Text>
    </Space>
  );
}

function eventLevelColor(level: string) {
  if (level === "error") return "error";
  if (level === "warning") return "warning";
  if (level === "info") return "processing";
  return "default";
}
