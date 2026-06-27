import { DeleteOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { AssetType, ClobClient, type ApiKeyCreds } from "@polymarket/clob-client-v2";
import { createWalletClient, custom, type Address, type WalletClient } from "viem";
import { polygon } from "viem/chains";
import { api, type PolymarketCredentialProfile } from "../api/client";
import { useWalletConnection, type EthereumProvider } from "../hooks/useWalletConnection";

const POLYGON_CHAIN_ID = "0x89";
const CLOB_HOST = "https://clob.polymarket.com";
const IMPORT_PROFILE_POLL_MS = 2500;
const IMPORT_PROFILE_POLL_TIMEOUT_MS = 120_000;
const SIGNATURE_TYPE_OPTIONS = [
  { value: 0, label: "EOA" },
  { value: 1, label: "POLY_PROXY" },
  { value: 2, label: "GNOSIS_SAFE" },
  { value: 3, label: "POLY_1271" },
] as const;

type GeneratedCredentialCommand = {
  signerAddress: string;
  funderAddress: string;
  signatureType: number;
  apiKey: string;
  command: string;
};

type CredentialFormValues = {
  label: string;
  funderAddress: string;
  signatureType: number;
};

type SignatureDetectionRow = {
  signatureType: number;
  label: string;
  funderCandidate: string | null;
  funderSource: "signer" | "profile" | "manual" | "missing";
  status: "ok" | "error";
  cash: number | null;
  allowanceCount: number;
  error: string | null;
};

type CredentialDetection = {
  signerAddress: string;
  credentials: ApiKeyCreds;
  rows: SignatureDetectionRow[];
  selected: { signatureType: number; funderAddress: string } | null;
};

type ApiCredentialAction = "derive" | "create";

type PolymarketCredentialManagerProps = {
  variant?: "card" | "popover";
};

export function PolymarketCredentialManager({ variant = "card" }: PolymarketCredentialManagerProps) {
  const queryClient = useQueryClient();
  const [messageApi, messageContext] = message.useMessage();
  const [form] = Form.useForm<CredentialFormValues>();
  const walletConnection = useWalletConnection();
  const connectedAddress = variant === "popover" ? walletConnection.address : null;
  const [generated, setGenerated] = useState<GeneratedCredentialCommand | null>(null);
  const [detection, setDetection] = useState<CredentialDetection | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [deriveFailed, setDeriveFailed] = useState(false);
  const [pendingImportSigner, setPendingImportSigner] = useState<string | null>(null);
  const [pendingImportStartedAt, setPendingImportStartedAt] = useState<number | null>(null);
  const profilesQuery = useQuery({
    queryKey: ["polymarket-credentials"],
    queryFn: api.polymarketCredentials,
    enabled: variant !== "popover" || Boolean(connectedAddress),
    refetchOnWindowFocus: true,
    refetchInterval: pendingImportSigner ? IMPORT_PROFILE_POLL_MS : false,
  });
  const activeProfile = useMemo(
    () => profilesQuery.data?.profiles.find((item) => item.id === profilesQuery.data?.active_id) ?? null,
    [profilesQuery.data],
  );
  const matchingProfiles = useMemo(
    () =>
      (profilesQuery.data?.profiles ?? []).filter(
        (profile) => connectedAddress && normalizeAddress(profile.signer_address) === normalizeAddress(connectedAddress),
      ),
    [connectedAddress, profilesQuery.data?.profiles],
  );
  const activateMutation = useMutation({
    mutationFn: api.activatePolymarketCredential,
    onSuccess: (data) => {
      queryClient.removeQueries({ queryKey: ["polymarket-account-state"] });
      queryClient.setQueryData(["polymarket-credentials"], data);
      queryClient.invalidateQueries({ queryKey: ["polymarket-account-state"] });
      messageApi.success("已切换 active wallet profile");
    },
    onError: (error: Error) => messageApi.error(error.message),
  });
  const deleteMutation = useMutation({
    mutationFn: api.deletePolymarketCredential,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["polymarket-credentials"] });
      messageApi.success("已删除 wallet profile");
    },
    onError: (error: Error) => messageApi.error(error.message),
  });
  const updateLabelMutation = useMutation({
    mutationFn: ({ id, label }: { id: string; label: string }) => api.updatePolymarketCredential(id, { label }),
    onSuccess: (data) => {
      queryClient.setQueryData(["polymarket-credentials"], data);
      queryClient.invalidateQueries({ queryKey: ["polymarket-credentials"] });
      messageApi.success("已更新 wallet profile label");
    },
    onError: (error: Error) => messageApi.error(error.message),
  });
  const refreshAccountMutation = useMutation({
    mutationFn: api.refreshPolymarketAccountState,
    onSuccess: (state) => {
      queryClient.setQueryData(["polymarket-account-state", "global"], state);
      queryClient.invalidateQueries({ queryKey: ["polymarket-account-state"] });
      messageApi.success("账户状态已刷新");
    },
    onError: (error: Error) => messageApi.error(error.message),
  });

  useEffect(() => {
    if (!connectedAddress || activateMutation.isPending) return;
    const activeMatches = activeProfile?.signer_address && normalizeAddress(activeProfile.signer_address) === normalizeAddress(connectedAddress);
    if (activeMatches || matchingProfiles.length === 0) return;
    activateMutation.mutate(matchingProfiles[0].id);
  }, [activeProfile?.id, activeProfile?.signer_address, activateMutation, connectedAddress, matchingProfiles]);

  useEffect(() => {
    if (!pendingImportSigner || !pendingImportStartedAt) return;
    const importedProfile = (profilesQuery.data?.profiles ?? []).find(
      (profile) => normalizeAddress(profile.signer_address) === normalizeAddress(pendingImportSigner),
    );
    if (importedProfile) {
      setPendingImportSigner(null);
      setPendingImportStartedAt(null);
      setGenerated(null);
      setDetection(null);
      setDeriveFailed(false);
      messageApi.success("已检测到服务器导入的 wallet profile，正在自动启用");
      if (profilesQuery.data?.active_id !== importedProfile.id && !activateMutation.isPending) {
        activateMutation.mutate(importedProfile.id);
      }
      return;
    }
    if (Date.now() - pendingImportStartedAt > IMPORT_PROFILE_POLL_TIMEOUT_MS) {
      setPendingImportSigner(null);
      setPendingImportStartedAt(null);
      messageApi.warning("还没有检测到新 wallet profile。请确认服务器导入命令已执行成功，再点击刷新。");
    }
  }, [
    activateMutation,
    messageApi,
    pendingImportSigner,
    pendingImportStartedAt,
    profilesQuery.data?.active_id,
    profilesQuery.data?.profiles,
  ]);

  const runDetection = async ({
    provider,
    signerAddress,
    currentValues,
    action,
  }: {
    provider: EthereumProvider;
    signerAddress: string;
    currentValues: CredentialFormValues;
    action: ApiCredentialAction;
  }) => {
    await switchToPolygon(provider);
    if (!currentValues.label) form.setFieldsValue({ label: shortAddress(signerAddress) });
    const walletClient = createWalletClient({
      account: signerAddress as Address,
      chain: polygon,
      transport: custom(provider),
    });
    const clobClient = new ClobClient({
      host: CLOB_HOST,
      chain: 137,
      signer: walletClient,
    });
    // credentials 只在浏览器内存里短暂生成，用于检测和拼导入命令；不会通过 HTTP 上传到后端。
    const credentials = action === "create" ? await clobClient.createApiKey() : await clobClient.deriveApiKey();
    const profileFunder = await fetchProfileFunderCandidate(signerAddress);
    const manualFunder = isWalletAddress(currentValues.funderAddress) ? currentValues.funderAddress : null;
    const rows = await Promise.all(
      SIGNATURE_TYPE_OPTIONS.map((option) =>
        detectSignatureType({
          signer: walletClient,
          signerAddress,
          credentials,
          signatureType: option.value,
          label: option.label,
          profileFunder,
          manualFunder,
        }),
      ),
    );
    setDetection({ signerAddress, credentials, rows, selected: null });
    setDeriveFailed(false);
    setGenerated(null);
    messageApi.success("检测完成，请选择一种结果生成导入命令");
  };

  const handleDetect = async (action: ApiCredentialAction = "derive") => {
    const provider = requireEthereumProvider();
    setDetecting(true);
    try {
      const currentValues = form.getFieldsValue();
      const accounts = await provider.request<string[]>({ method: "eth_accounts" });
      const signerAddress = accounts[0];
      if (!signerAddress) throw new Error("请先点击右上角登录连接 MetaMask");
      await runDetection({ provider, signerAddress, currentValues, action });
    } catch (error) {
      if (action === "derive") setDeriveFailed(true);
      messageApi.error(error instanceof Error ? error.message : "检测钱包类型失败");
    } finally {
      setDetecting(false);
    }
  };

  const handleUseDetection = (row: SignatureDetectionRow) => {
    if (!detection || !row.funderCandidate) return;
    const selected = {
      signatureType: row.signatureType,
      funderAddress: row.funderCandidate,
    };
    form.setFieldsValue(selected);
    setDetection({ ...detection, selected });
    setGenerated(null);
  };

  const handleUseDetectionAndGenerate = async (row: SignatureDetectionRow) => {
    if (!detection || !row.funderCandidate) return;
    const selected = {
      signatureType: row.signatureType,
      funderAddress: row.funderCandidate,
    };
    form.setFieldsValue(selected);
    setDetection({ ...detection, selected });
    await generateCommand({ values: { ...form.getFieldsValue(), ...selected }, selected, sourceDetection: detection });
  };

  const handleGenerate = async (values: CredentialFormValues) => {
    if (!detection?.selected) {
      messageApi.error("请先点击“检测钱包类型”，并在检测结果中点击“使用”后再生成命令");
      return;
    }
    await generateCommand({ values, selected: detection.selected, sourceDetection: detection });
  };

  const generateCommand = async ({
    values,
    selected,
    sourceDetection,
  }: {
    values: CredentialFormValues;
    selected: { signatureType: number; funderAddress: string };
    sourceDetection: CredentialDetection;
  }) => {
    setGenerating(true);
    try {
      if (normalizeAddress(connectedAddress) !== normalizeAddress(sourceDetection.signerAddress)) {
        throw new Error("当前连接的钱包和检测结果不一致，请重新检测");
      }
      if (
        selected.signatureType !== values.signatureType ||
        normalizeAddress(selected.funderAddress) !== normalizeAddress(values.funderAddress)
      ) {
        throw new Error("Funder 或 Signature Type 已变更，请重新检测并点击“使用”");
      }
      const command = buildImportCommand({
        label: values.label,
        signerAddress: sourceDetection.signerAddress,
        funderAddress: values.funderAddress,
        signatureType: values.signatureType,
        credentials: sourceDetection.credentials,
      });
      setGenerated({
        signerAddress: sourceDetection.signerAddress,
        funderAddress: values.funderAddress,
        signatureType: values.signatureType,
        apiKey: sourceDetection.credentials.key,
        command,
      });
      setPendingImportSigner(sourceDetection.signerAddress);
      setPendingImportStartedAt(Date.now());
      queryClient.invalidateQueries({ queryKey: ["polymarket-credentials"] });
      messageApi.success("已生成导入命令，请在服务器终端手动执行");
    } catch (error) {
      messageApi.error(error instanceof Error ? error.message : "生成导入命令失败");
    } finally {
      setGenerating(false);
    }
  };

  const sharedContent = (
    <PolymarketCredentialContent
      activeProfile={activeProfile}
      connectedAddress={connectedAddress}
      form={form}
      detection={detection}
      generated={generated}
      detecting={detecting}
      deriveFailed={deriveFailed}
      generating={generating}
      profiles={profilesQuery.data?.profiles ?? []}
      matchingProfiles={matchingProfiles}
      profilesFetching={profilesQuery.isFetching}
      encryptionConfigured={profilesQuery.data?.encryption_configured ?? true}
      activatePending={activateMutation.isPending}
      activateVariables={activateMutation.variables}
      deletePending={deleteMutation.isPending}
      deleteVariables={deleteMutation.variables}
      variant={variant}
      onActivate={(id) => activateMutation.mutate(id)}
      onDelete={(id) => deleteMutation.mutate(id)}
      onRename={(id, label) => updateLabelMutation.mutate({ id, label })}
      onDetect={handleDetect}
      onGenerate={handleGenerate}
      onUseDetection={handleUseDetection}
      onUseDetectionAndGenerate={handleUseDetectionAndGenerate}
      onRefreshProfiles={() => profilesQuery.refetch()}
      onClearGenerated={() => {
        setGenerated(null);
        setDetection(null);
        setDeriveFailed(false);
        setPendingImportSigner(null);
        setPendingImportStartedAt(null);
      }}
    />
  );

  if (variant === "popover") {
    return (
      <>
        {messageContext}
        {sharedContent}
      </>
    );
  }

  return (
    <Card
      title="Polymarket 钱包 Profiles"
      extra={
        <Space size={8}>
          <Button
            size="small"
            onClick={() => refreshAccountMutation.mutate()}
            loading={refreshAccountMutation.isPending}
            disabled={!activeProfile}
          >
            刷新账户状态
          </Button>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => profilesQuery.refetch()} loading={profilesQuery.isFetching}>
            刷新
          </Button>
        </Space>
      }
    >
      {messageContext}
      {sharedContent}
    </Card>
  );
}

