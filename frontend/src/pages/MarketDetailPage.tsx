import { ArrowLeftOutlined, ExportOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Spin, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo } from "react";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  type MarketPerformance,
  type ReportMarketActivity,
  type ReportMarketDetail,
  type ReportMarketMetadata,
} from "../api/client";

type MarketDetailPageProps = {
  accountId: string | null;
  marketId: string | null;
  onBack: () => void;
};

type ReplayPosition = {
  shares: number;
  cost: number;
  buyShares: number;
  buyCost: number;
};

type ReplayState = {
  buyCost: number;
  sellReturn: number;
  merged: number;
  redeemed: number;
  returned: number;
  tradeCount: number;
  mergeCount: number;
  redeemCount: number;
  splitCost: number;
  positions: Record<"up" | "down", ReplayPosition>;
  ambiguousRedeem: boolean;
};

type TimelineRow = {
  key: string;
  timestamp: string;
  type: string;
  side: string | null;
  outcome: string | null;
  price: number | null;
  amount: number;
  signedAmount: number;
  upDisplay: PositionDisplay | null;
  downDisplay: PositionDisplay | null;
  pnl: number;
  transactionHash: string | null;
};

type PositionDisplay = {
  average: number | null;
  shares: number;
  averageDirection: "up" | "down" | "flat";
  shareDelta: number;
};

type ReplayResult = {
  summary: ReplayState;
  timeline: TimelineRow[];
};

const DUST_SHARES = 0.01;
const EMPTY_STATE: ReplayState = {
  buyCost: 0,
  sellReturn: 0,
  merged: 0,
  redeemed: 0,
  returned: 0,
  tradeCount: 0,
  mergeCount: 0,
  redeemCount: 0,
  splitCost: 0,
  positions: {
    up: { shares: 0, cost: 0, buyShares: 0, buyCost: 0 },
    down: { shares: 0, cost: 0, buyShares: 0, buyCost: 0 },
  },
  ambiguousRedeem: false,
};

export default function MarketDetailPage({ accountId, marketId, onBack }: MarketDetailPageProps) {
  const missingParams = !accountId || !marketId;
  const detailQuery = useQuery({
    queryKey: ["account-market-detail", accountId, marketId],
    queryFn: () => api.accountMarketDetail(accountId as string, marketId as string),
    enabled: !missingParams,
  });
  const closedOutcome = useMemo(
    () => (detailQuery.data ? closedOutcomeKey(detailQuery.data.market, detailQuery.data.metadata) : null),
    [detailQuery.data],
  );
  const replay = useMemo(() => replayActivities(detailQuery.data?.activities ?? [], closedOutcome), [detailQuery.data?.activities, closedOutcome]);

  if (missingParams) {
    return (
      <div className="page-stack market-detail-page">
        <Button icon={<ArrowLeftOutlined />} onClick={onBack}>
          返回收益报表
        </Button>
        <Alert type="error" showIcon message="URL 缺少 account 或 market 参数" />
      </div>
    );
  }

  if (detailQuery.isLoading) {
    return (
      <div className="market-detail-loading">
        <Spin />
        <Typography.Text type="secondary">正在加载市场明细...</Typography.Text>
      </div>
    );
  }

  if (detailQuery.error instanceof Error) {
    return (
      <div className="page-stack market-detail-page">
        <Button icon={<ArrowLeftOutlined />} onClick={onBack}>
          返回收益报表
        </Button>
        <Alert type="error" showIcon message={detailQuery.error.message} />
      </div>
    );
  }

  const detail = detailQuery.data;
  if (!detail) return null;

  return (
    <div className="page-stack market-detail-page">
      <MarketDetailHeader detail={detail} onBack={onBack} />
      <MarketSummary detail={detail} replay={replay} />
      <TimelineTable rows={replay.timeline} />
    </div>
  );
}

function MarketDetailHeader({ detail, onBack }: { detail: ReportMarketDetail; onBack: () => void }) {
  const market = detail.market;
  const firstActivity = detail.activities[0];
  const lastActivity = detail.activities.at(-1);
  return (
    <Card className="market-detail-header-card">
      <div className="market-detail-topbar">
        <Button className="market-detail-back-button" icon={<ArrowLeftOutlined />} onClick={onBack} aria-label="返回收益报表" />
        <div className="market-detail-title-block">
          <Typography.Title level={3} className="market-detail-title">
            {market.title}
          </Typography.Title>
          <div className="market-detail-subtitle">
            {formatDate(firstActivity?.timestamp ?? null)} 至 {formatDate(lastActivity?.timestamp ?? null)} · {detail.activities.length} 条 activity
          </div>
        </div>
      </div>
    </Card>
  );
}

