import {
  Alert,
  Button,
  Card,
  Checkbox,
  DatePicker,
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
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
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
const MARKET_MATRIX_COLUMN_WIDTH = 260;
const MARKET_MATRIX_OVERSCAN = 3;
const { RangePicker } = DatePicker;

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
  const debouncedSearchText = useDebouncedValue(searchText, 350);

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
    queryKey: ["account-markets", selectedAccountId, debouncedSearchText, startDate, endDate, onlyBilateral],
    queryFn: ({ pageParam = 0 }) =>
      api.accountMarkets(selectedAccountId as string, {
        offset: pageParam,
        limit: MARKET_PAGE_SIZE,
        search: debouncedSearchText,
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
  const filteredMarketTotal = marketsQuery.data?.pages[0]?.total ?? 0;
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
          {summaryQuery.error instanceof Error && (
            <Button size="small" onClick={() => void summaryQuery.refetch()}>
              重试统计
            </Button>
          )}
          {summaryQuery.isLoading && <Alert type="info" message="正在加载账户统计..." showIcon />}
          <ReportStats summary={summaryQuery.data} loading={summaryQuery.isLoading} />
          <Card
            className="market-details-card"
            title={
              <div className="market-details-title">
                <Space size={8} wrap>
                  <span>市场明细</span>
                  {!marketsQuery.isLoading && <Tag color="blue">已加载 {markets.length} / {filteredMarketTotal}</Tag>}
                  {marketsQuery.isFetching && (
                    <Typography.Text type="secondary" className="inline-loading">
                      <Spin size="small" /> 加载中
                    </Typography.Text>
                  )}
                </Space>
              </div>
            }
          >
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
            {marketsQuery.error instanceof Error && (
              <Alert
                type="error"
                message={marketsQuery.error.message}
                showIcon
                action={
                  <Button size="small" onClick={() => void marketsQuery.refetch()}>
                    重试
                  </Button>
                }
              />
            )}
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

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedValue(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);

  return debouncedValue;
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
            render: (value: string) => (
              <Typography.Text
                copyable={{
                  text: value,
                  tooltips: ["复制完整钱包", "已复制"],
                }}
              >
                {shortWallet(value)}
              </Typography.Text>
            ),
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
  const dateRangeValue: [Dayjs | null, Dayjs | null] | null =
    startDate || endDate
      ? [startDate ? dayjs(startDate) : null, endDate ? dayjs(endDate) : null]
      : null;

  return (
    <div className="report-filters">
      <div className="report-filter-group report-filter-search">
        <Typography.Text type="secondary">搜索</Typography.Text>
        <Input
          size="small"
          value={searchText}
          onChange={(event) => onSearchText(event.target.value)}
          placeholder="btc / 关键词"
          allowClear
        />
      </div>
      <div className="report-filter-group report-filter-date">
        <Typography.Text type="secondary">市场日期</Typography.Text>
        <RangePicker
          size="small"
          value={dateRangeValue}
          format="YYYY-MM-DD"
          allowClear
          inputReadOnly
          placeholder={["开始日期", "结束日期"]}
          presets={[
            { label: "今天", value: [dayjs(), dayjs()] },
            { label: "最近 7 天", value: [dayjs().subtract(6, "day"), dayjs()] },
            { label: "最近 30 天", value: [dayjs().subtract(29, "day"), dayjs()] },
          ]}
          onChange={(_, dateStrings) => {
            onStartDate(dateStrings[0] || "");
            onEndDate(dateStrings[1] || "");
          }}
        />
      </div>
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
    </div>
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
  const [viewport, setViewport] = useState({ scrollLeft: 0, width: 0 });
  const rows = useMemo<MatrixRow[]>(
    () => [
      {
        key: "result",
        label: "实际结果",
        render: (market) => <ResultCell market={market} />,
        className: (market) => marketResultCellClass(market),
      },
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
        render: (market) => renderPnlRatio(market.pnl, market.roi),
      },
      {
        key: "if_up",
        label: "若上涨收益/收益率",
        render: (market) => {
          const { pnl, roi } = resolveHypotheticalValue(market, "up");
          return formatOptionalScenario(pnl, roi);
        },
        className: (market) => hypotheticalCellClass(market, "up"),
      },
      {
        key: "if_down",
        label: "若下跌收益/收益率",
        render: (market) => {
          const { pnl, roi } = resolveHypotheticalValue(market, "down");
          return formatOptionalScenario(pnl, roi);
        },
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

  useEffect(() => {
    const element = wrapRef.current;
    if (!element) return;
    const syncViewport = () => setViewport({ scrollLeft: element.scrollLeft, width: element.clientWidth });
    syncViewport();
    window.addEventListener("resize", syncViewport);
    return () => window.removeEventListener("resize", syncViewport);
  }, []);

  const virtualColumns = useMemo(() => {
    if (markets.length === 0) {
      return { visible: [], start: 0, end: 0, leftWidth: 0, rightWidth: 0 };
    }
    const availableWidth = Math.max(viewport.width - 150, MARKET_MATRIX_COLUMN_WIDTH);
    const start = Math.max(0, Math.floor(viewport.scrollLeft / MARKET_MATRIX_COLUMN_WIDTH) - MARKET_MATRIX_OVERSCAN);
    const visibleCount = Math.ceil(availableWidth / MARKET_MATRIX_COLUMN_WIDTH) + MARKET_MATRIX_OVERSCAN * 2;
    const end = Math.min(markets.length, start + visibleCount);
    return {
      visible: markets.slice(start, end),
      start,
      end,
      leftWidth: start * MARKET_MATRIX_COLUMN_WIDTH,
      rightWidth: Math.max(0, (markets.length - end) * MARKET_MATRIX_COLUMN_WIDTH),
    };
  }, [markets, viewport.scrollLeft, viewport.width]);

  function loadMoreIfNearRight(element: HTMLDivElement) {
    setViewport({ scrollLeft: element.scrollLeft, width: element.clientWidth });
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
            {virtualColumns.leftWidth > 0 && <th className="virtual-spacer" style={{ width: virtualColumns.leftWidth }} />}
            {virtualColumns.visible.map((market, index) => (
              <th key={market.market_id} data-market-title={market.title} data-market-date={market.market_date ?? ""}>
                <div className="market-column-title">
                  {virtualColumns.start + index + 1}. {market.title}
                </div>
              </th>
            ))}
            {virtualColumns.rightWidth > 0 && <th className="virtual-spacer" style={{ width: virtualColumns.rightWidth }} />}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <td>{row.label}</td>
              {virtualColumns.leftWidth > 0 && <td className="virtual-spacer" style={{ width: virtualColumns.leftWidth }} />}
              {virtualColumns.visible.map((market) => (
                <td
                  key={market.market_id}
                  className={row.className?.(market) ?? detailCellClass(row.key, market)}
                >
                  {row.render(market)}
                </td>
              ))}
              {virtualColumns.rightWidth > 0 && <td className="virtual-spacer" style={{ width: virtualColumns.rightWidth }} />}
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
  const tone = marketResultTone(market);
  const tagClass = tone === "positive" ? "up" : tone === "negative" ? "down" : "neutral";
  return (
    <div className="market-result-cell">
      <span className={`outcome-tag ${tagClass}`}>{market.result || "n/a"}</span>
    </div>
  );
}

const MARKET_VALUE_EPSILON = 1e-8;

function normalizeSignedValue(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value) || !Number.isFinite(value)) return null;
  if (Math.abs(value) < MARKET_VALUE_EPSILON) return 0;
  return value;
}

function signedTone(value: number | null | undefined): "positive" | "negative" | "neutral" {
  const normalized = normalizeSignedValue(value);
  if (normalized === null || normalized === 0) return "neutral";
  return normalized > 0 ? "positive" : "negative";
}

function isPositive(value: number | null | undefined) {
  const normalized = normalizeSignedValue(value);
  return normalized !== null && normalized > 0;
}

function isNegative(value: number | null | undefined) {
  const normalized = normalizeSignedValue(value);
  return normalized !== null && normalized < 0;
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
  const normalized = normalizeSignedValue(value) ?? value;
  const prefix = normalized >= 0 ? "+" : "-";
  return `${prefix}$${Math.abs(normalized).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

function marketResultTone(market: MarketPerformance) {
  if (market.result === "上涨" || market.result === "是") return "positive";
  if (market.result === "下跌" || market.result === "否") return "negative";
  return "neutral";
}

function marketResultCellClass(market: MarketPerformance) {
  // 标签展示官方结果，背景展示本账户在该结果下的实际盈亏，和离线报表保持一致。
  if (isPositive(market.pnl)) return "outcome-profit";
  if (isNegative(market.pnl)) return "outcome-loss";
  return "";
}

function renderMoney(value: number | null) {
  if (value === null || value === undefined) return "n/a";
  return <Typography.Text type={value < 0 ? "danger" : "success"}>{formatMoney(value)}</Typography.Text>;
}

function renderSignedMoney(value: number | null) {
  if (value === null || value === undefined) return "n/a";
  const tone = signedTone(value);
  const colorStyle = tone === "negative" ? { color: "#b42318" } : tone === "positive" ? { color: "#0f7a4f" } : undefined;
  return <span style={colorStyle}>{formatSignedMoney(value)}</span>;
}

function formatAmount(value: number | null) {
  if (value === null || value === undefined) return "n/a";
  return value.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

function formatPercent(value: number | null) {
  if (value === null || value === undefined) return "n/a";
  const normalized = normalizeSignedValue(value) ?? value;
  return `${(normalized * 100).toLocaleString("en-US", { maximumFractionDigits: 2 })}%`;
}

function renderPnlRatio(pnl: number | null, roi: number | null) {
  return (
    <>
      {renderSignedMoney(pnl)} / {formatPercent(roi)}
    </>
  );
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
  return renderPnlRatio(pnl, roi);
}

function resolveHypotheticalValue(market: MarketPerformance, side: "up" | "down") {
  const directPnl = side === "up" ? market.if_up_pnl : market.if_down_pnl;
  const directRoi = side === "up" ? market.if_up_roi : market.if_down_roi;
  if (directPnl !== null && directRoi !== null) {
    return { pnl: directPnl, roi: directRoi };
  }

  const fallbackShares = side === "up" ? market.up_shares : market.down_shares;
  const fallbackPnl = market.merge_return + fallbackShares - market.cost;
  const fallbackRoi = safeRatio(fallbackPnl, market.cost);
  if (market.cost === 0) {
    return { pnl: null, roi: null };
  }

  return { pnl: fallbackPnl, roi: fallbackRoi };
}

function toneFor(value: number | null | undefined): "positive" | "negative" | "neutral" {
  if (value === null || value === undefined || value === 0) return "neutral";
  return isPositive(value) ? "positive" : isNegative(value) ? "negative" : "neutral";
}

function detailCellClass(rowKey: string, market: MarketPerformance) {
  const classes: string[] = [];
  if (rowKey === "pnl") {
    if (isPositive(market.pnl)) classes.push("profit");
    if (isNegative(market.pnl)) classes.push("loss");
  }
  if (rowKey === "up") classes.push("up-side");
  if (rowKey === "down") classes.push("down-side");
  return classes.join(" ");
}

function hypotheticalCellClass(market: MarketPerformance, side: "up" | "down") {
  const { pnl, roi } = resolveHypotheticalValue(market, side);
  const classes: string[] = [];
  if (pnl === null || roi === null) return "";
  if (isPositive(pnl)) classes.push("profit");
  if (isNegative(pnl)) classes.push("loss");
  if (sameNumber(pnl, market.pnl) && sameNumber(roi, market.roi)) classes.push("matched-result");
  return classes.join(" ");
}

function safeRatio(numerator: number, denominator: number) {
  return denominator === 0 ? null : numerator / denominator;
}

function sameNumber(left: number | null, right: number | null) {
  if (left === null || right === null) return false;
  return Math.abs(left - right) < 0.000001;
}

function readSavedActivityLimit() {
  const saved = Number(localStorage.getItem(ACTIVITY_LIMIT_KEY));
  return Number.isFinite(saved) ? saved : 5000;
}
