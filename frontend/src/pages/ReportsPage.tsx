import {
  Alert,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Progress,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  api,
  type AccountSummary,
  type MarketPerformance,
  type RecentPerformance,
  type ReportAccount,
  type ReportTask,
} from "../api/client";

const ACTIVITY_LIMIT_KEY = "poly-auto.reports.activityLimit";
const SELECTED_ACCOUNT_KEY = "poly-auto.reports.selectedAccountId";
const MARKET_PAGE_SIZE = 20;
const MARKET_MATRIX_LOAD_THRESHOLD_PX = 96;

type AnalyzeForm = {
  input: string;
  activityLimit: number;
};

type MatrixRow = {
  key: string;
  label: string;
  render: (market: MarketPerformance) => ReactNode;
  className?: (market: MarketPerformance) => string;
};

export default function ReportsPage() {
  const [form] = Form.useForm<AnalyzeForm>();
  const queryClient = useQueryClient();
  const [taskId, setTaskId] = useState<string | null>(null);
  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(() => localStorage.getItem(SELECTED_ACCOUNT_KEY));
  const [searchText, setSearchText] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [onlyBilateral, setOnlyBilateral] = useState(false);

  const accountsQuery = useQuery({
    queryKey: ["report-accounts"],
    queryFn: api.reportAccounts,
  });
  const summaryQuery = useQuery({
    queryKey: ["account-summary", selectedAccountId],
    queryFn: () => api.accountSummary(selectedAccountId as string),
    enabled: Boolean(selectedAccountId),
  });
  const marketsQuery = useInfiniteQuery({
    queryKey: ["account-markets", selectedAccountId, searchText, startDate, endDate, onlyBilateral],
    queryFn: ({ pageParam = 0 }) =>
      api.accountMarkets(selectedAccountId as string, {
        offset: pageParam,
        limit: MARKET_PAGE_SIZE,
        search: searchText,
        startDate,
        endDate,
        onlyBilateral,
      }),
    enabled: Boolean(selectedAccountId),
    initialPageParam: 0,
    getNextPageParam: (lastPage) => {
      const nextOffset = lastPage.offset + lastPage.items.length;
      return nextOffset < lastPage.total ? nextOffset : undefined;
    },
  });
  const taskQuery = useQuery({
    queryKey: ["report-task", taskId],
    queryFn: () => api.reportTask(taskId as string),
    enabled: Boolean(taskId),
    refetchInterval: (query) => {
      const task = query.state.data;
      return task?.status === "running" ? 1000 : false;
    },
  });
  const analyzeMutation = useMutation({
    mutationFn: (values: AnalyzeForm) => api.analyzeAccount(values.input, values.activityLimit),
    onSuccess: (data) => setTaskId(data.task_id),
  });
  const updateAccountMutation = useMutation({
    mutationFn: ({ accountId, note }: { accountId: string; note: string }) => api.updateReportAccount(accountId, { note }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["report-accounts"] });
    },
  });

  const task = taskQuery.data;
  const isRunning = task?.status === "running" || analyzeMutation.isPending;
  const markets = useMemo(() => marketsQuery.data?.pages.flatMap((page) => page.items) ?? [], [marketsQuery.data]);
  const loadNextMarketPage = useCallback(() => {
    if (!marketsQuery.hasNextPage || marketsQuery.isFetchingNextPage) return;
    void marketsQuery.fetchNextPage();
  }, [marketsQuery.fetchNextPage, marketsQuery.hasNextPage, marketsQuery.isFetchingNextPage]);

  useEffect(() => {
    if (task?.status === "done") {
      void queryClient.invalidateQueries({ queryKey: ["report-accounts"] });
      if (typeof task.result.account_id === "string") {
        setSelectedAccountId(task.result.account_id);
        void queryClient.invalidateQueries({ queryKey: ["account-summary", task.result.account_id] });
        void queryClient.invalidateQueries({ queryKey: ["account-markets", task.result.account_id] });
      }
    }
  }, [queryClient, task?.status]);

  useEffect(() => {
    if (!selectedAccountId && accountsQuery.data?.[0]) {
      setSelectedAccountId(accountsQuery.data[0].id);
      localStorage.setItem(SELECTED_ACCOUNT_KEY, accountsQuery.data[0].id);
    } else if (selectedAccountId && accountsQuery.data && !accountsQuery.data.some((account) => account.id === selectedAccountId)) {
      const nextAccountId = accountsQuery.data[0]?.id ?? null;
      if (nextAccountId) {
        setSelectedAccountId(nextAccountId);
        localStorage.setItem(SELECTED_ACCOUNT_KEY, nextAccountId);
      }
    }
  }, [accountsQuery.data, selectedAccountId]);

  return (
    <div className="page-stack reports-page">
      <Card>
        <Form
          form={form}
          layout="inline"
          initialValues={{ activityLimit: readSavedActivityLimit() }}
          onFinish={(values) => {
            localStorage.setItem(ACTIVITY_LIMIT_KEY, String(values.activityLimit));
            analyzeMutation.mutate(values);
          }}
        >
          <Form.Item
            name="input"
            rules={[{ required: true, message: "请输入 profile、URL 或钱包地址" }]}
            className="reports-input-item"
          >
            <Input placeholder="@profile / profile URL / 0x wallet" allowClear />
          </Form.Item>
          <Form.Item
            name="activityLimit"
            rules={[{ required: true, message: "请输入下载数量" }]}
          >
            <InputNumber step={100} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={isRunning}>
              开始分析
            </Button>
          </Form.Item>
        </Form>
      </Card>

      {(task || analyzeMutation.error || taskQuery.error) && (
        <Card>
          <Space direction="vertical" size={10} className="reports-task-panel">
            {task && (
              <>
                <Space wrap>
                  <Typography.Text strong>任务 {task.id.slice(0, 8)}</Typography.Text>
                  <TaskStatusTag status={task.status} />
                  <Typography.Text type="secondary">{task.message}</Typography.Text>
                </Space>
                <Progress percent={task.percent} status={task.status === "error" ? "exception" : undefined} />
                {task.status === "done" && <TaskResult task={task} />}
                {task.status === "error" && <Alert type="error" message={task.error || "任务失败"} showIcon />}
              </>
            )}
            {analyzeMutation.error instanceof Error && <Alert type="error" message={analyzeMutation.error.message} showIcon />}
            {taskQuery.error instanceof Error && <Alert type="error" message={taskQuery.error.message} showIcon />}
          </Space>
        </Card>
      )}

      <Card title="本地账号" styles={{ body: { paddingTop: 8 } }}>
        <AccountPicker
          loading={accountsQuery.isLoading}
          accounts={accountsQuery.data ?? []}
          selectedAccountId={selectedAccountId}
          onSelectedAccountId={setSelectedAccountId}
          onUpdateNote={(accountId, note) => updateAccountMutation.mutate({ accountId, note })}
          updatingAccountId={updateAccountMutation.variables?.accountId ?? null}
        />
      </Card>

      {selectedAccountId && (
        <>
          {summaryQuery.error instanceof Error && <Alert type="error" message={summaryQuery.error.message} showIcon />}
          {summaryQuery.isLoading && <Alert type="info" message="正在加载账户统计..." showIcon />}
          <ReportStats summary={summaryQuery.data} loading={summaryQuery.isLoading} />
          <Card
            className="market-details-card"
            title={
              <Space size={8}>
                <span>市场明细</span>
                {marketsQuery.isFetching && (
                  <Typography.Text type="secondary" className="inline-loading">
                    <Spin size="small" /> 加载中
                  </Typography.Text>
                )}
              </Space>
            }
            extra={
              <ReportFilters
                searchText={searchText}
                startDate={startDate}
                endDate={endDate}
                onlyBilateral={onlyBilateral}
                onSearchText={setSearchText}
                onStartDate={setStartDate}
                onEndDate={setEndDate}
                onOnlyBilateral={setOnlyBilateral}
              />
            }
          >
            {marketsQuery.error instanceof Error && <Alert type="error" message={marketsQuery.error.message} showIcon />}
            <MarketMatrix
              loading={marketsQuery.isLoading}
              markets={markets}
              hasMore={marketsQuery.hasNextPage}
              loadingMore={marketsQuery.isFetchingNextPage}
              onLoadMore={loadNextMarketPage}
            />
          </Card>
        </>
      )}
    </div>
  );
}

