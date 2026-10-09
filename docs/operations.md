# 运行与维护

## 配置与密钥

复制 `.env.example` 为 `.env` 并在本地填写服务配置。进程环境变量可覆盖配置文件。
模板不包含真实凭证，`.env`、环境配置副本和运行数据被 Git 忽略。
聊天模型与嵌入模型可分别使用不同服务；聊天密钥不应复制到嵌入服务配置。

`.env.example` 提供 DeepSeek 聊天接口和本地 Ollama 嵌入接口的配置字段。
调用模型前需确认账号可用模型、网络连接与工具调用支持。
修改聊天配置后重启服务；修改嵌入模型后先重建索引再重启。

## 知识库更新

默认源目录为 `output/pdf`。新增或替换文本 PDF 后运行：

```bash
python -m scripts.ingest
```

命令返回 `reused=true` 时表示当前指纹与已有索引一致，未重新生成文档向量。
需要强制生成新快照时：

```bash
python -m scripts.ingest --force
```

索引和 manifest 默认保存于 `runtime/knowledge`。
扫描件需先进行 OCR。修改 `data/policies.json` 后，可执行
`python -m scripts.create_policy_pdf` 更新随项目提供的政策 PDF，再重新建库。

## 服务启动与健康检查

```bash
python -m uvicorn supportflow.api:app --host 127.0.0.1 --port 8000
```

当前只支持单个服务进程，不配置多个 worker。
`GET /health` 返回服务状态、Agent 模式和嵌入模式；它不证明模型 API 已成功执行。
端到端验证可发送只读政策查询，核对 `search_policy` 工具事件、来源引用和服务商返回的用量。

端口被占用时，先确认现有服务是否属于当前项目，再停止旧进程或选择其他端口。
配置修改后必须重启旧进程，刷新页面不足以重新加载后端配置。

## 沙箱数据

| 身份 | 订单 | 初始状态 |
| --- | --- | --- |
| Alice | O1001 | 签收 3 天，电子产品 |
| Alice | O1002 | 签收 45 天，已超过期限 |
| Alice | O1003 | 已发货，尚未签收 |
| Bob | O2001 | 签收 2 天，电子产品 |

首次初始化时按 Asia/Shanghai 的业务日期生成签收时间；后续启动保留已有日期。
新建会话不清除工单。公开沙箱令牌仅用于本地访问，不作为生产身份认证。

## 回归检查

```bash
python -m pytest
python -m ruff check .
python -m scripts.evaluate
```

离线评测使用规则 Agent 和哈希向量，真实模型评测使用 `--mode llm`。
评测业务数据按案例隔离，报告默认写入 `evals/results.local.json` 并被 Git 忽略。
报告检查状态、工具、引用、回答关键内容、工单数量及未确认写入。

本地嵌入接口验证命令为 `python -m scripts.test_embeddings`，测试索引和报告写入
`runtime/embedding-checks`，不切换服务当前使用的知识库。
`scripts.demo` 在独立数据与知识库目录中执行离线流程验证。

## 数据与部署

`runtime` 包含业务数据、对话、工具结果和向量快照，按实际数据治理要求保存与备份。
当前没有自动归档旧索引或清理历史会话；移除快照前需确认它未被 manifest 或运行进程使用。
模型调用失败后可通过恢复接口继续执行，已有审批和工单应保留以支持幂等检查。

公开部署前需实现正式认证、限流、备份恢复、上下文管理及并发容量验证。
当前不对接真实支付、退款、物流或库存，政策与订单均为沙箱数据。
