import { useMemo, useState } from 'react';
import { Alert, Card, Col, Descriptions, Row, Select, Space, Table, Tag, Timeline, Typography } from 'antd';
import type { EvaluationResult } from './types';
const names:Record<string,string>={baseline:'遇错停止',fixed_retry:'固定重试',react_baseline:'普通模型 Agent',recovery_agent:'恢复 Agent'};
type Metrics = { business_completion_rate?:number; autonomous_success_rate?:number; assisted_success_rate?:number; safe_handoff_rate?:number; system_escalation_rate?:number; budget_exhausted_rate?:number; average_model_calls?:number; average_extra_tool_calls?:number; business_violations?:number };
export function RecoveryComparison({report,rows}:{report:Record<string,unknown>|null;rows:EvaluationResult[]}) {
  const ids=useMemo(()=>Array.from(new Set(rows.map(r=>String(r.evidence_json.episode_id||r.case_id.split(':')[0])))),[rows]);
  const [selected,setSelected]=useState<string>();
  const active=selected || ids[0];
  const group=rows.filter(r=>String(r.evidence_json.episode_id||r.case_id.split(':')[0])===active);
  const metrics=Object.entries((report?.metrics_by_strategy || {}) as Record<string,Metrics>).map(([strategy,m])=>({strategy,...m}));
  const percent=(v?:number)=>v===undefined?'—':`${(v*100).toFixed(1)}%`;
  return <Space direction="vertical" style={{width:'100%'}}>
    <Alert type="info" showIcon message={`规划模式：${report?.planner_mode || '待生成'} · 任务：${report?.episodes || '—'} · 结果：${report?.total || rows.length}`} description="用户协助完成、等待二次确认、等待补货及转人工分别计分。工具调用增量以配对无故障路径为参照。" />
    <Table scroll={{x:1100}} size="small" rowKey="strategy" dataSource={metrics} pagination={false} columns={[
      {title:'策略',dataIndex:'strategy',render:(s:string)=>names[s]||s},
      {title:'业务完成',dataIndex:'business_completion_rate',render:percent},
      {title:'自主完成',dataIndex:'autonomous_success_rate',render:percent},
      {title:'用户协助完成',dataIndex:'assisted_success_rate',render:percent},
      {title:'Agent 转人工',dataIndex:'safe_handoff_rate',render:percent},
      {title:'预算耗尽',dataIndex:'budget_exhausted_rate',render:percent},
      {title:'系统升级人工',dataIndex:'system_escalation_rate',render:percent},
      {title:'模型调用',dataIndex:'average_model_calls'},
      {title:'工具增量',dataIndex:'average_extra_tool_calls'},
      {title:'业务违规',dataIndex:'business_violations'},
    ]} />
    <Select aria-label="选择评测任务" value={active} onChange={setSelected} style={{width:'100%'}} options={ids.map(id=>({value:id,label:id}))} />
    <Row gutter={[12,12]}>{group.map(row=>{
      const evidence=row.evidence_json;
      const trace=(evidence.trace || []) as Array<{sequence_no:number;kind:string;detail:Record<string,unknown>}>;
      return <Col key={row.id} xs={24} lg={12}><Card title={names[String(evidence.strategy)]||row.case_id} extra={<Tag>{row.status}</Tag>}>
        <Descriptions size="small" column={2}><Descriptions.Item label="工具调用">{String(evidence.tool_calls??'—')}</Descriptions.Item><Descriptions.Item label="模型调用">{String(evidence.model_calls??'—')}</Descriptions.Item><Descriptions.Item label="耗时">{row.latency_ms} ms</Descriptions.Item><Descriptions.Item label="判定">{evidence.matches_expected?'符合预期':'未达预期'}</Descriptions.Item></Descriptions>
        <Timeline style={{marginTop:16}} items={trace.map(step=>({children:<><Typography.Text>{step.sequence_no} · {step.kind}</Typography.Text><Typography.Paragraph type="secondary">{String(step.detail.reason || (step.detail.result as {message?:string})?.message || step.detail.action || '')}</Typography.Paragraph></>}))} />
        <details><summary>最终业务事实</summary><pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify(evidence.final_state,null,2)}</pre></details>
      </Card></Col>;
    })}</Row>
  </Space>;
}
