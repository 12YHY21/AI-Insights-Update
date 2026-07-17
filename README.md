# AI Insights Update

每周一自动采集 AI 前沿论文、官方动态与工程文章；每月1日重新阅读上月推送并对照当月新闻做一次前沿性复盘，最后通过飞书群自定义机器人推送。

## 当前能力

- 北京时间每周一 `08:17` 发送周报，每月1日 `09:17` 发送上个自然月的复盘。
- 如果每月1日恰好是周一，当天只发送月报，避免同一天收到两份内容。
- 采集16个来源，包括 arXiv、OpenAI、Google/DeepMind、Anthropic、DeepSeek、Qwen、Microsoft、Hugging Face、GitHub、AWS 和 NVIDIA。
- 先分批初筛，再将 Top 候选放进同一个上下文统一终审，避免不同批次分数不可比。
- 严格校验 DeepSeek 返回的 JSON、文章 ID 和字段类型，返回不完整时自动重试。
- 按论文、官方动态、开源工程和部署实践设置配额，避免简报被论文占满。
- 标题近重复和 AI 语义重复双重去重。
- 优先读取 arXiv HTML 全文，自动提取 GitHub、Hugging Face 等代码资源链接。
- 飞书卡片分片可恢复：中途失败后记录已成功分片，下次从未完成位置继续。
- 永久归档简报与运行报告，并记录来源健康、候选评分、淘汰原因和 Token 用量。
- GitHub Actions 使用最小权限、步骤级 Secrets、锁定依赖哈希和完整 Action SHA。
- 月报重新抓取此前推送的原文，并把基础模型重要版本、能力边界变化、训练/推理范式和显著成本变化列为高优先级；品牌宣传和普通客户案例不会仅因品牌获得高分。

## 工作流程

```text
16 个 RSS / Sitemap 来源
        ↓
48 小时重叠窗口 + 历史去重
        ↓
标题近重复过滤
        ↓
DeepSeek 分批初筛（默认 V4 Flash）
        ↓
Top 20 + 各类别候选统一终审
        ↓
类别配额选择最多 6 条
        ↓
全文提取 + DeepSeek 深度总结（默认 V4 Pro）
        ↓
可恢复飞书分片投递
        ↓
状态、简报、报告提交回仓库
```

月报在此基础上读取上个自然月所有已推送条目，重新抓取原文，再从官方动态、开源工程和部署新闻中选出最多10条对照材料。DeepSeek 会为每项给出“仍属前沿 / 重要但已常规 / 影响有限 / 待验证”结论，并生成真正值得记住的进展、本月新大事、降温项和下月观察清单。类似主流 GPT、Gemini、Claude、DeepSeek 或 Qwen 的重要版本发布，只要官方材料能证明能力、可用性或性价比发生明显变化，就会进入高优先级比较。

## 目录结构

```text
config/settings.json          兴趣、来源、阈值、配额和数量配置
data/state.json               去重状态及未完成投递进度
digests/YYYY/MM/              已发送简报永久归档
reports/YYYY/MM/              来源健康、评分和 Token 用量报告
digests/monthly/YYYY/         已发送月度复盘归档
reports/monthly/YYYY/         月度复评明细和新闻对照报告
output/                       当前运行预览和诊断，仅作为 Artifact
src/collectors.py             RSS、Sitemap、正文及资源链接提取
src/ai.py                     初筛、终审、去重、总结和用量统计
src/feishu.py                 飞书签名、重试和卡片推送
src/state.py                  去重与可恢复投递状态机
src/main.py                   任务编排入口
.github/workflows/digest.yml  定时任务和独立状态持久化任务
```

## 一、飞书机器人

1. 在飞书中新建一个群，可以只有自己。
2. 打开群设置 → 群机器人 → 添加机器人 → 自定义机器人。
3. 复制 Webhook 地址。
4. 推荐开启“签名校验”并复制签名密钥。

官方说明：[飞书自定义机器人使用指南](https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN)

## 二、GitHub Repository secrets

进入：

`Settings → Secrets and variables → Actions → Repository secrets`

添加：

| Secret | 必需 | 用途 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 是 | DeepSeek API Key |
| `FEISHU_WEBHOOK_URL` | 是 | 飞书自定义机器人 Webhook |
| `FEISHU_WEBHOOK_SECRET` | 否 | 开启签名校验后填写 |

Secrets 只注入需要它们的步骤：采集和测试阶段无法读取业务凭证。

可在 `Repository variables` 添加非敏感模型覆盖：

