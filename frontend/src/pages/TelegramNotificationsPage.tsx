import { Alert, Button, Card, Space, Switch, Table, Tag, Typography } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type NotificationDelivery, type SignalRecord, type TelegramStatus } from "../api/client";

export default function TelegramNotificationsPage() {
  const queryClient = useQueryClient();
  // Telegram 配置状态和投递记录分开轮询：配置影响开关，delivery 表用于追踪实际发送/跳过结果。
  const telegramStatus = useQuery({
    queryKey: ["telegram-status"],
    queryFn: api.telegramStatus,
    refetchInterval: 10_000
  });
  const notificationDeliveries = useQuery({
    queryKey: ["notification-deliveries", "telegram"],
    queryFn: () => api.notificationDeliveries(undefined, 20),
    refetchInterval: 10_000
  });
  const updateTelegramStatus = useMutation({
    mutationFn: (enabled: boolean) => api.updateTelegramStatus(enabled),
    onSuccess: () => {
      // 开关状态会同步影响系统服务页里的 telegram health 展示。
      void queryClient.invalidateQueries({ queryKey: ["telegram-status"] });
      void queryClient.invalidateQueries({ queryKey: ["services"] });
    }
  });
  const testTelegram = useMutation({
    mutationFn: api.testTelegram,
    onSuccess: () => {
      // 测试发送会写服务事件，也可能生成新的状态信息，所以刷新三处视图数据。
      void queryClient.invalidateQueries({ queryKey: ["telegram-status"] });
      void queryClient.invalidateQueries({ queryKey: ["services"] });
      void queryClient.invalidateQueries({ queryKey: ["notification-deliveries"] });
    }
  });

  return (
    <div className="page-stack telegram-page">
      <TelegramSettingsCard
        loading={telegramStatus.isLoading}
        status={telegramStatus.data}
        statusError={telegramStatus.error}
        deliveries={notificationDeliveries.data ?? []}
        deliveriesLoading={notificationDeliveries.isFetching}
        updatePending={updateTelegramStatus.isPending}
        testPending={testTelegram.isPending}
        testError={testTelegram.error}
        testSuccess={testTelegram.isSuccess}
        onEnabledChange={(enabled) => updateTelegramStatus.mutate(enabled)}
        onTest={() => testTelegram.mutate()}
      />
    </div>
  );
}

function TelegramSettingsCard({
  loading,
  status,
  statusError,
  deliveries,
  deliveriesLoading,
  updatePending,
  testPending,
  testError,
  testSuccess,
  onEnabledChange,
  onTest,
}: {
  loading: boolean;
  status: TelegramStatus | undefined;
  statusError: unknown;
  deliveries: NotificationDelivery[];
  deliveriesLoading: boolean;
  updatePending: boolean;
  testPending: boolean;
  testError: unknown;
  testSuccess: boolean;
  onEnabledChange: (enabled: boolean) => void;
  onTest: () => void;
}) {
  return (
    <Card title="Telegram 提醒" className="telegram-card">
      <Space direction="vertical" size={12} className="telegram-panel">
        {statusError instanceof Error && <Alert type="error" message={statusError.message} showIcon />}
        {!loading && status && !status.configured && (
          <Alert type="warning" message={`后端 Telegram 配置缺失：${status.missing.join(", ")}`} showIcon />
        )}
        {testError instanceof Error && <Alert type="error" message={testError.message} showIcon />}
        {testSuccess && <Alert type="success" message="测试消息已发送" showIcon />}
        <div className="telegram-status-row">
          <Space wrap>
            <Tag color={status?.configured ? "green" : "orange"}>{status?.configured ? "已配置" : "未配置"}</Tag>
            <Tag color={status?.enabled ? "blue" : "default"}>{status?.enabled ? "已开启" : "已关闭"}</Tag>
            <Typography.Text type="secondary">Chat {status?.chat_id_masked || "-"}</Typography.Text>
          </Space>
          <Space className="telegram-actions">
            <Typography.Text>启用</Typography.Text>
            <Switch checked={Boolean(status?.enabled)} loading={loading || updatePending} onChange={onEnabledChange} />
            <Button loading={testPending} disabled={!status?.configured || !status?.enabled} onClick={onTest}>
              测试发送
            </Button>
          </Space>
        </div>
        <div className="telegram-table-wrap">
          <Table<NotificationDelivery>
            rowKey="id"
            size="small"
            loading={deliveriesLoading}
            dataSource={deliveries}
            pagination={false}
            scroll={{ x: 860 }}
            expandable={{
              expandedRowRender: (record) => <SignalList signals={record.signals} />,
              rowExpandable: (record) => record.signals.length > 0
            }}
            columns={[
              { title: "时间", dataIndex: "created_at", width: 170, render: formatDateTime },
              { title: "目标", dataIndex: "target_key", width: 140 },
              { title: "通知", dataIndex: "title", width: 220 },
              { title: "状态", dataIndex: "status", width: 140, render: renderSignalStatus },
              { title: "信号数", dataIndex: "signals", width: 90, render: (value: SignalRecord[]) => value.length },
              {
                title: "错误",
                dataIndex: "error",
                render: (value: string) => value || <Typography.Text type="secondary">-</Typography.Text>,
              },
            ]}
          />
        </div>
      </Space>
    </Card>
  );
}

function SignalList({ signals }: { signals: SignalRecord[] }) {
  return (
    <Space direction="vertical" size={4}>
      {signals.map((signal) => (
        <Typography.Text key={signal.id}>
          {signal.signal_label} · score {formatNumber(signal.score)} · {signal.signal_key}
        </Typography.Text>
      ))}
    </Space>
  );
}

function renderSignalStatus(status: NotificationDelivery["status"]) {
  const color = {
    sent: "green",
    skipped_disabled: "default",
    error: "red",
  }[status];
  const label = {
    sent: "已发送",
    skipped_disabled: "已关闭跳过",
    error: "错误",
  }[status];
  return <Tag color={color}>{label}</Tag>;
}

function formatDateTime(value: string) {
  return new Date(value).toLocaleString();
}

function formatNumber(value: number | null) {
  if (value === null || value === undefined) return "-";
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}
