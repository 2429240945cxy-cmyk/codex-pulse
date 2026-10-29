# 部署到 Cloudflare Workers

本指南把监控页面和历史数据部署到自己的 Cloudflare 账户。需要 Workers 和 D1 的可用权限，以及已登录的 Wrangler CLI。部署和存储的费用取决于 Cloudflare 套餐。

## 创建数据库

在仓库根目录运行：

```sh
npx wrangler login
npx wrangler d1 create codex-pulse
cp wrangler.example.jsonc wrangler.local.jsonc
```

把创建结果中的数据库 ID 填入 `wrangler.local.jsonc` 的 `database_id`。

初始化表结构：

```sh
npx wrangler d1 execute codex-pulse --remote --file drizzle/0000_metrics.sql --config wrangler.local.jsonc
```

后续版本升级需要按其迁移说明处理数据库，不要重建已有数据库。

## 设置访问凭证

生成两个不同的随机值，一个用于采集端上传，一个用于浏览器查看。把它们保存在密码管理器中。

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

用 Wrangler 的交互输入设置密钥，不要把真实值放进命令行参数或 Git 文件：

```sh
npx wrangler secret put INGEST_TOKEN --config wrangler.local.jsonc
npx wrangler secret put VIEWER_TOKEN --config wrangler.local.jsonc
```

`INGEST_TOKEN` 用于 `/api/ingest`。`VIEWER_TOKEN` 用作浏览器 Basic Auth 密码，用户名可以填 `viewer`。远程部署未设置查看密码时，页面拒绝提供数据。

## 发布页面

```sh
npm run build
npx wrangler deploy --config wrangler.local.jsonc
```

打开返回的 HTTPS 地址，输入查看密码。Basic Auth 凭证应只通过 HTTPS 发送。

## 连接采集端

复制 `collector/config.example.json` 为已忽略的 `config.local.json`，填入自己的值：

```json
{
  "url": "https://YOUR-WORKER.workers.dev",
  "ingest_token": "YOUR_INGEST_TOKEN"
}
```

启动采集：

```sh
python3 collector/collector.py --config config.local.json
```

网页显示最近同步时间后，即可在其他设备打开同一网址。采集电脑需要联网，新记录才会上云。

## 使用 ChatGPT Sites

源码也保留了 `.openai/hosting.json` 供 Sites 工作流使用。该文件不包含个人站点 ID。创建自己的 Site 后，由 Sites 配置 `project_id` 和 `DB` 绑定，并应用 `drizzle` 中的迁移。

仅当站点已由 Sites 的私有访问边界保护时，设置 Worker 环境变量 `ACCESS_MODE=sites`。设置 `INGEST_TOKEN` 后，通过 Sites 正式发布。

采集端配置还需要该站点受支持的 service access token：

```json
{
  "url": "https://YOUR-PRIVATE-SITE.chatgpt.site",
  "ingest_token": "YOUR_INGEST_TOKEN",
  "bypass_token": "YOUR_SITE_SERVICE_ACCESS_TOKEN"
}
```

`bypass_token` 只用于该 Site 的 `OAI-Sites-Authorization` 请求头。不要将它写入网页、源码或仓库。Sites 的浏览器登录与 service access 是两个不同的访问方式。
