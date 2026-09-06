# MailFlow Agent

**证据约束的邮件自动处理：低风险 FAQ 自动回复，复杂请求人工审核。**

小组项目基础上的个人扩展，维护者：吴沈宇。源项目为 [Automatic-Email-Responder](https://github.com/Carbohydrate1001/Automatic-Email-Responder)，基线提交 `f7d274f`。保留原作者 MIT 许可证与来源说明；不将整个小组成果声称为个人独立完成。

![邮件工作台](docs/mailflow-workbench.png)

## 五分钟运行

Python 3.10+，在仓库根目录执行：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e .
python -m mailflow.app
```

打开 http://localhost:5087 。点击「普通 FAQ」「混合退款请求」等示例，查看路由依据、人工编辑/拒绝和审计轨迹。默认使用确定性规则，不需要 API、Outlook 登录或真实邮件；**“已模拟发送”不是发送到真实收件人**。

## 个人扩展做了什么

| 能力 | 小组基础 | 此版本新增或修复 |
| --- | --- | --- |
| 邮件工作流 | 意图分类、生成回复、Microsoft Graph、人工审核、SQLite | 独立的轻量工作台与可演示的分流闭环 |
| 自动发送 | 原代码已有置信阈值和 rubric 评分，并非一律人工审核 | 分数不能覆盖风险规则；唯一知识依据与意图一致才可放行 |
| 回复可信度 | LLM 生成草稿并检查质量 | 自动回复使用本地审核 FAQ 的原文；模型自由生成内容不直接发送 |
| 发送状态 | 原流程无 Graph 对象也可能记录 auto_sent | 区分 ready / sending / simulated_sent / sent / send_unknown；记录处理轨迹 |
| 并发和异常 | 拉取时查重；发送失败可能重试 | SQLite 原子认领、防重复提交、审核版本冲突检测、不盲重试未知发送结果 |
| 验证 | 小组原有测试 | 新的流程回归、20 条合成政策用例、模型实测记录与离线重放 |

新增核心在 `mailflow/`；`backend/` 保留小组后端及少量修复供对照与集成。新的工作台是默认入口，旧 Vue 前端和历史数据库不随此发布版提供。仓库没有 API key、真实邮箱 token 或旧邮件数据库。

## 放行条件

```text
来信 → 规则匹配 / 可选 LLM 提议 → 分数与意图校验
                              → 风险 / 附件 / 自动邮件检查
                              → 唯一已审核 FAQ → ready → 原子认领 → 发送适配器
                              → 不满足任一条件 → 人工审核 → 版本检查 → ready
超时或无法确认 → send_unknown，人工核实；不自动重发
```

高分只是输入信号，不是“正确率”。当前自动处理范围刻意限定为两类演示 FAQ：**如何查询物流**、**在哪里查看预计送达时间**。它们不查询具体订单，不承诺交期。退款、价格、支付、投诉、异常、具体订单、可疑指令、敏感号码、附件及自动邮件进入人工队列。规则错误或模型格式异常也进入人工。

知识库位于 `mailflow/policy.py`。它包含演示内容、版本和 SHA-256 指纹；正式使用必须替换成企业审核的内容。当前关键词检查是可解释原型，**不是对所有语言、混合意图和提示注入的完备防御**。扩展自动处理范围需要新的证据源、业务审核与真实标注数据验证。

## 可选模型模式

在本地设置 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，参考 `.env.example.mailflow`。程序读取环境变量，不自动读取桌面文件。

```bash
python -m mailflow.app --live-model --max-model-calls 3
```

该模式会把输入邮件的**主题和正文**发送到你配置的 HTTPS 模型服务。每次分析最多一个请求、900 completion tokens，无自动重试；每次服务启动最多 3 次调用，重启会重置计数。即便开启模型，网页发送仍为模拟。用完预算后转人工；界面显示模式与调用计数。

`examples/live-smoke.json` 记录一封合成 FAQ 的真实模型响应：`gpt-5.6-luna`，服务商报告 199 tokens，路由为 auto。首次接口兼容性失败未返回用量，随后修正请求头完成此实测；这不是稳定性或准确率统计。

## 发送适配器与可靠性边界

`Store.dispatch()` 支持模拟适配器，另有 `GraphTransport` 可接入原小组 `GraphService`。真实适配器必须显式 `enabled=True`，调用方负责邮箱身份验证、收件账户匹配与部署授权；网页演示不提供真实发送入口。本次仅用 Mock 验证 Graph 契约，**未连接真实邮箱发信**。

消息按 `(mailbox, message_id)` 去重；重复 ID 内容变化会拒绝。发送前事务认领，避免两个 worker 同时发送。服务商超时或响应缺失保留 `send_unknown`；进程在发送中崩溃会保留 `sending`。这些状态需要操作人员向服务商核实后恢复，当前不提供一键强制重试。这里提供的是“避免自动重复发送”的保障，**不宣称外部邮件端到端 exactly-once 或送达回执**；`sent` 表示服务商接受请求。

旧 `backend/` 的补丁包括校验异常转人工、rubric 不覆盖低分类置信、没有发送连接不记成功、标记已读失败不撤销发送成功。旧入口默认关闭自动发送，其审核/批量接口尚未全部迁移到新 outbox；它是集成参考，不是本次验证过的生产入口。

## 验证与复现

```bash
pip install -e ".[test,legacy]"
python -m pytest tests_mailflow
python -m mailflow.evaluate --out results/policy-evaluation.json
```

当前新增测试覆盖策略、网页闭环、模型错误、并发、重启后的未知发送状态和旧流程关键回归。结果见 `docs/VALIDATION.md`。

20 条人工编写的**合成回归用例**中，5 条允许自动处理，15 条进入审核，全部符合预设标签。仅用 `score >= 0.9` 的消融规则会误放行其中 13 条；这只是说明阈值不能替代政策约束，**不是与原项目完整多阶段流水线比较，也不是生产准确率、节省人力或真实自动回复率**。

## 后续发展

进一步的实质升级是接入经过身份核验的订单只读查询、扩充真实标注评测集、估计分类置信的可靠性、完善服务商发送结果核实流程，以及把新队列完整接入原 Outlook 登录入口。所有性能或准确率数字应以这些验证结果为准。
