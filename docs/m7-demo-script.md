# M7 演示手册

在项目根目录执行。Docker 不参与此流程。

```sh
M7_AGENT_MODE=live sh scripts/start_m7_local.sh
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  /data/user004/miniforge3/bin/mamba run -n verireturn python -m scripts.demo_m7
DATABASE_URL=postgresql+psycopg://verireturn@127.0.0.1:54329/verireturn \
  /data/user004/miniforge3/bin/mamba run -n verireturn python -m scripts.verify_m7_demo
```

结束后执行 `sh scripts/stop_m7_local.sh`。它只停止由 M7 启动脚本记录的进程；本地 PostgreSQL 保留运行，以便继续开发。

若现场网络不稳定，使用 `M7_AGENT_MODE=deterministic` 启动。若某批次被人工中断，使用新 key，例如 `M7_RUN_KEY=interview-20260914` 同时运行 demo 和 verifier；旧批次仍保留为审计事实。

## 三分钟讲解顺序

1. 打开 `http://127.0.0.1:8000/docs`，说明 Agent 只有受控工具接口，没有直接数据库写权限。
2. 展示 Demo 输出的知识问答 run：知识检索仅解释已发布政策，不能改变资格或金额。
3. 展示退款 run：创建后售后单为 `pending_confirmation`，确认后才进入取件流程；重复命令由幂等键收敛。
4. 展示退款 case 的四个已验签履约事件和 `completed` 状态。
5. 打开 `http://127.0.0.1:5173`，查看质量争议审核时间线、M6 指标、告警历史和 Trace 下钻。
6. 运行 verifier，强调它检查的是售后单、确认、工具调用、Outbox、Webhook、审核事件、指标快照和告警事件。

演示输出存放在被 Git 忽略的 `.local/m7/`，其中包含业务 ID，便于在运营台的 Trace 对话框中粘贴 `refund_run_id` 或 `refund_case_id`。