function MarketSummary({ detail, replay }: { detail: ReportMarketDetail; replay: ReplayResult }) {
  const { market, metadata } = detail;
  const summary = replay.summary;
  const pnl = summary.returned - summary.buyCost;
  const roi = safeRatio(pnl, summary.buyCost);
  const closedOutcome = closedOutcomeKey(market, metadata);
  const positionStatus = formatCurrentPosition(summary, closedOutcome);
  const currentShares = currentDisplayShares(summary, closedOutcome);

  return (
    <>
      <div className="market-detail-metrics">
        <DetailMetric
          label="实际结果"
          value={<OutcomePill value={resolveResultLabel(market, metadata)} />}
          tone={toneFor(pnl)}
          result
        />
        <DetailMetric
          label="持仓状态"
          value={<OutcomePill value={positionStatus} />}
          tone="neutral"
          result
        />
        <DetailMetric label="收益" value={formatSignedMoney(pnl)} tone={toneFor(pnl)} />
        <DetailMetric label="收益率" value={formatPercent(roi)} tone={toneFor(pnl)} />
        <DetailMetric label="总成本" value={formatMoney(summary.buyCost)} tone="neutral" />
        <DetailMetric label="回收金额" value={formatMoney(summary.returned)} tone="neutral" />
      </div>
      <div className="market-detail-subsummary">
        <SummaryItem label="交易数" value={String(summary.tradeCount)} />
        <SummaryItem label="Merge" value={String(summary.mergeCount)} />
        <SummaryItem label="Redeem" value={String(summary.redeemCount)} />
        <SummaryItem label="当前持仓" value={currentShares} />
        <SummaryItem label="Up" value={formatSharesCost(summary.positions.up.buyShares, summary.positions.up.buyCost)} valueClass="profit" />
        <SummaryItem label="Down" value={formatSharesCost(summary.positions.down.buyShares, summary.positions.down.buyCost)} valueClass="loss" />
      </div>
    </>
  );
}

function SummaryItem({ label, value, valueClass }: { label: string; value: string; valueClass?: "profit" | "loss" }) {
  return (
    <span className="market-detail-summary-item">
      <span>{label}</span>
      <strong className={valueClass}>{value}</strong>
    </span>
  );
}

function DetailMetric({
  label,
  value,
  tone,
  result = false,
}: {
  label: string;
  value: ReactNode;
  tone: "positive" | "negative" | "neutral" | "info";
  result?: boolean;
}) {
  return (
    <Card
      className={`market-detail-metric ${result ? "market-detail-result-metric" : ""} market-detail-metric-${tone}`}
      styles={{ body: { padding: 12 } }}
    >
      <div className="report-metric-label">{label}</div>
      <div className="report-metric-value">{value}</div>
    </Card>
  );
}

function OutcomePill({ value }: { value: string }) {
  const klass = outcomeTagClass(value);
  return <span className={`pill ${klass}`}>{value || "n/a"}</span>;
}

function TimelineTable({ rows }: { rows: TimelineRow[] }) {
  const columns: ColumnsType<TimelineRow> = [
    {
      title: "时间",
      dataIndex: "timestamp",
      fixed: "left",
      width: 190,
      render: (value: string) => formatDate(value),
    },
    {
      title: "类型",
      width: 170,
      render: (_, row) => <ActivityPill row={row} />,
    },
    {
      title: "价格/金额",
      width: 150,
      align: "right",
      render: (_, row) => (
        <span className={`timeline-action ${timelineActionClass(row.type, row.side)}`}>
          <span className="timeline-action-detail">
            {formatCents(row.price)} / <span className="timeline-action-amount">{formatSignedMoney(row.signedAmount)}</span>
          </span>
        </span>
      ),
    },
    {
      title: "Up 均价/持仓",
      width: 190,
      align: "right",
      render: (_, row) => <PositionCell display={row.upDisplay} />,
    },
    {
      title: "Down 均价/持仓",
      width: 190,
      align: "right",
      render: (_, row) => <PositionCell display={row.downDisplay} />,
    },
    {
      title: "累计收益",
      dataIndex: "pnl",
      width: 130,
      align: "right",
      render: (value: number) => <span className={value >= 0 ? "timeline-positive" : "timeline-negative"}>{formatSignedMoney(value)}</span>,
    },
    {
      title: "链上",
      dataIndex: "transactionHash",
      width: 90,
      align: "right",
      render: (value: string | null) =>
        value ? (
          <a href={transactionUrl(value)} target="_blank" rel="noreferrer">
            tx <ExportOutlined />
          </a>
        ) : (
          "n/a"
        ),
    },
  ];

  return (
    <Card className="market-detail-timeline-card" styles={{ body: { paddingTop: 0 } }}>
      <div className="market-detail-timeline-header">
        <Typography.Title level={4} className="market-detail-section-title">
          交易时间线
        </Typography.Title>
        <span className="market-detail-timeline-count">{rows.length} 条</span>
      </div>
      <div className="market-detail-table-wrap">
        <Table<TimelineRow>
          rowKey="key"
          size="small"
          columns={columns}
          dataSource={rows}
          pagination={false}
          scroll={{ x: 980 }}
          rowClassName={(_, index) => (index % 2 === 1 ? "timeline-row-alt" : "")}
        />
      </div>
    </Card>
  );
}

