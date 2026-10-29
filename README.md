# Codex Pulse

**把 Codex 的速度、token 消耗和账户用量放到一个可实时查询的私有监控台。**

Codex Pulse 从本机 Codex 日志中提取性能指标，用 Python 增量采集，再由浏览器展示。你可以只在本机使用，也可以部署到自己的 Cloudflare Workers，让手机和其他电脑查询同一份历史。

[快速开始](#快速开始) · [部署到云端](docs/deploy.md) · [指标口径](docs/metrics.md) · [隐私与安全](SECURITY.md)

## 能看什么

- 模型、推理强度、请求时间和完成状态。
- 输入、输出、缓存输入、缓存写入和推理 token。
- 每轮对话的首字延迟、总耗时，以及模型请求的首项延迟和综合输出 TPS。
- 已收录历史的累计消耗、所选时间范围消耗、账户剩余额度和重置时间。
- 按官方模型价格换算的 API 等价费用，以及 Standard credits 参考值。
- 延迟和速率趋势、模型筛选、状态筛选、记录明细和当前页 CSV 导出。

页面默认每 5 秒刷新。采集端默认每 2 秒增量检查日志，断网后重试并补传。云端保存历史，采集电脑休眠时仍可查询已上传的数据。

## 快速开始

需要 Node.js 22.13 或更高版本、Python 3.10 或更高版本，以及本机 Codex 产生的日志。核心运行不需要安装 npm 包或 Python 包。

1. 克隆仓库并启动本机页面。

   ```sh
   git clone https://github.com/2429240945cxy-cmyk/codex-pulse.git
   cd codex-pulse
   npm run dev
   ```

2. 在另一个终端启动采集端。

   ```sh
   cd codex-pulse
   cp collector/config.example.json config.local.json
   python3 collector/collector.py --config config.local.json
   ```

3. 打开 <http://127.0.0.1:8768>。首次索引完成后，记录会自动出现。

本机预览只监听 `127.0.0.1`，历史保存在已忽略的 `preview.sqlite` 中，重启后继续保留。需要跨设备查询时，使用[云端部署](docs/deploy.md)。

`config.local.json` 和采集端生成的数据库已经加入 `.gitignore`。不要把真实 token 写进示例配置。

## 在 macOS 登录后自动采集

先在 `config.local.json` 中配置自己的服务地址和上传 token，再运行：

```sh
python3 scripts/install-agent.py --config config.local.json
```

安装器把采集程序和权限为 `0600` 的配置复制到 `~/Library/Application Support/CodexPulseOpenSource/`。它使用当前 Python 解释器，不依赖仓库路径。

停止服务并移除登录启动项：

```sh
python3 scripts/install-agent.py --uninstall
```

卸载保留本地配置和历史。其他系统可以直接运行采集端；本仓库暂未提供 Linux 和 Windows 服务安装器。

## 数据如何流动

```text
本机 Codex 日志
    ↓ 增量读取、提取统计字段
Python 采集端 → HTTPS 上传 → Worker API → D1 历史库
                                            ↑
                                  浏览器每 5 秒查询
```

采集端默认读取 `~/.codex/sessions/`、`~/.codex/state_5.sqlite` 和 `~/.codex/logs_2.sqlite`。上传内容限定为模型、ID、token、时间和账户额度等统计，不包含提示词、回复正文或工具参数。

## 指标边界

首字延迟来自 Codex 对话轮次的原生字段。请求级日志没有精确首字记录时，页面显示“首项”，不会把两者混用。日志缺失的指标显示为未记录。

费用按**当前官方价目表**重算，是 API 等价估算，订阅实际扣款以账户账单为准。价格不是历史账单快照。未支持的模型显示为未计价；工具独立费用、地区加价和未记录的缓存保留时长不在估算范围内。

累计消耗只覆盖采集端已收录的本机日志。账户剩余额度由 Codex 返回，可能包含其他设备的消耗，不能从本机 token 总量直接推导。

更多定义见[指标口径](docs/metrics.md)。

## 开发和验证

```sh
npm run build
npm test
npm run validate
```

界面源文件在 `worker/ui.html`，服务端在 `worker/api.js`。构建脚本把它们合并到 `worker/index.js`，然后输出到 `dist/server/index.js`。不要直接编辑生成文件。

测试覆盖缓存计价、长上下文、token 事件去重、首字归属和数据字段过滤。GitHub Actions 在推送和 Pull Request 时执行构建、测试及产物验证。

## 兼容性

这个项目读取 Codex 的本地日志格式，不使用稳定的公开监控 API。Codex 升级可能改变日志文件、字段或事件顺序。项目会保留缺失值，采集错误会记录到服务日志；更新后请检查是否仍有新记录进入监控台。

价格解析器每日读取 [OpenAI API 价目表](https://developers.openai.com/api/docs/pricing) 和 [Codex 计价说明](https://learn.chatgpt.com/docs/pricing)。解析或网络失败时保留最近一次有效价格。仓库中的 credits 备用表标有验证日期，不能代替最新价目表。

## 许可证

[MIT](LICENSE)。本项目是独立的社区工具，与 OpenAI 无隶属关系。
