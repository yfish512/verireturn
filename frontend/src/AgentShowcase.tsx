import { useEffect, useMemo, useState } from "react";
import { Alert, Avatar, Button, Card, Col, Descriptions, Divider, Empty, Input, Layout, List, Row, Select, Space, Spin, Steps, Tag, Timeline, Tooltip, Typography, message } from "antd";
import type { AgentMessage, AgentTaskMemory, AgentThreadSnapshot, AgentToolCall, FulfillmentStatus } from "./types";
import { api } from "./api";

type ChatItem = {
  id: string;
  role: "customer" | "agent";
  content: string;
  result?: AgentMessage;
  tools?: AgentToolCall[];
  fulfillment?: FulfillmentStatus;
};

const suggestedPrompts = [
  "退款政策和上门取件流程是什么？",
  "帮我退 O1001，商品未拆封",
  "订单 O1003 有质量问题，申请退款人工审核",
];

const labelForTool: Record<string, string> = {
  get_order: "查询订单", get_logistics: "查询物流", check_after_sales_eligibility: "校验售后资格",
  create_after_sales_case: "创建待确认售后单", confirm_after_sales_case: "确认售后单",
  cancel_after_sales_case: "取消售后单", schedule_pickup: "预约取件",
  get_fulfillment_status: "查询履约进度", retrieve_knowledge: "检索已发布知识", create_review_ticket: "提交人工审核",
};
const taskLabel: Record<string, string> = {
  create_after_sales: "退款/换货申请", request_manual_review: "人工审核申请", schedule_pickup: "预约上门取件",
};
const phaseLabel: Record<string, string> = {
  collecting_slots: "等待补充信息", ready_to_execute: "正在执行", awaiting_customer_confirmation: "等待客户确认",
  awaiting_pickup_slot: "等待取件时段", completed: "已完成", cancelled: "已取消", expired: "已过期",
};

const resultTag = (status: AgentMessage["status"]) => (
  <Tag color={status === "awaiting_confirmation" ? "gold" : status === "completed" ? "green" : "red"}>
    {status === "awaiting_confirmation" ? "等待确认" : status === "completed" ? "已完成" : "执行失败"}
  </Tag>
);

const storageKey = (actor: string) => `verireturn.agentThread.${actor}`;
const snapshotItems = (snapshot: AgentThreadSnapshot): ChatItem[] => snapshot.messages.map((item) => ({
  id: item.id, role: item.role, content: item.content, result: item.payload || undefined,
}));

