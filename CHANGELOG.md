# Changelog

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