function ReportStats({ summary, loading }: { summary: AccountSummary | undefined; loading: boolean }) {
  const recentByDays = new Map((summary?.recent ?? []).map((item) => [item.days, item]));
  return (
    <div className="report-stats">
      <div className="report-stats-main">
        <MetricCard
          loading={loading}
          label="全部收益"
          value={formatSignedMoney(summary?.total_pnl ?? 0)}
          tone={toneFor(summary?.total_pnl)}
        />
        <MetricCard
          loading={loading}
          label="含 rebate 收益"
          value={formatSignedMoney(summary?.total_pnl_with_rebate ?? 0)}
          tone={toneFor(summary?.total_pnl_with_rebate)}
        />
        <MetricCard
          loading={loading}
          label="平均/中位/最大成本"
          value={`${formatMoney(summary?.average_cost ?? 0)} / ${formatMoney(summary?.median_cost ?? 0)} / ${formatMoney(summary?.max_cost ?? 0)}`}
          tone="neutral"
        />
        <MetricCard
          loading={loading}
          label="🚀 胜率"
          value={formatPercent(summary?.win_rate ?? null)}
          tone={summary?.win_rate && summary.win_rate >= 0.5 ? "positive" : "neutral"}
        />
      </div>
      <div className="report-stats-recent">
        {[1, 3, 7, 14, 30].map((days) => (
          <RecentMetricCard key={days} loading={loading} days={days} item={recentByDays.get(days)} />
        ))}
      </div>
    </div>
  );
}