function ActivityPill({ row }: { row: TimelineRow }) {
  const type = row.type.toUpperCase();
  const side = row.side?.toUpperCase() ?? "";
  const className =
    type === "TRADE"
      ? side === "SELL"
        ? "sell"
        : side === "BUY"
          ? "buy"
          : ""
      : type === "MERGE" || type === "REDEEM" || type === "SPLIT"
        ? "other"
        : "";
  return <span className={`pill ${className}`}>{typeLabel(type, side, row.outcome)}</span>;
}

function PositionCell({ display }: { display: PositionDisplay | null }) {
  if (!display) return "n/a";
  const arrow = display.averageDirection === "up" ? "↑" : display.averageDirection === "down" ? "↓" : "";
  const delta =
    Math.abs(display.shareDelta) > 1e-8
      ? `(${display.shareDelta > 0 ? "↑" : "↓"}${formatShares(Math.abs(display.shareDelta))})`
      : "";
  const mainDirection =
    display.averageDirection !== "flat" ? display.averageDirection : display.shareDelta > 0 ? "up" : display.shareDelta < 0 ? "down" : "";
  return (
    <span className="share-cell">
      <span className={`value-change ${mainDirection}`}>
        {arrow && <span className={`average-change ${display.averageDirection}`}>{arrow}</span>}
        {formatAveragePrice(display.average)} / {formatShares(display.shares)}
      </span>
      {delta && <span className={`share-change ${display.shareDelta > 0 ? "up" : "down"}`}>{delta}</span>}
    </span>
  );
}

function replayActivities(activities: ReportMarketActivity[], settledOutcome: "up" | "down" | null = null): ReplayResult {
  const state = cloneState(EMPTY_STATE);
  const timeline: TimelineRow[] = [];
  const ordered = [...activities].sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());

  for (const activity of ordered) {
    const previous = cloneState(state);
    const type = activity.type.toUpperCase();
    const side = activity.side?.toUpperCase() ?? "";
    const outcome = normalizeOutcome(activity.outcome);
    const amount = safeNumber(activity.usdc_size);
    const size = safeNumber(activity.size);

    if (type === "TRADE") {
      state.tradeCount += 1;
      if (side === "BUY") {
        state.buyCost += amount;
        applyBuy(state, outcome, size, amount);
      } else if (side === "SELL") {
        state.sellReturn += amount;
        state.returned += amount;
        reducePosition(state, outcome, size);
      }
    } else if (type === "SPLIT") {
      state.buyCost += amount;
      state.splitCost += amount;
      for (const splitOutcome of ["up", "down"] as const) {
        applyBuy(state, splitOutcome, size, amount / 2);
      }
    } else if (type === "MERGE") {
      state.mergeCount += 1;
      state.merged += amount;
      state.returned += amount;
      reducePosition(state, "up", size);
      reducePosition(state, "down", size);
    } else if (type === "REDEEM") {
      state.redeemCount += 1;
      state.redeemed += amount;
      state.returned += amount;
      const redeemShares = redeemShareSize(size, amount);
      const redeemOutcome = settledOutcome ?? inferRedeemOutcome(previous, redeemShares);
      if (redeemOutcome) {
        reducePosition(state, redeemOutcome, redeemShares);
        expireLosingOutcome(state, redeemOutcome);
      } else {
        state.ambiguousRedeem = true;
        state.positions.up.shares = 0;
        state.positions.down.shares = 0;
      }
    }

    const signedAmount = signedActivityAmount(type, side, amount);
    timeline.push({
      key: activity.id,
      timestamp: activity.timestamp,
      type,
      side: activity.side,
      outcome: activity.outcome,
      price: activity.price,
      amount,
      signedAmount,
      upDisplay: buildPositionDisplay(previous.positions.up, state.positions.up),
      downDisplay: buildPositionDisplay(previous.positions.down, state.positions.down),
      pnl: state.returned - state.buyCost,
      transactionHash: activityTransactionReference(activity),
    });
  }

  return { summary: state, timeline };
}

