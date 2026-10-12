import { useEffect, useState } from 'react';
import { Alert, Button, Card, Descriptions, Modal, Space, Tag, Timeline, Typography, message } from 'antd';
import { api } from './api';
import type { CustomerRecovery, Resolution } from './types';
const statuses:Record<string,string>={running:'恢复处理中',pending:'待恢复',awaiting_customer:'等待选择',applied:'方案已执行',waiting_stock:'等待补货',human:'人工处理中',cancelled:'已停止',model_error:'模型服务异常',budget_exhausted:'自动恢复预算已用尽',tool_recovered:'工具已恢复',completed:'业务已完成',restock_available:'补货后待确认'};
const names: Record<string,string> = { refund:'改为退款', exchange:'继续换货', wait:'等待补货', human:'转人工处理' };
export function RecoveryPanel({ actor, id, onChanged }: { actor:string; id:string; onChanged?:()=>void }) {
  const [data,setData] = useState<CustomerRecovery>();
  const [proposal,setProposal] = useState<Resolution>();
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState<string>();
  const refresh=()=>api.customerRecovery(actor,id).then(setData).catch((e:Error)=>setError(e.message));
  useEffect(()=>{ void refresh(); },[actor,id]);
  async function choose(option:{option:string;replacement_sku?:string}) {
    if (!data?.case_id) return;
    setBusy(true);
    try { setProposal(await api.proposeResolution(actor,data.case_id,option)); }
    catch(e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  async function confirm(approved:boolean) {
    if(!proposal)return;
    setBusy(true);
    try { const result=await api.confirmResolution(actor,proposal.id,proposal.version,approved);setProposal(undefined);await refresh();onChanged?.();message.success(result.status==='waiting_stock'?'已登记等待补货':'方案已处理'); }
    catch(e) {setError((e as Error).message);setProposal(undefined);await refresh();} finally{setBusy(false);}
  }
  async function resume() {
    if(!data)return;
    setBusy(true);setError(undefined);
    try {await api.resumeRecovery(actor,id,data.version);await refresh();onChanged?.();}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function cancel() {
    if(!data)return;
    setBusy(true);
    try {await api.cancelRecovery(actor,id,data.version);await refresh();onChanged?.();}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  return <Card size="small" title="售后恢复" extra={<Tag>{statuses[data?.status || ''] || data?.status || '加载中'}</Tag>}>
    {error && <Alert type="error" message={error} showIcon />}
    <Space wrap>{data?.status!=='cancelled' && data?.options?.map(o=><Button key={`${o.option}:${o.replacement_sku||''}`} loading={busy} onClick={()=>void choose(o)}>{names[o.option]}{o.replacement_sku?` · ${o.replacement_sku}`:''}</Button>)}</Space>
    <Space style={{marginTop:12}}><Button size="small" onClick={()=>void refresh()}>刷新状态</Button>{data && ['pending','running','model_error','stopped'].includes(data.status) && <Button size="small" loading={busy} onClick={()=>void resume()}>继续恢复</Button>}{data?.status!=='cancelled' && <Button size="small" danger loading={busy} onClick={()=>void cancel()}>停止自动恢复</Button>}</Space>
    <Timeline style={{marginTop:16}} items={(data?.steps||[]).map(s=>({children:<><Typography.Text>{s.kind}</Typography.Text><Typography.Paragraph type="secondary">{String(s.detail.reason || (s.detail.result as {message?:string})?.message || s.detail.action || '')}</Typography.Paragraph></>}))} />
    <Modal open={Boolean(proposal)} title="确认新的售后方案" okText="确认执行" cancelText="拒绝方案" confirmLoading={busy} onOk={()=>void confirm(true)} onCancel={()=>void confirm(false)}>
      <Descriptions column={1}><Descriptions.Item label="方案">{proposal && names[proposal.option]}</Descriptions.Item><Descriptions.Item label="替换商品">{proposal?.replacement_sku || '原订单商品'}</Descriptions.Item><Descriptions.Item label="有效期">{proposal?.expires_at}</Descriptions.Item></Descriptions>
      <Typography.Paragraph>确认后执行所选方案。库存再次变化时需要重新选择和确认。</Typography.Paragraph>
    </Modal>
  </Card>;
}
