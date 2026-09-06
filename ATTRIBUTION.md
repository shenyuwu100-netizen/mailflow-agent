# 来源与贡献边界

- 上游：[Carbohydrate1001/Automatic-Email-Responder](https://github.com/Carbohydrate1001/Automatic-Email-Responder)
- 使用基线：`f7d274f`（完整提交信息见 `docs/VALIDATION.md`）
- 原许可证：MIT，Copyright (c) 2026 Centauri_C，完整保留在 `LICENSE`。
- 本仓库为吴沈宇在参与的小组项目基础上维护的个人扩展，不等于上游全部工作由吴沈宇独立完成。
- 此次新增：`mailflow/`、`tests_mailflow/`、合成评测与模型实测记录、新工作台及文档。
- 此次修改的上游文件：`backend/config.py`、`backend/models/database.py`、`backend/services/reply_service.py`。
- 发布时去除了上游缓存、数据库、开发日志、历史报告和演示发信脚本；没有改写上游仓库。

## v0.2 个人扩展

两阶段模型分类/生成、六份知识内容的词项检索与引用校验、持久化请求和调用预算账本、实时处理状态与可编辑草稿、API 集成测试和离线重放。