function applyBuy(state: ReplayState, outcome: string | null, shares: number, cost: number) {
  if (outcome !== "up" && outcome !== "down") return;
  const position = state.positions[outcome];
  position.shares += shares;
  position.cost += cost;
  position.buyShares += shares;
  position.buyCost += cost;
}

function reducePosition(state: ReplayState, outcome: string | null, removedShares: number) {
  if (outcome !== "up" && outcome !== "down") return;
  const position = state.positions[outcome];
  const sharesBefore = position.shares;
  if (sharesBefore <= 0) return;
  const remainingRaw = Math.max(0, sharesBefore - removedShares);
  const remainingShares = remainingRaw < DUST_SHARES ? 0 : remainingRaw;
  position.cost = position.cost * (remainingShares / sharesBefore);
  position.shares = remainingShares;
}

function expireLosingOutcome(state: ReplayState, winningOutcome: "up" | "down") {
  const losingOutcome = winningOutcome === "up" ? "down" : "up";
  state.positions[losingOutcome].shares = 0;
  state.positions[losingOutcome].cost = 0;
}

function redeemShareSize(size: number, amount: number) {
  return size > DUST_SHARES ? size : amount;
}

function inferRedeemOutcome(state: ReplayState, size: number): "up" | "down" | null {
  if (size <= 0) return null;
  const upDistance = Math.abs(state.positions.up.shares - size);
  const downDistance = Math.abs(state.positions.down.shares - size);
  return upDistance <= downDistance ? "up" : "down";
}

function buildPositionDisplay(previous: ReplayPosition, next: ReplayPosition): PositionDisplay | null {
  const average = next.shares > 0 ? next.cost / next.shares : null;
  const previousAverage = previous.shares > 0 ? previous.cost / previous.shares : null;
  const shareDelta = next.shares - previous.shares;
  let averageDirection: PositionDisplay["averageDirection"] = "flat";
  if (average !== null && previousAverage !== null) {
    if (average > previousAverage + 1e-8) averageDirection = "up";
    if (average < previousAverage - 1e-8) averageDirection = "down";
  }
  return { average, shares: next.shares, averageDirection, shareDelta };
}

function cloneState(state: ReplayState): ReplayState {
  return {
    ...state,
    positions: {
      up: { ...state.positions.up },
      down: { ...state.positions.down },
    },
  };
}

function signedActivityAmount(type: string, side: string, amount: number) {
  if (type === "TRADE" && side === "BUY") return -amount;
  if (type === "SPLIT") return -amount;
  if (type === "TRADE" && side === "SELL") return amount;
  if (type === "MERGE" || type === "REDEEM" || type === "MAKER_REBATE") return amount;
  return 0;
}

function resolveResultLabel(market: MarketPerformance, metadata: ReportMarketMetadata | null) {
  if (metadata?.closed && metadata.outcome) return displayOutcome(normalizeOutcome(metadata.outcome) ?? metadata.outcome);
  if (metadata?.closed && metadata.raw_outcome) return displayOutcome(normalizeOutcome(metadata.raw_outcome) ?? metadata.raw_outcome);
  return market.result || "未结算";
}

function resultTone(market: MarketPerformance, metadata: ReportMarketMetadata | null): "positive" | "negative" | "neutral" {
  const label = resolveResultLabel(market, metadata);
  if (label === "上涨" || label === "是") return "positive";
  if (label === "下跌" || label === "否") return "negative";
  return "neutral";
}

function closedOutcomeKey(market: MarketPerformance, metadata: ReportMarketMetadata | null): "up" | "down" | null {
  const metadataOutcome = metadata?.closed ? normalizeOutcome(metadata.outcome ?? metadata.raw_outcome) : null;
  if (metadataOutcome === "up" || metadataOutcome === "down") return metadataOutcome;

  const marketOutcome = normalizeOutcome(market.result);
  if (marketOutcome === "up" || marketOutcome === "down") return marketOutcome;
  return null;
}

