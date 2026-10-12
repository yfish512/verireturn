# VeriReturn Recovery 案例集

20 组案例、40 条真实模型轨迹。覆盖正式评测的全部 12 个故障变体，并展示客户分支、失败结果与 v3 修复专项。图片从归档证据生成，不是产品页面截图；不调用模型，不修改历史统计。

[离线交互案例集](images/recovery-gallery/casebook.html)：下载后用浏览器打开，可按类型筛选并查看逐步对照。

![案例总览](images/recovery-gallery/overview.png)

| 案例 | 故障或客户选择 | 数据版本 | 普通模型 | 恢复模型 |
| --- | --- | --- | --- | --- |
| [案例 01](#案例-01) | 一次读取失败 | v2 正式 | 自主完成 | 自主完成 |
| [案例 02](#案例-02) | 连续两次读取失败 | v2 正式 | 转人工 | 自主完成 |
| [案例 03](#案例-03) | 持续不可用 | v2 正式 | 转人工 | 转人工 |
| [案例 04](#案例-04) | 核验过程中再次失败 | v2 正式 | 转人工 | 自主完成 |
| [案例 05](#案例-05) | 提交前失败 | v2 正式 | 自主完成 | 自主完成 |
| [案例 06](#案例-06) | 渠道接受，本地未回写 | v2 正式 | 自主完成 | 自主完成 |
| [案例 07](#案例-07) | 执行成功，响应丢失 | v2 正式 | 自主完成 | 自主完成 |
| [案例 08](#案例-08) | 状态查询暂时不可用 | v2 正式 | 自主完成 | 自主完成 |
| [案例 09](#案例-09) | 原 SKU 恢复库存 | v2 正式 | 客户协助完成 | 客户协助完成 |
| [案例 10](#案例-10) | 选择替换 SKU | v2 正式 | 客户协助完成 | 客户协助完成 |
| [案例 11](#案例-11) | 换货改为退款 | v2 正式 | 客户协助完成 | 客户协助完成 |
| [案例 12](#案例-12) | 客户等待补货 | v2 正式 | 等待补货 | 等待补货 |
| [案例 13](#案例-13) | 客户选择人工处理 | v2 正式 | 转人工 | 转人工 |
| [案例 14](#案例-14) | 客户拒绝新方案 | v2 正式 | 等待客户确认 | 等待客户确认 |
| [案例 15](#案例-15) | 客户尚未回复 | v2 正式 | 等待客户确认 | 等待客户确认 |
| [案例 16](#案例-16) | 客户撤回任务 | v2 正式 | 已取消 | 已取消 |
| [案例 17](#案例-17) | 模型错误判断已完成 | v2 正式 | 转人工 | 未完成 / 判定不通过 |
| [案例 18](#案例-18) | 模型服务超时 | v2 正式 | 自主完成 | 模型服务异常 |
| [案例 19](#案例-19) | v3：持续故障转人工 | v3 专项 | 转人工 | 转人工 |
| [案例 20](#案例-20) | v3：预算耗尽兜底 | v3 专项 | 转人工 | 预算耗尽 / 系统兜底 |

## 案例 01

**一次读取失败** — 两组均重试成功，展示基础恢复。

来源：`formal-v2` · `read_once-001:r0`。

![一次读取失败](images/recovery-gallery/01-read_once-001-formal-v2.png)

## 案例 02

**连续两次读取失败** — 普通模型转人工；恢复模型在预算内再次读取。

来源：`formal-v2` · `read_twice-001:r0`。

![连续两次读取失败](images/recovery-gallery/02-read_twice-001-formal-v2.png)

## 案例 03

**持续不可用** — 两组均转人工，未将开放任务计为完成。

来源：`formal-v2` · `read_persistent-001:r0`。

![持续不可用](images/recovery-gallery/03-read_persistent-001-formal-v2.png)

## 案例 04

**核验过程中再次失败** — 恢复模型再次核验，普通模型提前转人工。

来源：`formal-v2` · `read_verification-001:r0`。

![核验过程中再次失败](images/recovery-gallery/04-read_verification-001-formal-v2.png)

## 案例 05

**提交前失败** — 取得未执行证据后重试，再核对渠道结果。

来源：`formal-v2` · `write_before_commit-001:r0`。

![提交前失败](images/recovery-gallery/05-write_before_commit-001-formal-v2.png)

## 案例 06

**渠道接受，本地未回写** — 先核验渠道事实，避免盲目重新提交。

来源：`formal-v2` · `write_remote_only-001:r0`。

![渠道接受，本地未回写](images/recovery-gallery/06-write_remote_only-001-formal-v2.png)

## 案例 07

**执行成功，响应丢失** — 查询已有执行结果，最终核对账本与渠道。

来源：`formal-v2` · `write_response_lost-001:r0`。

![执行成功，响应丢失](images/recovery-gallery/07-write_response_lost-001-formal-v2.png)

## 案例 08

**状态查询暂时不可用** — 核验失败后继续查询，保持写入结果未决。

来源：`formal-v2` · `write_query_unavailable-001:r0`。

![状态查询暂时不可用](images/recovery-gallery/08-write_query_unavailable-001-formal-v2.png)

## 案例 09

**原 SKU 恢复库存** — 刷新业务状态并重新获得客户确认。

来源：`formal-v2` · `stock_restored-001:r0`。

![原 SKU 恢复库存](images/recovery-gallery/09-stock_restored-001-formal-v2.png)

## 案例 10

**选择替换 SKU** — 客户确认允许的替换方案后完成履约。

来源：`formal-v2` · `stock_alternative-001:r0`。

![选择替换 SKU](images/recovery-gallery/10-stock_alternative-001-formal-v2.png)

## 案例 11

**换货改为退款** — 新授权关联退款单，按退款事实判定完成。

来源：`formal-v2` · `stock_refund-001:r0`。

![换货改为退款](images/recovery-gallery/11-stock_refund-001-formal-v2.png)

## 案例 12

**客户等待补货** — 保持开放任务；等待补货不等于业务完成。

来源：`formal-v2` · `stock_wait_human-001:r0`。

![客户等待补货](images/recovery-gallery/12-stock_wait_human-001-formal-v2.png)

## 案例 13

**客户选择人工处理** — 工单已创建，单独统计人工处理。

来源：`formal-v2` · `stock_wait_human-002:r0`。

![客户选择人工处理](images/recovery-gallery/13-stock_wait_human-002-formal-v2.png)

## 案例 14

**客户拒绝新方案** — 确认被拒绝，不擅自执行退款或换货。

来源：`formal-v2` · `stock_wait_human-003:r0`。

![客户拒绝新方案](images/recovery-gallery/14-stock_wait_human-003-formal-v2.png)

## 案例 15

**客户尚未回复** — 保留待确认状态，不把沉默当成授权。

来源：`formal-v2` · `stock_wait_human-004:r0`。

![客户尚未回复](images/recovery-gallery/15-stock_wait_human-004-formal-v2.png)

## 案例 16

**客户撤回任务** — 恢复任务取消，未完成业务。

来源：`formal-v2` · `stock_wait_human-005:r0`。

![客户撤回任务](images/recovery-gallery/16-stock_wait_human-005-formal-v2.png)

## 案例 17

**模型错误判断已完成** — 核验库存不等于完成换货；独立判定器拒绝计分。

来源：`formal-v2` · `read_persistent-011:r0`。

![模型错误判断已完成](images/recovery-gallery/17-read_persistent-011-formal-v2.png)

## 案例 18

**模型服务超时** — 规划 API 超时显式记录，未切回规则冒充模型成功。

来源：`formal-v2` · `write_query_unavailable-018:r0`。

![模型服务超时](images/recovery-gallery/18-write_query_unavailable-018-formal-v2.png)

## 案例 19

**v3：持续故障转人工** — 模型选择转人工；专项结果不替换正式效果统计。

来源：`repair-v3-final` · `read_persistent-005:r0`。

![v3：持续故障转人工](images/recovery-gallery/19-read_persistent-005-repair-v3-final.png)

## 案例 20

**v3：预算耗尽兜底** — 系统创建工单，不能计为模型自主恢复。

来源：`repair-v3-final` · `read_persistent-001:r0`。

![v3：预算耗尽兜底](images/recovery-gallery/20-read_persistent-001-repair-v3-final.png)

## 数据与生成

案例选取固定在生成脚本中，第 01–12 组取每个变体的第一条，第 13–16 组覆盖不同客户分支，第 17–18 组明确保留模型误判与服务超时，第 19–20 组区分模型转人工与系统预算兜底。不是随机抽样，也不能用这些案例重新计算成功率。

v2 的持续故障脚本只约束原始读取，核验工具仍可能读到库存；该环境局限在第 17 组也有体现。v3 修复专项补强了这一点，与正式实验独立展示。开放任务、转人工、模型异常和预算停止均不计完整业务完成。

```sh
conda activate verireturn
python -m scripts.verify_recovery_evidence
python -m scripts.generate_recovery_gallery
```

依赖已安装的 Playwright Chromium；已有浏览器可用 `PLAYWRIGHT_CHROMIUM_PATH` 指定路径。不需要 API、数据库或密钥。生成器校验源文件 SHA256、配对身份、故障覆盖及最终业务事实；浏览器验证筛选、案例切换和无横向溢出。

[全部实测统计](recovery-latest-report.md) · [案例来源与图片清单](images/recovery-gallery/manifest.json) · [项目成果展示](showcase.md)
