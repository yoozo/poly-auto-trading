import { CloudDownloadOutlined, ReloadOutlined } from "@ant-design/icons";
import { Badge, Button, Card, Empty, Space, Table, Tag, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type SystemTaskStatus, type SystemTaskStepStatus, type SystemTaskType } from "../api/client";

const taskTypeText: Record<SystemTaskType, string> = {
  kline_backfill: "K 线下载",
  indicator_backfill: "指标计算"
};

export default function SystemTasksPage() {
  const queryClient = useQueryClient();
  const tasks = useQuery({
    queryKey: ["system-tasks"],
    queryFn: api.systemTasks,
    refetchInterval: (query) => (query.state.data?.some((task) => task.status === "running") ? 3000 : 10000)
  });
  const startTask = useMutation({
    mutationFn: api.startSystemTask,
    onSuccess: async (task) => {
      message.success(`${taskTypeLabel(task.task_type)}任务已提交`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["system-tasks"] }),
        queryClient.invalidateQueries({ queryKey: ["services"] }),
        queryClient.invalidateQueries({ queryKey: ["service-events"] })
      ]);
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : "启动任务失败");
    }
  });

  return (
    <div className="page-stack">
      <Card
        title="系统任务"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => tasks.refetch()} loading={tasks.isFetching}>
              刷新
            </Button>
            <Button
              type="primary"
              icon={<CloudDownloadOutlined />}
              loading={startTask.isPending && startTask.variables === "kline_backfill"}
              onClick={() => startTask.mutate("kline_backfill")}
            >
              启动 K 线下载
            </Button>
            <Button
              icon={<CloudDownloadOutlined />}
              loading={startTask.isPending && startTask.variables === "indicator_backfill"}
              onClick={() => startTask.mutate("indicator_backfill")}
            >
              启动指标计算
            </Button>
          </Space>
        }
      >
        <Table<SystemTaskStatus>
          rowKey={(record) => `${record.task_type}-${record.id ?? "empty"}`}
          loading={tasks.isFetching}
          dataSource={tasks.data ?? []}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无系统任务" /> }}
          expandable={{
            expandedRowRender: (record) => <TaskStepsTable task={record} />,
            rowExpandable: (record) => record.steps.length > 0
          }}
          columns={[
            {
              title: "任务类型",
              dataIndex: "task_type",
              width: 140,
              render: (value: SystemTaskType) => <Tag>{taskTypeLabel(value)}</Tag>
            },
            {
              title: "状态",
              dataIndex: "status",
              width: 120,
              render: (value: string) => <Badge status={taskStateColor(value)} text={taskStateText(value)} />
            },
            {
              title: "Symbol",
              dataIndex: "symbol",
              width: 120
            },
            {
              title: "当前 Step",
              render: (_, record) => currentStepLabel(record)
            },
            {
              title: "已写入",
              dataIndex: "total_inserted",
              render: (value: number) => value.toLocaleString()
            },
            {
              title: "Raw",
              render: (_, record) => record.steps.reduce((sum, step) => sum + step.raw_count, 0).toLocaleString()
            },
            {
              title: "开始",
              dataIndex: "started_at",
              render: formatDate
            },
            {
              title: "结束",
              dataIndex: "finished_at",
              render: formatDate
            },
            {
              title: "错误",
              dataIndex: "error",
              render: (value: string | null) => value || <Typography.Text type="secondary">-</Typography.Text>
            }
          ]}
        />
      </Card>
    </div>
  );
}

function TaskStepsTable({ task }: { task: SystemTaskStatus }) {
  return (
    <Table<SystemTaskStepStatus>
      rowKey="id"
      size="small"
      pagination={{ pageSize: 20, showSizeChanger: true }}
      dataSource={task.steps}
      columns={[
        {
          title: "周期",
          dataIndex: "interval",
          width: 100,
          render: (value: string) => <Tag>{value}</Tag>
        },
        {
          title: "状态",
          dataIndex: "status",
          width: 110,
          render: (value: string) => <Tag color={progressStateColor(value)}>{progressStateText(value)}</Tag>
        },
        {
          title: "缺口起点",
          dataIndex: "start_ms",
          render: formatTimestamp
        },
        {
          title: "游标",
          dataIndex: "cursor_ms",
          render: formatTimestamp
        },
        {
          title: "缺口终点",
          dataIndex: "end_ms",
          render: formatTimestamp
        },
        {
          title: "已写入",
          dataIndex: "inserted_count",
          render: (value: number) => value.toLocaleString()
        },
        {
          title: "Raw",
          dataIndex: "raw_count",
          render: (value: number) => value.toLocaleString()
        },
        {
          title: "错误",
          dataIndex: "last_error",
          render: (value: string) => value || <Typography.Text type="secondary">-</Typography.Text>
        }
      ]}
    />
  );
}

function taskTypeLabel(value: SystemTaskType) {
  return taskTypeText[value] ?? value;
}

function currentStepLabel(task: SystemTaskStatus) {
  const step = task.steps.find((item) => item.status === "running") ?? task.steps.find((item) => item.status === "pending" || item.status === "error");
  if (!step) return <Typography.Text type="secondary">-</Typography.Text>;
  return (
    <Space size={4}>
      <Tag>{step.interval}</Tag>
      <Typography.Text type="secondary">{formatTimestampText(step.cursor_ms)}</Typography.Text>
    </Space>
  );
}

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : <Typography.Text type="secondary">-</Typography.Text>;
}

function formatTimestamp(value: number | null) {
  return value && value > 0 ? formatTimestampText(value) : <Typography.Text type="secondary">-</Typography.Text>;
}

function formatTimestampText(value: number) {
  return new Date(value).toLocaleString();
}

function taskStateText(state: string) {
  if (state === "running") return "运行中";
  if (state === "completed") return "已完成";
  if (state === "error") return "失败";
  return "空闲";
}

function taskStateColor(state: string): "success" | "processing" | "default" | "error" {
  if (state === "running") return "processing";
  if (state === "completed") return "success";
  if (state === "error") return "error";
  return "default";
}

function progressStateText(state: string) {
  if (state === "running") return "处理中";
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
