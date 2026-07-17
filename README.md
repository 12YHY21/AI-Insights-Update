# AI Insights Update

每周一、周五自动采集 AI 前沿论文与官方技术文章，通过 DeepSeek 完成筛选和中文技术总结，再推送到飞书群自定义机器人。

## 工作流程

1. 从 arXiv、OpenAI、Google DeepMind、Hugging Face、NVIDIA 等 RSS 获取最新条目。
2. 使用“上次成功时间减 12 小时”的重叠窗口采集，再用 `data/state.json` 去重，减少来源延迟导致的漏报。
3. 使用 `deepseek-v4-flash` 批量评分，按前沿性、创新、实用性、可信度和兴趣相关性排序。
4. 从不同来源中选择最多 6 条，再用 `deepseek-v4-pro` 生成基于原文的中文技术总结。
5. 通过飞书互动卡片推送；过长内容自动拆成多张卡片。
6. GitHub Actions 将本次处理状态提交回仓库，防止重复推送和重复消耗额度。

定时任务为北京时间每周一、周五 `08:17`，也可以在 Actions 页面手动运行。

## 目录结构

```text
config/settings.json          兴趣、来源、阈值和数量配置
data/state.json               去重与上次成功运行状态
src/collectors.py             RSS 采集和正文提取
src/ai.py                     DeepSeek 评分与总结
src/feishu.py                 飞书签名和卡片推送
src/main.py                   任务编排入口
.github/workflows/digest.yml  定时任务
```

## 一、创建飞书群机器人

1. 在飞书中新建一个群，可以只有你自己。
2. 打开群设置 → 群机器人 → 添加机器人 → 自定义机器人。
3. 设置名称并复制 Webhook 地址。
4. 推荐在安全设置中开启“签名校验”，并复制签名密钥。

官方说明：[飞书自定义机器人使用指南](https://open.feishu.cn/document/ukTMukTMukTM/ucTM5YjL3ETO24yNxkjN)

Webhook 和签名密钥等同于发送凭证，不要放进 `config/settings.json`，也不要提交到仓库。

## 二、配置 GitHub Secrets

进入仓库：

`Settings → Secrets and variables → Actions → New repository secret`

添加以下 Secrets：

| Secret | 必需 | 内容 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 是 | DeepSeek API Key |
| `FEISHU_WEBHOOK_URL` | 是 | 飞书自定义机器人 Webhook |
| `FEISHU_WEBHOOK_SECRET` | 否 | 开启签名校验后填写的密钥 |

程序默认使用当前 DeepSeek API 的 `deepseek-v4-flash` 和 `deepseek-v4-pro`。如果模型名以后变化，可增加 `DEEPSEEK_RANK_MODEL`、`DEEPSEEK_SUMMARY_MODEL` Secret，并同步在工作流 `env` 中引用；当前代码无需修改。

## 三、首次验证

打开仓库的 `Actions → AI Insights Update → Run workflow`。

建议依次运行：

1. `collect-only`：只检查 RSS，无需调用 DeepSeek 或飞书。
2. `dry-run`：调用 DeepSeek 生成简报，但不推送、不修改去重状态；结果可在运行页面的 Artifacts 下载。
3. `send`：真实推送到飞书，并更新 `data/state.json`。

第一次 `send` 默认回看最近 7 天。若不希望第一期内容太多，可先将 `config/settings.json` 中的 `lookback_days_on_first_run` 调为 `2`。

## 四、本地使用 Conda Py312

本项目本地环境固定使用 `Py312`。当前机器的环境为 Python 3.12.3：

```powershell
conda activate Py312
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m src.main --collect-only
```

如果 PowerShell 尚未初始化 Conda，可直接运行：

```powershell
& 'D:\Miniconda\Scripts\conda.exe' run -n Py312 python -m pip install -r requirements.txt
& 'D:\Miniconda\Scripts\conda.exe' run -n Py312 python -m unittest discover -s tests -v
```

仓库同时提供 `environment.yml`，在其他机器可执行：

```powershell
conda env create -f environment.yml
```

GitHub 托管运行器无法访问本机 Conda 环境，因此工作流使用 `actions/setup-python` 安装同一主版本 Python 3.12。

### 本地真实生成或推送

复制示例环境文件：

```powershell
Copy-Item .env.example .env
```

将 `.env` 中的占位值替换为真实凭证。该文件已被 `.gitignore` 排除。

```powershell
# 生成预览，不推送、不更新状态
python -m src.main --dry-run

# 真实推送并更新 data/state.json
python -m src.main
```

## 五、定制兴趣和来源

编辑 `config/settings.json`：

- `interests`：影响 DeepSeek 相关性评分。
- `feeds`：RSS/Atom 信息源。
- `minimum_score`：最低入选分，默认 `6.8`。
- `max_selected`：每期最多条目，默认 `6`。
- `max_selected_per_source`：单一来源上限，默认 `2`。
- `send_empty_digest`：无合格内容时是否仍发送运行正常通知。
- `fetch_full_text`：是否尝试抓正文；失败会自动回退到 RSS 摘要。

若某个来源不可访问，任务会记录警告并继续处理其他来源。只有所有后续处理失败时，任务才会失败；已配置飞书时还会尽力发送一条不包含敏感信息的失败告警。

## 状态与安全

- 只有推送成功后才会更新去重状态；DeepSeek 或飞书失败不会把内容误标为已处理。
- `data/state.json` 会保留 180 天历史，其中包含文章标题和 URL，不包含任何 Key。
- `.env`、生成的 `output/` 和临时文件不会进入 Git。
- GitHub Actions 只获得 `contents: write` 权限，用于提交去重状态。
- DeepSeek 的总结提示词要求“只根据提供资料总结，缺失时写原文未说明”，但重要结论仍应通过卡片中的原文链接核验。