function activePositionParts(state: ReplayState, onlyOutcome?: "up" | "down" | null) {
  const outcomes = onlyOutcome ? [onlyOutcome] : (["up", "down"] as const);
  return outcomes
    .map((outcome) => ({ outcome, shares: state.positions[outcome].shares }))
    .filter((item) => item.shares >= DUST_SHARES);
}

function formatCurrentPosition(state: ReplayState, onlyOutcome?: "up" | "down" | null) {
  const parts = activePositionParts(state, onlyOutcome);
  if (!parts.length) return "无持仓";
  return parts.map((item) => `${originalOutcome(item.outcome)} ${formatShares(item.shares)}`).join(" / ");
}

function currentDisplayShares(state: ReplayState, onlyOutcome?: "up" | "down" | null) {
  const total = activePositionParts(state, onlyOutcome).reduce((sum, item) => sum + item.shares, 0);
  return total >= DUST_SHARES ? formatShares(total) : "0";
}

function normalizeOutcome(value: string | null | undefined) {
  const normalized = (value ?? "").trim().toLowerCase();
  if (normalized === "up" || normalized === "上涨") return "up";
  if (normalized === "down" || normalized === "下跌") return "down";
  if (normalized === "yes" || normalized === "是") return "yes";
  if (normalized === "no" || normalized === "否") return "no";
  return normalized || null;
}

function displayOutcome(value: string | null) {
  if (value === "up") return "上涨";
  if (value === "down") return "下跌";
  if (value === "yes") return "是";
  if (value === "no") return "否";
  return value || "n/a";
}

function originalOutcome(value: string | null | undefined) {
  if (value === "up" || value === "上涨") return "Up";
  if (value === "down" || value === "下跌") return "Down";
  if (value === "yes" || value === "是") return "Yes";
  if (value === "no" || value === "否") return "No";
  return value ?? "";
}

function outcomeTagClass(value: string) {
  if (value === "上涨" || value === "是" || value.startsWith("Up ")) return "up";
  if (value === "下跌" || value === "否" || value.startsWith("Down ")) return "down";
  if (value === "未结算" || value === "无持仓") return "neutral";
  return "";
}

function typeLabel(type: string, side: string, outcome: string | null) {
  if (type === "TRADE") {
    const action = side === "BUY" ? "Buy" : side === "SELL" ? "Sell" : "Trade";
    const arrow = outcome === "Up" ? " ↑" : outcome === "Down" ? " ↓" : "";
    return `${action}${arrow}`;
  }
  if (type === "MERGE") return "Merge";
  if (type === "REDEEM") return "Redeem";
  if (type === "SPLIT") return "Split";
  return type || "n/a";
}

function timelineActionClass(type: string, side: string | null) {
  if (type === "MERGE" || type === "REDEEM") return "other";
  if (side?.toUpperCase() === "SELL") return "sell";
  return "buy";
}

function rawString(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

function activityTransactionReference(activity: ReportMarketActivity) {
  return activity.transaction_hash ?? rawString(activity.raw, ["transactionUrl", "transaction_url", "transactionHash", "transaction_hash"]);
}

function transactionUrl(value: string) {
  if (/^https?:\/\//i.test(value)) return value;
  return `https://polygonscan.com/tx/${value}`;
}

function formatDate(value: string | null) {
  if (!value) return "n/a";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZoneName: "shortOffset",
  }).formatToParts(date);
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}:${part("second")} ${part("timeZoneName")}`.trim();
}

function formatMoney(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

function formatSignedMoney(value: number) {
  const prefix = value >= 0 ? "+" : "-";
  return `${prefix}$${Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

function formatPercent(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "n/a";
  return `${(value * 100).toLocaleString("en-US", { maximumFractionDigits: 2 })}%`;
}

function formatShares(value: number) {
  return value.toLocaleString("en-US", { maximumFractionDigits: 5 });
}

function formatSharesCost(shares: number, cost: number) {
  return `${formatShares(shares)} / ${formatMoney(cost)}`;
}

function formatCents(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "n/a";
  return `${(value * 100).toLocaleString("en-US", { maximumFractionDigits: 2 })}¢`;
}

function formatAveragePrice(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "n/a";
  return formatCents(value);
}

function safeRatio(numerator: number, denominator: number) {
  return denominator === 0 ? null : numerator / denominator;
}

function safeNumber(value: number | null | undefined) {
  return value === null || value === undefined || !Number.isFinite(value) ? 0 : value;
}

function toneFor(value: number): "positive" | "negative" | "neutral" {
  if (value > 0) return "positive";
  if (value < 0) return "negative";
  return "neutral";
}