function AccountPicker({
  loading,
  accounts,
  selectedAccountId,
  onSelectedAccountId,
  onUpdateNote,
  updatingAccountId,
}: {
  loading: boolean;
  accounts: ReportAccount[];
  selectedAccountId: string | null;
  onSelectedAccountId: (value: string) => void;
  onUpdateNote: (accountId: string, note: string) => void;
  updatingAccountId: string | null;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingNote, setEditingNote] = useState("");
  return (
    <div className="account-table-wrap">
      <Table<ReportAccount>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={accounts}
        pagination={{ pageSize: 5, size: "small" }}
        scroll={{ x: 940 }}
        rowSelection={{
          type: "radio",
          selectedRowKeys: selectedAccountId ? [selectedAccountId] : [],
          onChange: ([key]) => {
            const accountId = String(key);
            localStorage.setItem(SELECTED_ACCOUNT_KEY, accountId);
            onSelectedAccountId(accountId);
          },
        }}
        onRow={(record) => ({
          onClick: () => {
            localStorage.setItem(SELECTED_ACCOUNT_KEY, record.id);
            onSelectedAccountId(record.id);
          },
        })}
        columns={[
          {
            title: "标签",
            dataIndex: "note",
            width: 220,
            render: (value: string, record) => {
              const isEditing = editingId === record.id;
              if (isEditing) {
                return (
                  <Space.Compact>
                    <Input
                      size="small"
                      value={editingNote}
                      maxLength={255}
                      onClick={(event) => event.stopPropagation()}
                      onChange={(event) => setEditingNote(event.target.value)}
                      onPressEnter={(event) => {
                        event.stopPropagation();
                        onUpdateNote(record.id, editingNote.trim());
                        setEditingId(null);
                      }}
                    />
                    <Button
                      size="small"
                      loading={updatingAccountId === record.id}
                      onClick={(event) => {
                        event.stopPropagation();
                        onUpdateNote(record.id, editingNote.trim());
                        setEditingId(null);
                      }}
                    >
                      保存
                    </Button>
                  </Space.Compact>
                );
              }
              return (
                <button
                  type="button"
                  className="account-note-button"
                  onClick={(event) => {
                    event.stopPropagation();
                    setEditingId(record.id);
                    setEditingNote(value || "");
                  }}
                >
                  {value || "添加标签"}
                </button>
              );
            },
          },
          {
            title: "用户",
            dataIndex: "normalized_user",
            width: 260,
            render: (value: string) => <Typography.Text strong>{value}</Typography.Text>,
          },
          {
            title: "钱包",
            dataIndex: "proxy_wallet",
            width: 220,
            render: (value: string) => <Typography.Text copyable>{shortWallet(value)}</Typography.Text>,
          },
          { title: "Activity", dataIndex: "activity_count", width: 110 },
          { title: "最后下载", dataIndex: "last_downloaded_at", width: 180, render: formatDate },
          { title: "最新 Activity", dataIndex: "latest_activity_at", width: 180, render: formatDate },
        ]}
      />
    </div>
  );
}