function PolymarketCredentialContent({
  activeProfile,
  connectedAddress,
  form,
  detection,
  generated,
  detecting,
  deriveFailed,
  generating,
  profiles,
  matchingProfiles,
  profilesFetching,
  encryptionConfigured,
  activatePending,
  activateVariables,
  deletePending,
  deleteVariables,
  variant,
  onActivate,
  onDelete,
  onRename,
  onDetect,
  onGenerate,
  onUseDetection,
  onUseDetectionAndGenerate,
  onRefreshProfiles,
  onClearGenerated,
}: {
  activeProfile: PolymarketCredentialProfile | null;
  connectedAddress: string | null;
  form: ReturnType<typeof Form.useForm<CredentialFormValues>>[0];
  detection: CredentialDetection | null;
  generated: GeneratedCredentialCommand | null;
  detecting: boolean;
  deriveFailed: boolean;
  generating: boolean;
  profiles: PolymarketCredentialProfile[];
  matchingProfiles: PolymarketCredentialProfile[];
  profilesFetching: boolean;
  encryptionConfigured: boolean;
  activatePending: boolean;
  activateVariables: string | undefined;
  deletePending: boolean;
  deleteVariables: string | undefined;
  variant: "card" | "popover";
  onActivate: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, label: string) => void;
  onDetect: (action?: ApiCredentialAction) => void;
  onGenerate: (values: CredentialFormValues) => void;
  onUseDetection: (row: SignatureDetectionRow) => void;
  onUseDetectionAndGenerate: (row: SignatureDetectionRow) => void;
  onRefreshProfiles: () => void;
  onClearGenerated: () => void;
}) {
  return (
    <Space direction="vertical" size={variant === "popover" ? 10 : 14} className="polymarket-credential-manager">
      {variant !== "popover" && (
        <Alert
          type="warning"
          showIcon
          message="当前服务器未启用 HTTPS 时，不要通过 HTTP 上传 CLOB secret。这里仅在浏览器本地生成导入命令，用户手动到服务器执行。"
        />
      )}
      {!encryptionConfigured && (
        <Alert
          type="error"
          showIcon
          message="后端未配置 POLYMARKET_CREDENTIALS_ENCRYPTION_KEY，无法导入或使用多钱包 credentials。"
        />
      )}
      {variant === "popover" ? (
        <HeaderWalletWorkflow
          activeProfile={activeProfile}
          connectedAddress={connectedAddress}
          form={form}
          detection={detection}
          generated={generated}
          detecting={detecting}
          deriveFailed={deriveFailed}
          generating={generating}
          profiles={profiles}
          matchingProfiles={matchingProfiles}
          profilesFetching={profilesFetching}
          activatePending={activatePending}
          onActivate={onActivate}
          onRename={onRename}
          onClearGenerated={onClearGenerated}
          onDetect={onDetect}
          onGenerate={onGenerate}
          onRefreshProfiles={onRefreshProfiles}
          onUseDetection={onUseDetection}
          onUseDetectionAndGenerate={onUseDetectionAndGenerate}
        />
      ) : (
        <>
          <ActiveProfile profile={activeProfile} />
          <CredentialProfileTable
            profiles={profiles}
            profilesFetching={profilesFetching}
            activatePending={activatePending}
            activateVariables={activateVariables}
            deletePending={deletePending}
            deleteVariables={deleteVariables}
            onActivate={onActivate}
            onRename={onRename}
            onDelete={onDelete}
          />
        </>
      )}
      {variant !== "popover" && (
        <CredentialGenerator
          connectedAddress={connectedAddress}
          form={form}
          detection={detection}
          generated={generated}
          detecting={detecting}
          deriveFailed={deriveFailed}
          generating={generating}
          variant={variant}
          onClearGenerated={onClearGenerated}
          onDetect={onDetect}
          onGenerate={onGenerate}
          onUseDetection={onUseDetection}
          onUseDetectionAndGenerate={onUseDetectionAndGenerate}
        />
      )}
    </Space>
  );
}

