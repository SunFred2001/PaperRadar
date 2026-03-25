# ECG Paper Radar

一个可本地运行、也可直接部署到 GitHub Pages 的论文追踪网站，用来每天更新“深度学习结合心电信号处理”方向的最新期刊论文，并支持微信推送新增论文。

## 主要能力

- 基于 PubMed 检索期刊论文
- 默认聚焦 `deep learning + ECG + signal processing`
- 支持本地网页查看论文标题、作者、摘要、期刊、PubMed/DOI 链接
- 支持每日自动刷新缓存
- 支持每日微信推送，只发送尚未通知过的新论文
- 支持两种微信渠道
  - `Server酱`：推送到个人微信
  - `企业微信群机器人`：推送到群聊
- 支持 GitHub Pages 在线部署
- 支持 GitHub Actions 定时自动更新站点数据

## 本地运行

启动本地服务：

```bash
python app.py
```

打开：

```text
http://127.0.0.1:8000
```

只刷新一次数据，不启动网页服务：

```bash
python app.py --refresh-now
```

测试一次微信推送：

```bash
python app.py --push-test
```

## 在线部署

本仓库已经包含 GitHub Pages 自动部署配置：

- 工作流文件：`.github/workflows/deploy-pages.yml`
- 静态构建脚本：`scripts/build_static_site.py`

部署方式：

1. 把代码推送到 GitHub 仓库主分支 `main`
2. 在 GitHub 仓库 `Settings > Pages` 中把来源切换为 `GitHub Actions`
3. 等待工作流 `Deploy Paper Radar` 首次跑完
4. 站点地址通常会是：

```text
https://sunfred2001.github.io/PaperRadar/
```

## GitHub Actions Secrets

如果你希望线上站点每天自动发微信推送，请在仓库 `Settings > Secrets and variables > Actions` 中添加：

- `NCBI_CONTACT_EMAIL`
- `SERVERCHAN_SENDKEY`
- `WECOM_WEBHOOK_URL`

说明：

- `SERVERCHAN_SENDKEY` 和 `WECOM_WEBHOOK_URL` 二选一即可
- 定时任务每小时运行一次，但只有当到达你配置的每日推送时间时，才会真正发送当天的新论文

## 配置文件

配置文件在 `data/config.json`。

常用字段：

- `search_query`：PubMed 查询式
- `max_results`：每次最多抓取多少篇
- `lookback_days`：向前回看多少天
- `refresh_interval_hours`：自动刷新间隔
- `push_enabled`：是否开启每日微信推送
- `push_channel`：`serverchan` 或 `wecom_bot`
- `push_time`：每日推送时间，例如 `09:00`
- `push_timezone`：时区，例如 `Asia/Shanghai`
- `push_max_papers`：每次推送最多包含几篇论文

## 静态站点说明

部署到 GitHub Pages 后，网站会自动切换为“在线只读模式”：

- 公开网页可以正常浏览每日更新的论文流
- 页面中的筛选功能仍然可用
- 配置修改、手动刷新、测试推送只在本地服务模式下可用

## 备注

- `data/papers_cache.json` 和 `data/dashboard.json` 会由 GitHub Actions 自动更新并回写到仓库
- 如果你修改了检索条件，系统会重置“已推送论文”记录，并按新的检索范围重新开始统计