function RecentMetricCard({
  loading,
  days,
  item,
}: {
  loading: boolean;
  days: number;
  item: RecentPerformance | undefined;
}) {
  return (
    <MetricCard
      loading={loading}
      label={`最近 ${days} 天收益`}
      value={`${formatSignedMoney(item?.pnl ?? 0)} / ${formatPercent(item?.roi ?? null)}`}
      tone={toneFor(item?.pnl)}
    />
  );
}

function MetricCard({
  loading,
  label,
  value,
  tone,
}: {
  loading: boolean;
  label: string;
  value: string;
  tone: "positive" | "negative" | "neutral";
}) {
  return (
    <Card loading={loading} className={`report-metric-card report-metric-${tone}`} styles={{ body: { padding: 12 } }}>
      <div className="report-metric-label">{label}</div>
      <div className="report-metric-value">{value}</div>
    </Card>
  );
}

function ReportFilters({
  searchText,
  startDate,
  endDate,
  onlyBilateral,
  onSearchText,
  onStartDate,
  onEndDate,
  onOnlyBilateral,
}: {
  searchText: string;
  startDate: string;
  endDate: string;
  onlyBilateral: boolean;
  onSearchText: (value: string) => void;
  onStartDate: (value: string) => void;
  onEndDate: (value: string) => void;
  onOnlyBilateral: (value: boolean) => void;
}) {
  return (
    <Space wrap size={8} className="report-filters">
      <Typography.Text type="secondary">搜索</Typography.Text>
      <Input
        size="small"
        value={searchText}
        onChange={(event) => onSearchText(event.target.value)}
        placeholder="btc / 关键词"
        allowClear
      />
      <Typography.Text type="secondary">开始</Typography.Text>
      <Input size="small" type="date" value={startDate} onChange={(event) => onStartDate(event.target.value)} />
      <Typography.Text type="secondary">结束</Typography.Text>
      <Input size="small" type="date" value={endDate} onChange={(event) => onEndDate(event.target.value)} />
      <Button
        size="small"
        onClick={() => {
          onStartDate("");
          onEndDate("");
        }}
      >
        清除日期
      </Button>
      <Checkbox checked={onlyBilateral} onChange={(event) => onOnlyBilateral(event.target.checked)}>
        只显示双向份额
      </Checkbox>
    </Space>
  );
}