function HeaderWalletWorkflow({
  activeProfile,
  connectedAddress,
  form,
  detection,
  generated,
  detecting,
  deriveFailed,
  generating,
  profiles,
  matchingProfiles,
  profilesFetching,
  activatePending,
  onActivate,
  onRename,
  onClearGenerated,
  onDetect,
  onGenerate,
  onRefreshProfiles,
  onUseDetection,
  onUseDetectionAndGenerate,
}: {
  activeProfile: PolymarketCredentialProfile | null;
  connectedAddress: string | null;
  form: ReturnType<typeof Form.useForm<CredentialFormValues>>[0];
  detection: CredentialDetection | null;
  generated: GeneratedCredentialCommand | null;
  detecting: boolean;
  deriveFailed: boolean;
  generating: boolean;
  profiles: PolymarketCredentialProfile[];
  matchingProfiles: PolymarketCredentialProfile[];
  profilesFetching: boolean;
  activatePending: boolean;
  onActivate: (id: string) => void;
  onRename: (id: string, label: string) => void;
  onClearGenerated: () => void;
  onDetect: (action?: ApiCredentialAction) => void;
  onGenerate: (values: CredentialFormValues) => void;
  onRefreshProfiles: () => void;
  onUseDetection: (row: SignatureDetectionRow) => void;
  onUseDetectionAndGenerate: (row: SignatureDetectionRow) => void;
}) {
  return (
    <div className="polymarket-wallet-flow">
      <div className="polymarket-wallet-flow-head">
        <div>
          <div className="polymarket-wallet-flow-subtitle">
            {connectedAddress ? `MetaMask ${shortAddress(connectedAddress)}` : "连接 MetaMask 后开始"}
          </div>
        </div>
        <Space size={6}>
          <Button size="small" type="text" icon={<ReloadOutlined />} loading={profilesFetching} onClick={onRefreshProfiles} />
        </Space>
      </div>

      {!connectedAddress ? (
        <div className="polymarket-wallet-connect">
          <Typography.Text type="secondary">请在 MetaMask 中连接当前站点，页面会自动识别钱包状态。</Typography.Text>
        </div>
      ) : matchingProfiles.length > 0 ? (
        <div className="polymarket-wallet-ready">
          <CompactProfileSwitcher
            activeProfile={activeProfile}
            profiles={matchingProfiles}
            profilesFetching={profilesFetching}
            activatePending={activatePending}
            onActivate={onActivate}
            onRename={onRename}
          />
        </div>
      ) : (
        <div className="polymarket-wallet-onboarding">
          <CredentialGenerator
            connectedAddress={connectedAddress}
            form={form}
            detection={detection}
            generated={generated}
            detecting={detecting}
            deriveFailed={deriveFailed}
            generating={generating}
            variant="popover"
            onClearGenerated={onClearGenerated}
            onDetect={onDetect}
            onGenerate={onGenerate}
            onUseDetection={onUseDetection}
            onUseDetectionAndGenerate={onUseDetectionAndGenerate}
          />
        </div>
      )}
    </div>
  );
}

