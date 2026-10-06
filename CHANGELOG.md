# Changelog

## 0.3.0 — 2026-10-06

- 模型设置增加 OpenAI Chat Completions、OpenAI Responses 与 Anthropic Messages 协议选择，按协议构造请求、读取文本与用量、识别截断及未完成响应。
- 增加「获取模型」：用当前草稿查询列表，支持筛选和手填，生成模型与评审模型共用候选列表；查询不保存设置，也不发送生成请求。
- 增加 JSON 输出方式、自动/Bearer/x-api-key 鉴权、请求超时、最大输出 token、可选 Temperature 与 Chat 输出参数设置。Temperature 默认留空；兼容接口可使用提示词 JSON。
- 规范化误粘贴的完整请求路由，Anthropic 裸域名自动补 `/v1`，保留自定义路径前缀；更换地址时仍要求重新提供该地址的密钥。
- 连接测试按当前配置只发送一次短生成请求，使用所设输出上限，分别提示鉴权、地址、参数、超时、证书、JSON 格式和输出截断问题；测试不自动重试或应用设置。
- 网页状态读取最多尝试 3 次，持续中断时保留原任务编号供恢复查询，不重新提交模型任务。
- 再次启动时复用同安装路径、同版本的现有服务；升级保留旧服务和结果，新版使用空闲端口。网页配置与密钥仍只在当前服务进程内有效。
- 模型设置设计参考官方 DeepSeek Harness，独立实现，未复制源码。新增中文接入与排错指南；验证状态见 `docs/validation.md`。
- 尚未进行外部模型服务实测，也没有新增创造力提升或超越人类的实验结论。

## 0.2.0 — 2026-10-06

- 网页增加“模型设置”：配置 OpenAI 兼容接口地址、生成模型、API 密钥、可选评审模型与 token 参数。
- “测试连接”发送短 JSON 请求，检查接口兼容性；测试可能产生模型调用费用。
- “应用设置”仅更新当前服务进程的内存配置，默认不写磁盘；密钥不经 GET 配置接口回传，也不进入运行导出。
- 服务重启后恢复原环境变量配置或需要重新填写；CLI 环境变量方式继续支持。
- 修改接口地址时清空未应用的密钥，避免将旧密钥发给新服务；旧窗口占用端口时，新版自动选择空闲端口并保留旧窗口的运行结果。
- 补充中文网页接入步骤与配置生命周期说明。0.2.0 的当前验证记录见 `docs/validation.md`；0.1.0 统计按历史记录保留。
- 尚未测得真实模型创造力提升幅度，也没有本项目超越人类的实验结论。

## 0.1.0 — 2026-10-06

- Six idea operators with lineage, near-duplicate filtering, a discrete quality/diversity archive and critique-fed next rounds.
- JSON-capable Chat Completions adapter, loopback-only studio and deterministic fixture demo.
- Matched call-cap baseline, reviewer-blind packs, vote statistics and external evidence import.
- Research synthesis of 12 papers and 8 repositories; no third-party source code incorporated.
- This release has no measured creativity uplift or human-superiority result.