function MarketMatrix({
  loading,
  markets,
  hasMore,
  loadingMore,
  onLoadMore,
}: {
  loading: boolean;
  markets: MarketPerformance[];
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const rows = useMemo<MatrixRow[]>(
    () => [
      { key: "result", label: "实际结果", render: (market) => <ResultCell market={market} /> },
      { key: "position", label: "持仓状态", render: (market) => <Tag>{market.position_status}</Tag> },
      { key: "redeem_time", label: "Redeem time", render: (market) => formatDate(market.redeem_time) },
      { key: "market_date", label: "市场日期", render: (market) => formatDate(market.market_date) },
      { key: "activity_count", label: "交易数", render: (market) => market.activity_count },
      { key: "redeem_count", label: "Redeem count", render: (market) => market.redeem_count },
      { key: "merge_count", label: "Merge count", render: (market) => market.merge_count },
      {
        key: "up",
        label: "上涨成本/份额/平均成本",
        render: (market) => renderOutcomeStats(market.up_cost, market.up_shares, market.up_average_cost, "positive"),
      },
      {
        key: "down",
        label: "下跌成本/份额/平均成本",
        render: (market) => renderOutcomeStats(market.down_cost, market.down_shares, market.down_average_cost, "negative"),
      },
      { key: "recovery_cost", label: "回收 / 成本", render: (market) => <>{renderMoney(market.recovery)} / {formatMoney(market.cost)}</> },
      { key: "merge_return", label: "Merge return", render: (market) => formatMoney(market.merge_return) },
      {
        key: "pnl",
        label: "收益/收益率",
        render: (market) => (
          <>
            {renderSignedMoney(market.pnl)} / {formatPercent(market.roi)}
          </>
        ),
      },
      {
        key: "if_up",
        label: "若上涨收益/收益率",
        render: (market) => formatOptionalScenario(market.if_up_pnl, market.if_up_roi),
        className: (market) => hypotheticalCellClass(market, "up"),
      },
      {
        key: "if_down",
        label: "若下跌收益/收益率",
        render: (market) => formatOptionalScenario(market.if_down_pnl, market.if_down_roi),
        className: (market) => hypotheticalCellClass(market, "down"),
      },
    ],
    [],
  );

  useEffect(() => {
    const element = wrapRef.current;
    if (!element || !hasMore || loadingMore) return;
    if (element.scrollWidth <= element.clientWidth + MARKET_MATRIX_LOAD_THRESHOLD_PX) {
      onLoadMore();
    }
  }, [hasMore, loadingMore, markets.length, onLoadMore]);

  function loadMoreIfNearRight(element: HTMLDivElement) {
    if (!hasMore || loadingMore) return;
    const distanceToRight = element.scrollWidth - element.scrollLeft - element.clientWidth;
    if (distanceToRight <= MARKET_MATRIX_LOAD_THRESHOLD_PX) {
      onLoadMore();
    }
  }

  if (loading && markets.length === 0) {
    return (
      <div className="matrix-loading" aria-busy="true">
        <Spin />
        <Typography.Text type="secondary">正在加载市场明细...</Typography.Text>
      </div>
    );
  }

  return (
    <div
      className="table-wrap wide market-matrix-wrap"
      aria-busy={loading}
      ref={wrapRef}
      onScroll={(event) => loadMoreIfNearRight(event.currentTarget)}
    >
      <table className="market-matrix-table">
        <thead>
          <tr>
            <th>市场</th>
            {markets.map((market, index) => (
              <th key={market.market_id} data-market-title={market.title} data-market-date={market.market_date ?? ""}>
                <div className="market-column-title">
                  {index + 1}. {market.title}
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td>{row.label}</td>
              {markets.map((market) => (
                <td key={market.market_id} className={row.className?.(market) ?? detailCellClass(row.key, market)}>
                  {row.render(market)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!loading && markets.length === 0 && <div className="matrix-empty">暂无市场</div>}
      {!loading && markets.length > 0 && loadingMore && (
        <div className="matrix-loading-footer">
          <Spin size="small" /> 加载下一页...
        </div>
      )}
    </div>
  );
}

function ResultCell({ market }: { market: MarketPerformance }) {
  const isSettled = market.result !== "未结算";
  const tagClass = market.result === "上涨" || market.result === "是" ? "up" : market.result === "下跌" || market.result === "否" ? "down" : "neutral";
  return (
    <div className={isSettled ? "market-result-cell market-result-settled" : "market-result-cell market-result-open"}>
      <span className={`outcome-tag ${tagClass}`}>{market.result || "n/a"}</span>
    </div>
  );
}

function TaskStatusTag({ status }: { status: ReportTask["status"] }) {
  if (status === "done") return <Tag color="success">完成</Tag>;
  if (status === "error") return <Tag color="error">失败</Tag>;
  return <Tag color="processing">运行中</Tag>;
}

function TaskResult({ task }: { task: ReportTask }) {
  const downloaded = Number(task.result.downloaded_count ?? 0);
  const total = Number(task.result.total_activity_count ?? 0);
  return (
    <Alert
      type="success"
      showIcon
      message={`下载 ${downloaded.toLocaleString()} 条，本地累计 ${total.toLocaleString()} 条`}
    />
  );
}

function shortWallet(wallet: string) {
  if (wallet.length <= 14) return wallet;
  return `${wallet.slice(0, 8)}...${wallet.slice(-6)}`;
}

function formatDate(value: string | null) {
  if (!value) return "n/a";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function formatMoney(value: number | null) {
  if (value === null || value === undefined) return "n/a";
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

function formatSignedMoney(value: number) {
  const prefix = value >= 0 ? "+" : "-";
  return `${prefix}$${Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

function renderMoney(value: number | null) {
  if (value === null || value === undefined) return "n/a";
  return <Typography.Text type={value < 0 ? "danger" : "success"}>{formatMoney(value)}</Typography.Text>;
}

function renderSignedMoney(value: number | null) {
  if (value === null || value === undefined) return "n/a";
  return <Typography.Text type={value < 0 ? "danger" : "success"}>{formatSignedMoney(value)}</Typography.Text>;
}

function formatAmount(value: number | null) {
  if (value === null || value === undefined) return "n/a";
  return value.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

function formatPercent(value: number | null) {
  if (value === null || value === undefined) return "n/a";
  return `${(value * 100).toLocaleString("en-US", { maximumFractionDigits: 2 })}%`;
}

function renderOutcomeStats(cost: number, shares: number, averageCost: number | null, tone: "positive" | "negative") {
  return (
    <Typography.Text type={tone === "negative" ? "danger" : "success"}>
      {formatMoney(cost)} / {formatAmount(shares)} / {averageCost === null ? "n/a" : formatMoney(averageCost)}
    </Typography.Text>
  );
}

function formatOptionalScenario(pnl: number | null, roi: number | null) {
  if (pnl === null || roi === null) return "不适用";
  return (
    <>
      {renderSignedMoney(pnl)} / {formatPercent(roi)}
    </>
  );
}

function toneFor(value: number | null | undefined): "positive" | "negative" | "neutral" {
  if (value === null || value === undefined || value === 0) return "neutral";
  return value > 0 ? "positive" : "negative";
}

function detailCellClass(rowKey: string, market: MarketPerformance) {
  const classes: string[] = [];
  if (rowKey === "result") {
    if (market.pnl > 0) classes.push("outcome-profit");
    if (market.pnl < 0) classes.push("outcome-loss");
  }
  if (rowKey === "up") classes.push("up-side");
  if (rowKey === "down") classes.push("down-side");
  return classes.join(" ");
}

function hypotheticalCellClass(market: MarketPerformance, side: "up" | "down") {
  const pnl = side === "up" ? market.if_up_pnl : market.if_down_pnl;
  const roi = side === "up" ? market.if_up_roi : market.if_down_roi;
  const classes: string[] = [];
  if (pnl === null || roi === null) return "";
  if (pnl > 0) classes.push("profit");
  if (pnl < 0) classes.push("loss");
  if (sameNumber(pnl, market.pnl) && sameNumber(roi, market.roi)) classes.push("matched-result");
  return classes.join(" ");
}

function sameNumber(left: number | null, right: number | null) {
  if (left === null || right === null) return false;
  return Math.abs(left - right) < 0.000001;
}

function readSavedActivityLimit() {
  const saved = Number(localStorage.getItem(ACTIVITY_LIMIT_KEY));
  return Number.isFinite(saved) ? saved : 5000;
}