function CompactProfileSwitcher({
  activeProfile,
  profiles,
  profilesFetching,
  activatePending,
  onActivate,
  onRename,
}: {
  activeProfile: PolymarketCredentialProfile | null;
  profiles: PolymarketCredentialProfile[];
  profilesFetching: boolean;
  activatePending: boolean;
  onActivate: (id: string) => void;
  onRename: (id: string, label: string) => void;
}) {
  return (
    <div className="polymarket-wallet-quick-panel">
      {profiles.length > 0 ? (
        <List
          className="polymarket-wallet-profile-list"
          size="small"
          loading={profilesFetching}
          dataSource={profiles}
          renderItem={(profile) => {
            const active = profile.id === activeProfile?.id;
            return (
              <List.Item
                className={active ? "polymarket-wallet-profile-item active" : "polymarket-wallet-profile-item"}
                actions={[
                  active ? (
                    <Tag color="success" key="active">当前</Tag>
                  ) : (
                    <Button
                      key="activate"
                      size="small"
                      type="link"
                      loading={activatePending}
                      onClick={() => onActivate(profile.id)}
                    >
                      启用
                    </Button>
                  ),
                ]}
              >
                <List.Item.Meta
                  title={<EditableProfileLabel profile={profile} onRename={onRename} />}
                  description={<Typography.Text type="secondary">{shortAddress(profile.funder_address)}</Typography.Text>}
                />
              </List.Item>
            );
          }}
        />
      ) : (
        <div className="polymarket-wallet-empty">{profilesFetching ? "加载中" : "暂无 wallet profile"}</div>
      )}
    </div>
  );
}