| Variable | 默认值 |
|---|---|
| `DEEPSEEK_RANK_MODEL` | `deepseek-v4-flash` |
| `DEEPSEEK_SUMMARY_MODEL` | `deepseek-v4-pro` |

## 三、手动验证

进入 `Actions → AI Insights Update → Run workflow`，每次选择一个模式：

1. `collect-only`：只检查信息源，不读取 Secrets。
2. `dry-run`：生成本周简报预览，不推送、不修改状态。
3. `send`：真实发送本周简报，并持久化状态、简报和报告。
4. `monthly-dry-run`：用“本月至今”的历史推送和新闻生成月报预览，不推送。
5. `monthly-send`：真实发送“上个自然月”的复盘；一般只让每月1日的定时任务使用。

查看内容效果不需要再次 `send`。运行 `dry-run` 或 `monthly-dry-run` 后，在运行详情底部下载 `latest-digest-*` Artifact，打开其中的 `latest_digest.md` 即可；只有要检查飞书群里的实际卡片排版时才需要真实发送。预览 Artifact 保留30天，正式简报永久保存在 `digests/`，对应报告保存在 `reports/`。

## 四、本地 Conda Py312

```powershell
conda activate Py312
python -m pip install --require-hashes -r requirements.lock
python -m unittest discover -s tests -v
python -m src.main --collect-only
```

如果 PowerShell 未初始化 Conda：

```powershell
& 'D:\Miniconda\Scripts\conda.exe' run -n Py312 python -m pip install --require-hashes -r requirements.lock
& 'D:\Miniconda\Scripts\conda.exe' run -n Py312 python -m unittest discover -s tests -v
```

也可根据 `environment.yml` 重建环境：

```powershell
conda env create -f environment.yml
```

本地真实运行时复制 `.env.example` 为 `.env` 并填写凭证。`.env` 已被 Git 忽略。

```powershell
python -m src.main --dry-run
python -m src.main
python -m src.main --monthly --dry-run --current-month
python -m src.main --monthly
```

## 五、筛选和成本配置

主要配置位于 `config/settings.json`：

- `interests`：个人兴趣方向。
- `minimum_score`：最低入选分，默认 `6.8`。
- `max_candidates`：一次最多送入初筛的候选数量，默认 `80`。
- `rerank_top_n`：统一终审的全局 Top 数量，默认 `20`。
- `max_selected`：每期最多条目，默认 `6`。
- `category_maximums`：各类别最大数量，论文默认最多3条。
- `category_minimums`：在达到最低分时优先保留的类别数量。
- `overlap_hours`：采集重叠窗口，默认48小时；依靠状态去重，不会重复消耗已处理文章。
- `full_text_max_characters`：单篇送入总结模型的最大正文字符数。
- `monthly_review`：月报历史条数、新闻扫描数量、新闻质量阈值和正文长度。

运行报告中的 `ai_usage` 会记录每个模型的请求数、输入 Token、输出 Token和总 Token，便于观察额度消耗。

## 六、状态恢复机制

发送前，程序会在 `data/state.json` 创建 `pending_delivery`，其中包含简报类型、简报 ID、分片和已成功分片编号。周报与月报使用独立成功时间：月报不会推进周报的采集窗口。每张飞书卡片收到成功响应后立即更新本地进度；无论主任务最终成功还是失败，独立 `persist` Job 都会尝试把状态提交回 `main`。

下一次真实运行若发现未完成投递，会跳过采集和 DeepSeek，直接续发剩余分片。

飞书 Webhook 没有跨系统幂等键，因此无法实现严格的分布式 exactly-once。如果飞书已接收消息、但运行器在记录成功之前瞬间终止，仍存在极小的单分片重复概率。每期卡片中的稳定“简报 ID”可用于人工识别重复。

## 七、公开仓库说明

仓库可以保持公开。GitHub Secrets 不会写入代码、状态、Artifact 或日志，但以下非敏感信息会公开：

- 兴趣和信息源配置
- 已处理和已选择的文章标题、URL
- 历史简报、评分理由及 Token 用量

## 八、维护依赖

`requirements.txt` 保存直接依赖，`requirements.lock` 保存完整传递依赖和哈希。升级依赖后重新生成锁文件：

```powershell
python -m pip install pip-tools==7.5.0
python -m piptools compile --generate-hashes --output-file=requirements.lock requirements.txt
```

GitHub Actions 中的官方 Action 固定为完整 commit SHA。升级 Action 时，应先核实目标版本对应的官方仓库 SHA，再更新工作流。