export function AgentShowcase() {
  const [actor, setActor] = useState("U001");
  const [threadId, setThreadId] = useState("");
  const [draft, setDraft] = useState("");
  const [items, setItems] = useState<ChatItem[]>([]);
  const [memory, setMemory] = useState<AgentTaskMemory>();
  const [activeRunId, setActiveRunId] = useState<string>();
  const [sending, setSending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [loadingThread, setLoadingThread] = useState(true);
  const [messageApi, contextHolder] = message.useMessage();

  const activeItem = useMemo(() => [...items].reverse().find((item) => item.role === "agent" && item.result?.run_id === activeRunId), [items, activeRunId]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      setLoadingThread(true);
      const saved = localStorage.getItem(storageKey(actor));
      try {
        const snapshot = saved ? await api.agentThread(actor, saved) : null;
        if (!active) return;
        if (snapshot) {
          setThreadId(snapshot.thread_id); setItems(snapshotItems(snapshot)); setMemory(snapshot.task || undefined);
          setActiveRunId([...snapshot.messages].reverse().find((item) => item.payload)?.payload?.run_id);
        } else {
          const created = await api.createAgentThread(actor);
          localStorage.setItem(storageKey(actor), created.thread_id);
          if (active) { setThreadId(created.thread_id); setItems([]); setMemory(undefined); setActiveRunId(undefined); }
        }
      } catch {
        const created = await api.createAgentThread(actor);
        localStorage.setItem(storageKey(actor), created.thread_id);
        if (active) { setThreadId(created.thread_id); setItems([]); setMemory(undefined); setActiveRunId(undefined); }
      } finally { if (active) setLoadingThread(false); }
    };
    void load();
    return () => { active = false; };
  }, [actor]);

  async function hydrateResult(result: AgentMessage): Promise<Pick<ChatItem, "tools" | "fulfillment">> {
    const tools = await api.agentToolCalls(actor, result.run_id);
    const fulfillment = result.case_id ? await api.agentFulfillment(actor, result.case_id).catch(() => undefined) : undefined;
    return { tools, fulfillment };
  }

  async function send() {
    const input = draft.trim();
    if (!input || sending || !threadId) return;
    const id = crypto.randomUUID();
    setItems((current) => [...current, { id, role: "customer", content: input }]);
    setDraft(""); setSending(true);
    try {
      const result = await api.agentMessage(actor, threadId, input, `web-message-${id}`);
      const hydrated = await hydrateResult(result);
      setItems((current) => [...current, { id: crypto.randomUUID(), role: "agent", content: result.response, result, ...hydrated }]);
      setMemory(result.memory || undefined); setActiveRunId(result.run_id);
    } catch (error) { messageApi.error(error instanceof Error ? error.message : "Agent 请求失败，请稍后重试。"); }
    finally { setSending(false); }
  }

  async function resolveConfirmation(item: ChatItem, approved: boolean) {
    if (!item.result?.confirmation_id || confirming) return;
    setConfirming(true);
    try {
      const result = await api.resolveAgentConfirmation(actor, item.result.confirmation_id, approved);
      const hydrated = await hydrateResult(result);
      setItems((current) => [...current, { id: crypto.randomUUID(), role: "agent", content: result.response, result, ...hydrated }]);
      setMemory(result.memory || undefined); setActiveRunId(result.run_id);
      messageApi.success(approved ? "确认已提交，Agent 已恢复执行。" : "已取消本次申请。");
    } catch (error) { messageApi.error(error instanceof Error ? error.message : "确认失败，请重试。"); }
    finally { setConfirming(false); }
  }

  async function resetConversation() {
    setLoadingThread(true);
    try {
      const created = await api.createAgentThread(actor);
      localStorage.setItem(storageKey(actor), created.thread_id);
      setThreadId(created.thread_id); setItems([]); setMemory(undefined); setActiveRunId(undefined); setDraft("");
    } catch (error) { messageApi.error(error instanceof Error ? error.message : "无法创建新会话。"); }
    finally { setLoadingThread(false); }
  }

  return <>{contextHolder}<Layout className="agent-shell">
    <Layout.Header className="agent-header">
      <Space size="middle"><a className="agent-brand" href="/">VeriReturn</a><Tag>客户 Agent 演示</Tag></Space>
      <Space><Select value={actor} onChange={setActor} options={[{ value: "U001", label: "演示客户 · 李雷" }, { value: "U7001", label: "M7 演示客户" }]} /><Button onClick={() => void resetConversation()}>新建会话</Button><a href="/">运营台</a></Space>
    </Layout.Header>
    <Layout.Content className="agent-content">
      <section className="agent-hero"><div><Typography.Title level={1}>售后问题，给你可核验的答案</Typography.Title><Typography.Paragraph>Agent 只能调用受控工具。金额、资格、权限和履约状态都由后端服务与数据库决定。</Typography.Paragraph></div><Space wrap><Tag color="blue">LangGraph</Tag><Tag color="purple">DeepSeek v4 Flash</Tag><Tag color="green">确认后执行</Tag><Tag color="gold">显式任务记忆</Tag></Space></section>
      <Row gutter={[20, 20]}>
        <Col xs={24} lg={15}><Card className="agent-chat-card" title={<Space><Avatar style={{ background: "#1677ff" }}>V</Avatar><span>VeriReturn 售后助手</span><Tag color="green">在线</Tag></Space>} extra={<Typography.Text type="secondary">会话 {threadId ? threadId.slice(-8) : "加载中"}</Typography.Text>}>
          {loadingThread ? <div className="agent-empty"><Spin /><Typography.Text type="secondary">正在恢复会话记忆…</Typography.Text></div> : items.length === 0 ? <div className="agent-empty"><Empty description="从一个售后问题开始" /><Space wrap>{suggestedPrompts.map((prompt) => <Button key={prompt} onClick={() => setDraft(prompt)}>{prompt}</Button>)}</Space></div> : <List className="agent-messages" dataSource={items} renderItem={(item) => <List.Item className={`agent-message agent-message-${item.role}`}>
            <div className="agent-message-head"><Space><Avatar style={{ background: item.role === "customer" ? "#6b7280" : "#1677ff" }}>{item.role === "customer" ? "我" : "V"}</Avatar><b>{item.role === "customer" ? "客户" : "售后助手"}</b>{item.result && resultTag(item.result.status)}</Space>{item.result && <Button size="small" type="link" onClick={() => setActiveRunId(item.result!.run_id)}>查看本次执行</Button>}</div>
            <div className="agent-bubble">{item.content}</div>
            {item.result?.citations.length ? <div className="agent-citations"><Typography.Text type="secondary">已引用发布知识：</Typography.Text>{item.result.citations.map((citation) => <Tag key={citation} color="geekblue">#{citation.slice(0, 8)}</Tag>)}</div> : null}
            {item.result?.status === "awaiting_confirmation" && item.result.confirmation_id ? <Alert className="agent-confirmation" type="warning" showIcon message="此操作会创建或提交售后业务请求" description={<Space wrap><Typography.Text>系统已保存待确认命令；确认前不会进入后续业务流程。</Typography.Text><Button type="primary" loading={confirming} onClick={() => void resolveConfirmation(item, true)}>确认执行</Button><Button loading={confirming} onClick={() => void resolveConfirmation(item, false)}>取消</Button></Space>} /> : null}
            {item.fulfillment && <Card size="small" className="agent-case-card" title={`售后单 #${item.fulfillment.case_id}`} extra={<Tag color={item.fulfillment.status === "completed" ? "green" : "blue"}>{item.fulfillment.status}</Tag>}><Timeline items={item.fulfillment.events.map((event) => ({ children: `${event.sequence_no}. ${event.event_type}` }))} /></Card>}
          </List.Item>} />}
          {sending && <div className="agent-thinking"><Spin size="small" /> 正在处理…</div>}
          <Divider /><Input.TextArea value={draft} disabled={loadingThread} onChange={(event) => setDraft(event.target.value)} onPressEnter={(event) => { if (!event.shiftKey) { event.preventDefault(); void send(); } }} placeholder="例如：帮我退 O1001，商品未拆封" autoSize={{ minRows: 3, maxRows: 6 }} />
          <div className="agent-compose-actions"><Typography.Text type="secondary">Enter 发送，Shift + Enter 换行</Typography.Text><Button type="primary" disabled={loadingThread || !threadId} loading={sending} onClick={() => void send()}>发送</Button></div>
        </Card></Col>
        <Col xs={24} lg={9}><Space direction="vertical" size="middle" className="agent-side-stack">
          <Card title="当前任务记忆" extra={memory ? <Tag color={memory.phase === "completed" ? "green" : "blue"}>{phaseLabel[memory.phase]}</Tag> : null}>{memory ? <Descriptions size="small" column={1}><Descriptions.Item label="任务">{taskLabel[memory.intent] || memory.intent}</Descriptions.Item><Descriptions.Item label="已收集">{Object.entries(memory.slots).filter(([, value]) => value !== null && value !== undefined && value !== "").map(([key, value]) => <Tag key={key}>{`${key}: ${String(value)}`}</Tag>)}</Descriptions.Item><Descriptions.Item label="仍需补充">{memory.missing_slots.length ? memory.missing_slots.map((slot) => <Tag color="orange" key={slot}>{({ order_id: "订单号", request_type: "售后类型", case_id: "售后单编号", time_slot: "取件时段" } as Record<string, string>)[slot] || slot}</Tag>) : "无"}</Descriptions.Item><Descriptions.Item label="版本">v{memory.version}</Descriptions.Item></Descriptions> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无活跃任务" />}</Card>
          <Card title="本次受控执行" extra={activeItem?.result && resultTag(activeItem.result.status)}>{activeItem ? <><Descriptions size="small" column={1}><Descriptions.Item label="Run"><Typography.Text copyable>{activeItem.result?.run_id}</Typography.Text></Descriptions.Item><Descriptions.Item label="业务对象">{activeItem.result?.case_id ? `售后单 #${activeItem.result.case_id}` : activeItem.result?.ticket_id ? `审核单 #${activeItem.result.ticket_id.slice(0, 8)}` : "仅查询或补充信息"}</Descriptions.Item></Descriptions><Steps direction="vertical" size="small" current={activeItem.tools?.length || 0} items={(activeItem.tools || []).map((tool) => ({ title: labelForTool[tool.tool_name] || tool.tool_name, description: <Space><Tag color={tool.status === "succeeded" ? "green" : "red"}>{tool.status}</Tag><span>{tool.latency_ms} ms</span><Tooltip title={JSON.stringify(tool.arguments_json)}><Typography.Text type="secondary">参数</Typography.Text></Tooltip></Space>, status: tool.status === "succeeded" ? "finish" : "error" }))} /></> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="发送消息后显示工具 Trace" />}</Card>
          <Card title="为什么值得信任？"><Space direction="vertical"><Typography.Text>✓ 任务槽位由数据库显式保存</Typography.Text><Typography.Text>✓ 高风险写操作必须确认</Typography.Text><Typography.Text>✓ Agent 不能直连数据库</Typography.Text><Typography.Text>✓ 只检索已发布的知识版本</Typography.Text><Typography.Text>✓ 每次调用都可关联审计与 Trace</Typography.Text></Space></Card>
          <Card title="演示订单"><Typography.Paragraph><b>O1001</b>：签收 3 天、未拆封，可退款</Typography.Paragraph><Typography.Paragraph><b>O1003</b>：质量问题，可申请人工审核</Typography.Paragraph><Typography.Text type="secondary">这是开发演示数据，不代表真实商城政策。</Typography.Text></Card>
        </Space></Col>
      </Row>
    </Layout.Content>
  </Layout></>;
}