function CredentialProfileTable({
  profiles,
  profilesFetching,
  activatePending,
  activateVariables,
  deletePending,
  deleteVariables,
  onActivate,
  onRename,
  onDelete,
}: {
  profiles: PolymarketCredentialProfile[];
  profilesFetching: boolean;
  activatePending: boolean;
  activateVariables: string | undefined;
  deletePending: boolean;
  deleteVariables: string | undefined;
  onActivate: (id: string) => void;
  onRename: (id: string, label: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <Table<PolymarketCredentialProfile>
      rowKey="id"
      size="small"
      loading={profilesFetching}
      dataSource={profiles}
      locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已导入 wallet profile" /> }}
      pagination={false}
      columns={[
        {
          title: "Label",
          dataIndex: "label",
          render: (value: string, record) => (
            <Space size={6}>
              <EditableProfileLabel profile={record} label={value} onRename={onRename} />
              {record.active && <Tag color="success">active</Tag>}
            </Space>
          ),
        },
        {
          title: "API Key",
          dataIndex: "api_key_masked",
          width: 170,
        },
        {
          title: "Signer",
          dataIndex: "signer_address",
          render: (value: string) => <Typography.Text code>{shortAddress(value)}</Typography.Text>,
        },
        {
          title: "Funder",
          dataIndex: "funder_address",
          render: (value: string) => <Typography.Text code>{shortAddress(value)}</Typography.Text>,
        },
        {
          title: "Type",
          dataIndex: "signature_type",
          width: 80,
        },
        {
          title: "操作",
          width: 170,
          render: (_value: unknown, record) => (
            <Space size={6}>
              <Button
                size="small"
                disabled={record.active}
                loading={activatePending && activateVariables === record.id}
                onClick={() => onActivate(record.id)}
              >
                启用
              </Button>
              <Popconfirm
                title="删除 wallet profile"
                description="只会删除服务器加密保存的 CLOB credentials。"
                okText="删除"
                cancelText="取消"
                disabled={record.active}
                onConfirm={() => onDelete(record.id)}
              >
                <Button
                  size="small"
                  danger
                  icon={<DeleteOutlined />}
                  disabled={record.active}
                  loading={deletePending && deleteVariables === record.id}
                />
              </Popconfirm>
            </Space>
          ),
        },
      ]}
    />
  );
}

function CredentialGenerator({
  connectedAddress,
  form,
  detection,
  generated,
  detecting,
  deriveFailed,
  generating,
  variant,
  onClearGenerated,
  onDetect,
  onGenerate,
  onUseDetection,
  onUseDetectionAndGenerate,
}: {
  connectedAddress: string | null;
  form: ReturnType<typeof Form.useForm<CredentialFormValues>>[0];
  detection: CredentialDetection | null;
  generated: GeneratedCredentialCommand | null;
  detecting: boolean;
  deriveFailed: boolean;
  generating: boolean;
  variant: "card" | "popover";
  onClearGenerated: () => void;
  onDetect: (action?: ApiCredentialAction) => void;
  onGenerate: (values: CredentialFormValues) => void;
  onUseDetection: (row: SignatureDetectionRow) => void;
  onUseDetectionAndGenerate: (row: SignatureDetectionRow) => void;
}) {
  const currentSignatureType = Form.useWatch("signatureType", form);
  const currentFunderAddress = Form.useWatch("funderAddress", form);
  const [copied, setCopied] = useState(false);
  const detectionStale = Boolean(
    detection?.selected &&
      (detection.selected.signatureType !== currentSignatureType ||
        normalizeAddress(detection.selected.funderAddress) !== normalizeAddress(currentFunderAddress)),
  );
  return (
    <div className="polymarket-credential-generator">
      <div className="polymarket-credential-generator-head">
        <div>
          <Typography.Title level={5}>{variant === "popover" ? "初始化 Wallet Profile" : "生成导入命令"}</Typography.Title>
          <Typography.Text type="secondary">
            恢复和创建是两个独立动作；每次只会请求一次 MetaMask 签名，生成命令不会再次签名。
          </Typography.Text>
        </div>
      </div>
      <Form
        form={form}
        layout="vertical"
        initialValues={{ signatureType: 3 }}
        onFinish={onGenerate}
        className="polymarket-credential-form"
      >
        <Form.Item label="Label" name="label" rules={[{ required: true, message: "请输入 profile label" }]}>
          <Input placeholder="例如 Main deposit wallet" autoComplete="off" />
        </Form.Item>
        <Form.Item
          label="Funder / Deposit Wallet 地址"
          name="funderAddress"
          rules={[
            { required: true, message: "请输入 Polymarket Deposit Wallet 地址" },
            { pattern: /^0x[a-fA-F0-9]{40}$/, message: "请输入 0x 开头的钱包地址" },
          ]}
        >
          <Input placeholder="Polymarket 账户里的 Deposit Wallet 地址" autoComplete="off" />
        </Form.Item>
        <Form.Item label="Signature Type" name="signatureType" rules={[{ required: true }]}>
          <InputNumber min={0} max={3} className="polymarket-credential-signature-input" />
        </Form.Item>
        <Form.Item>
          <Space>
            <Button onClick={() => onDetect("derive")} loading={detecting}>
              {detection ? "重新恢复 API 授权" : "恢复已有 API 授权"}
            </Button>
            {deriveFailed && (
              <Button onClick={() => onDetect("create")} loading={detecting}>
                创建新 API 授权
              </Button>
            )}
            <Button type="primary" htmlType="submit" loading={generating} disabled={!detection?.selected || detectionStale}>
              生成命令
            </Button>
            <Button onClick={onClearGenerated} disabled={!generated && !detection}>
              清空 secret
            </Button>
          </Space>
        </Form.Item>
      </Form>
      {detection && (
        <SignatureDetectionTable
          detection={detection}
          usingSignatureType={currentSignatureType}
          usingFunderAddress={currentFunderAddress}
          onUse={onUseDetection}
          onUseAndGenerate={variant === "popover" ? onUseDetectionAndGenerate : undefined}
        />
      )}
      {detectionStale && <Alert type="warning" showIcon message="Funder 或 Signature Type 已变更，请重新检测并点击“使用”。" />}
      {generated && (
        <div className="polymarket-credential-command">
          <div className="polymarket-credential-command-head">
            <Typography.Text strong>服务器导入命令</Typography.Text>
            <Typography.Text type={copied ? "success" : "secondary"}>{copied ? "已复制" : "点击文本框复制"}</Typography.Text>
          </div>
          <button
            className="polymarket-credential-command-copy"
            type="button"
            onClick={() => {
              copyText(generated.command)
                .then(() => {
                  setCopied(true);
                  window.setTimeout(() => setCopied(false), 1800);
                })
                .catch(() => undefined);
            }}
          >
            {generated.command}
          </button>
          <Typography.Text type="secondary">
            这段命令包含 CLOB secret/passphrase。执行完成后点击刷新，确认 profile 出现在列表后点击“启用”。
            完成后点击“清空 secret”，不要截图、不要提交到 git、不要保存到浏览器。
          </Typography.Text>
        </div>
      )}
    </div>
  );
}

function ActiveProfile({ profile }: { profile: PolymarketCredentialProfile | null }) {
  if (!profile) {
    return (
      <Alert
        type="info"
        showIcon
        message="当前没有 active wallet profile。请生成并导入 profile 后点击启用。"
      />
    );
  }
  return (
    <Descriptions size="small" bordered column={{ xs: 1, md: 2 }}>
      <Descriptions.Item label="Active">{profile.label}</Descriptions.Item>
      <Descriptions.Item label="API Key">{profile.api_key_masked}</Descriptions.Item>
      <Descriptions.Item label="Signer">{profile.signer_address}</Descriptions.Item>
      <Descriptions.Item label="Funder">{profile.funder_address}</Descriptions.Item>
    </Descriptions>
  );
}

function SignatureDetectionTable({
  detection,
  usingSignatureType,
  usingFunderAddress,
  onUse,
  onUseAndGenerate,
}: {
  detection: CredentialDetection;
  usingSignatureType: number | undefined;
  usingFunderAddress: string | undefined;
  onUse: (row: SignatureDetectionRow) => void;
  onUseAndGenerate?: (row: SignatureDetectionRow) => void;
}) {
  return (
    <div className="polymarket-signature-detection">
      <Descriptions size="small" column={{ xs: 1, md: 2 }}>
        <Descriptions.Item label="Signer">{shortAddress(detection.signerAddress)}</Descriptions.Item>
        <Descriptions.Item label="API Key">{shortApiKey(detection.credentials.key)}</Descriptions.Item>
      </Descriptions>
      <Table<SignatureDetectionRow>
        rowKey="signatureType"
        size="small"
        pagination={false}
        dataSource={detection.rows}
        columns={[
          {
            title: "Type",
            width: 150,
            render: (_value: unknown, record) => (
              <Space direction="vertical" size={0}>
                <Typography.Text strong>{record.signatureType}</Typography.Text>
                <Typography.Text type="secondary">{record.label}</Typography.Text>
              </Space>
            ),
          },
          {
            title: "状态",
            width: 90,
            render: (_value: unknown, record) => <DetectionStatusTag record={record} />,
          },
          {
            title: "Cash",
            width: 100,
            render: (_value: unknown, record) => (record.cash == null ? "-" : `$${formatNumber(record.cash)}`),
          },
          {
            title: "Allowance",
            width: 100,
            dataIndex: "allowanceCount",
          },
          {
            title: "Funder 候选",
            render: (_value: unknown, record) => (
              <Space direction="vertical" size={0}>
                <Typography.Text code>{record.funderCandidate ? shortAddress(record.funderCandidate) : "-"}</Typography.Text>
                <Typography.Text type="secondary">{funderSourceLabel(record.funderSource)}</Typography.Text>
                {record.error && <Typography.Text type="danger">{record.error}</Typography.Text>}
              </Space>
            ),
          },
          {
            title: "操作",
            width: 90,
            render: (_value: unknown, record) => {
              const active =
                record.signatureType === usingSignatureType &&
                normalizeAddress(record.funderCandidate) === normalizeAddress(usingFunderAddress);
              return (
                <Button
                  size="small"
                  disabled={!record.funderCandidate}
                  type={active ? "primary" : "default"}
                  onClick={() => (onUseAndGenerate ? onUseAndGenerate(record) : onUse(record))}
                >
                  {onUseAndGenerate ? "使用并生成" : "使用"}
                </Button>
              );
            },
          },
        ]}
      />
      <Typography.Text type="secondary">
        检测只辅助选择类型；最终 Deposit Wallet 地址仍以 Polymarket Settings 页面为准。
      </Typography.Text>
    </div>
  );
}

function DetectionStatusTag({ record }: { record: SignatureDetectionRow }) {
  if (record.status === "error") return <Tag color="error">失败</Tag>;
  if ((record.cash ?? 0) > 0) return <Tag color="success">有余额</Tag>;
  return <Tag>可查询</Tag>;
}

function requireEthereumProvider() {
  if (!window.ethereum) {
    throw new Error("未检测到 MetaMask，请先安装或打开钱包插件");
  }
  return window.ethereum;
}

async function copyText(value: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

async function switchToPolygon(provider: EthereumProvider) {
  try {
    await provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: POLYGON_CHAIN_ID }] });
  } catch (error) {
    const code = typeof error === "object" && error !== null && "code" in error ? (error as { code?: number }).code : null;
    if (code !== 4902) throw error;
    await provider.request({
      method: "wallet_addEthereumChain",
      params: [
        {
          chainId: POLYGON_CHAIN_ID,
          chainName: "Polygon",
          nativeCurrency: { name: "POL", symbol: "POL", decimals: 18 },
          rpcUrls: ["https://polygon-rpc.com"],
          blockExplorerUrls: ["https://polygonscan.com"],
        },
      ],
    });
  }
}

