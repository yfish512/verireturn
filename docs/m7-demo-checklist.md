# M7 演示前检查清单

- [ ] 在项目根目录，Miniforge 环境名为 `verireturn`。
- [ ] `.env` 存在，包含 DeepSeek 配置与 `FULFILLMENT_WEBHOOK_SECRET`；不要在终端或截图中展示其值。
- [ ] `sh scripts/start_m7_local.sh` 输出 API 和运营台地址。
- [ ] `curl -fsS http://127.0.0.1:8000/health` 返回 `{"status":"ok"}`。
- [ ] 执行 `sh scripts/demo_m7.sh`，保存 `.local/m7/demo-portfolio-v1.json` 中的 run/case ID。
- [ ] 执行 `python -m scripts.verify_m7_demo`，确认 JSON 中 `status` 为 `passed`。
- [ ] 在运营台的 Trace 对话框粘贴 `refund_run_id`，确认能看到工具、审计、履约和 Outbox。
- [ ] 截图前确认屏幕上没有 `.env`、密钥、终端环境变量或非演示订单数据。

如果演示批次意外中断，不要修改已有售后、审核或履约记录。使用 `M7_RUN_KEY=interview-YYYYMMDD` 重新执行 demo 与 verifier，即可留下新批次的独立审计证据。