async function fetchProfileFunderCandidate(signerAddress: string): Promise<string | null> {
  try {
    const response = await fetch(`https://gamma-api.polymarket.com/public-profile?address=${encodeURIComponent(signerAddress)}`);
    if (!response.ok) return null;
    const payload = (await response.json()) as { proxyWallet?: unknown };
    const proxyWallet = typeof payload.proxyWallet === "string" ? payload.proxyWallet : "";
    return isWalletAddress(proxyWallet) ? proxyWallet : null;
  } catch {
    return null;
  }
}

async function detectSignatureType(args: {
  signer: WalletClient;
  signerAddress: string;
  credentials: ApiKeyCreds;
  signatureType: number;
  label: string;
  profileFunder: string | null;
  manualFunder: string | null;
}): Promise<SignatureDetectionRow> {
  const funderCandidate = funderCandidateForSignatureType(args);
  const client = new ClobClient({
    host: CLOB_HOST,
    chain: 137,
    signer: args.signer,
    creds: args.credentials,
    signatureType: args.signatureType,
    funderAddress: funderCandidate ?? undefined,
  });
  try {
    const balance = await client.getBalanceAllowance({ asset_type: AssetType.COLLATERAL });
    return {
      signatureType: args.signatureType,
      label: args.label,
      funderCandidate,
      funderSource: funderSourceForSignatureType(args),
      status: "ok",
      cash: usdcBaseUnitsToNumber(balance.balance),
      allowanceCount: Object.keys(balance.allowances ?? {}).length,
      error: null,
    };
  } catch (error) {
    return {
      signatureType: args.signatureType,
      label: args.label,
      funderCandidate,
      funderSource: funderSourceForSignatureType(args),
      status: "error",
      cash: null,
      allowanceCount: 0,
      error: errorMessage(error),
    };
  }
}

function funderCandidateForSignatureType(args: {
  signerAddress: string;
  signatureType: number;
  profileFunder: string | null;
  manualFunder: string | null;
}) {
  if (args.signatureType === 0) return args.signerAddress;
  return args.profileFunder ?? args.manualFunder ?? null;
}

function funderSourceForSignatureType(args: {
  signatureType: number;
  profileFunder: string | null;
  manualFunder: string | null;
}): SignatureDetectionRow["funderSource"] {
  if (args.signatureType === 0) return "signer";
  if (args.profileFunder) return "profile";
  if (args.manualFunder) return "manual";
  return "missing";
}

function funderSourceLabel(source: SignatureDetectionRow["funderSource"]) {
  if (source === "signer") return "Signer";
  if (source === "profile") return "Public profile";
  if (source === "manual") return "表单输入";
  return "请到 Polymarket Settings 查看";
}

function buildImportCommand(args: {
  label: string;
  signerAddress: string;
  funderAddress: string;
  signatureType: number;
  credentials: ApiKeyCreds;
}) {
  const payload = {
    label: args.label,
    signer_address: args.signerAddress,
    funder_address: args.funderAddress,
    signature_type: args.signatureType,
    api_key: args.credentials.key,
    api_secret: args.credentials.secret,
    api_passphrase: args.credentials.passphrase,
  };
  const encoded = base64EncodeUtf8(JSON.stringify(payload));
  return `POLY_CREDENTIAL_PAYLOAD='${encoded}' make import-polymarket-credentials`;
}

function base64EncodeUtf8(value: string) {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary);
}

function shortAddress(value: string) {
  if (!value) return "-";
  if (value.length <= 14) return value;
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

function EditableProfileLabel({
  profile,
  label = profile.label,
  onRename,
}: {
  profile: PolymarketCredentialProfile;
  label?: string;
  onRename: (id: string, label: string) => void;
}) {
  return (
    <Typography.Text
      strong
      className="polymarket-wallet-profile-name"
      editable={{
        tooltip: "修改 label",
        triggerType: ["icon"],
        onChange: (value) => {
          const normalizedLabel = value.trim();
          if (!normalizedLabel || normalizedLabel === profile.label) return;
          onRename(profile.id, normalizedLabel);
        },
      }}
    >
      {label}
    </Typography.Text>
  );
}

function shortApiKey(value: string) {
  if (!value) return "-";
  if (value.length <= 12) return value;
  return `${value.slice(0, 5)}...${value.slice(-4)}`;
}

function isWalletAddress(value: string | null | undefined) {
  return /^0x[a-fA-F0-9]{40}$/.test(String(value || "").trim());
}

function normalizeAddress(value: string | null | undefined) {
  return String(value || "").trim().toLowerCase();
}

function usdcBaseUnitsToNumber(value: string | number | null | undefined) {
  const numeric = Number(value ?? 0);
  if (!Number.isFinite(numeric)) return null;
  return numeric / 1_000_000;
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 }).format(value);
}

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message.slice(0, 160);
  return "请求失败";
}
