# Creativity Lab · 创造力实验室

**把一次回答，变成有分支、有批评、有证据的想法搜索。** 面向科研构思、产品创新与写作设计，支持任意提供 JSON Chat Completions 的模型接口。Python 3.10+，运行时零第三方依赖，MIT 开源。

这一版实现提升创造力的搜索机制和评测工具。**尚未测得真实模型的提升幅度，也没有证据证明本项目超越人类。** 网页默认使用明确标记的预置演示；真实模式需要你自己的模型接口。

## 先用起来

下载并解压仓库，Windows 双击 **launch.bat** 打开本地网页。0.2.0 可以直接在网页接入模型：

1. 点击右上角 **设置**，填入 OpenAI 兼容接口地址、模型名称与 API 密钥。接口地址通常以 `/v1` 结尾，例如 `https://api.openai.com/v1`，无需附加 `/chat/completions`。
2. 点击 **测试连接**，确认接口能返回所需 JSON。测试会发送一个短模型请求，可能产生调用费用；测试本身不是创造力评测。
3. 点击 **应用设置**，再切换到 **真实模型**。需要时可在设置中填写评审模型，并选择 `max_completion_tokens` 或 `max_tokens` 参数。
4. 输入希望解决的问题和约束，点击 **开始探索**。查看方案、机制差异、关键假设、失败风险、最小验证方法与候选档案；可导出 JSON 和 Markdown。

也可以直接使用预置演示了解流程。macOS/Linux 可从终端运行 `python3 -m creativity_lab serve --open`。

电脑需要已有 Python 3.10 或更高版本；启动器会检测，缺少时会说明原因，不会自动安装。演示无需账号或网络。真实模型模式需要联网，任务和提供的参考资料会发送给你配置的模型服务。

试着输入：

> 设计一个让年轻人愿意持续使用的社区图书交换服务。预算每月 500 元，不依赖积分排行榜；提出机制不同的方案，每个方案附可证伪的试验。

科研和写作示例见 [examples/briefs.md](examples/briefs.md)。[完整中文研究报告](docs/research.zh-CN.md)解释为什么普通 AI 回答容易缺乏新意、论文实际证明了什么，以及我们采用哪些机制。

## 如何提升探索能力

| 环节 | 当前实现 | 用意 |
| --- | --- | --- |
| 发散 | 假设反转、远域类比、机制重组、矛盾分离、反事实约束、反馈变异 | 改变解题机制，而不只是换角色或措辞 |
| 去重 | 英文词与中文双字的 Jaccard 相似度，近重复拒绝 | 避免候选数量看似很多、实质相同 |
| 批评 | 新颖性、实用性、可行性、惊喜度、可测试性分别评分 | 不把离谱与流畅误认成有效创新 |
| 保留 | “机制 × 测试方式”24 格档案，同格保留质量更好者 | 保护不同方向的垫脚石 |
| 迭代 | 从档案取父代，提供具体失败风险与下一步实验 | 后续生成能参考上一轮批评 |
| 证据 | 按精确想法哈希导入外部测量，与文本筛选分列 | 提议、模型评价、外部结果各有明确状态 |
| 对照 | 同模型、同候选配置、同调用上限的直接采样基线 | 检验结构是否比单纯多生成更有价值 |
| 盲评 | 隐藏系统来源和内部得分的 A/B 包与统计工具 | 给人类评审留下独立判断空间 |

词面相似度只是透明的离线代理，不能证明语义新颖或全球首创。档案类别由模型标注，也需要人类核查。不同模型评审是可选配置；使用同一模型时会明确记录，不能称为独立证据。所有候选默认尚未做真实实验。

## 研究来源

设计综合了 [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve)、[ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve)、[FunSearch](https://github.com/google-deepmind/funsearch)、[OpenELM](https://github.com/CarperAI/OpenELM) 等 8 个仓库的方法。仓库和许可证逐项记录在 [项目调研](docs/projects.md)，未复制第三方代码，也未将其软件作为依赖。

核心论文包括 [FunSearch，Nature](https://www.nature.com/articles/s41586-023-06924-6)、[Co-Scientist，Nature 2026](https://www.nature.com/articles/s41586-026-10644-y)、[AI Scientist，Nature 2026](https://www.nature.com/articles/s41586-026-10265-5)、[AI 写作质量与集合多样性，Science Advances](https://doi.org/10.1126/sciadv.adn5290)、[研究创意的人类盲评，ICLR 2025](https://arxiv.org/abs/2409.04109)。12 篇文献的年份、实际结论与适用范围见 [研究报告](docs/research.zh-CN.md)。这些论文支持设计方向，并不直接验证本项目的组合效果。

## 接入真实模型

网页 **模型设置** 是日常接入入口。如果由你的 AI 助手操作，把 [Agent 使用指南](docs/agent-guide.md)交给它，再用自然语言说出目标即可。

设置默认只保存在当前本地服务进程的内存中，不写入磁盘。API 密钥不会通过 GET 配置接口回传，也不会进入运行结果或导出文件。关闭并重启服务后，网页临时设置失效：服务会恢复启动时的环境变量配置，没有环境配置时需要重新填写。网页设置仅影响当前网页服务，不会自动配置另一个终端里的 CLI 进程。

CLI 与已有自动化仍支持环境变量。开发者可在 PowerShell 启动进程前设置：

```powershell
$env:CREATIVITY_API_KEY = '你的密钥'
$env:CREATIVITY_MODEL = '服务商提供的模型名称'
# 默认接口 https://api.openai.com/v1；其他兼容服务按实际地址设置：
# $env:CREATIVITY_BASE_URL = 'https://服务商/v1'
# 可选：使用不同模型进行筛选
# $env:CREATIVITY_JUDGE_MODEL = '评审模型名称'
# 可选：部分兼容服务使用此参数
# $env:CREATIVITY_TOKEN_PARAM = 'max_tokens'
python -m creativity_lab serve --open
```

兼容服务需支持 `/chat/completions`、`response_format: json_object` 和文本 JSON 输出。评审模型使用同一接口地址与密钥；留空时使用生成模型。部分服务需要选择 `max_tokens`，默认是 `max_completion_tokens`。OpenAI 接口依据 [官方 Chat Completions 文档](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。支持无密钥的 loopback 本地兼容模型；远程接口要求 HTTPS。没有隐藏重试，失败请求也计入调用上限。中途失败会保留已生成候选与用量，明确标为未完成。

## 复现实验

从仓库根目录运行以下命令，无需先安装依赖：

```console
python -m unittest discover -s tests -v
python -m creativity_lab run "设计预算内的社区共享服务" --demo --output runs/demo.json
python -m creativity_lab benchmark --demo --output runs/benchmark.json
python -m creativity_lab blind runs/benchmark.json --output runs/reviewer-pack.json --mapping runs/private-mapping.json
```

`--demo` 是固定样例的流程测试。去掉它才会调用真实模型。默认基准包含科研、产品、写作共 12 个任务。保留所有运行、失败和真实 token 用量；相同调用上限并不等于相同 token 成本。具体评测规程、人类比较、缺失票与置信区间见 [评测说明](docs/evaluation.md)。

外部实验证据接口：

```console
python -m creativity_lab evidence-template runs/demo.json --output runs/evidence-template.json
# 用可信实验结果填入模板，再导入：
python -m creativity_lab attach-evidence runs/demo.json runs/evidence-template.json --output runs/with-evidence.json
```

导入结果标记为 `reported_pass/reported_fail`，表示外部报告的结果；软件不自动核验报告真伪，不自动执行模型生成代码。模板里的空值必须由实际测量填写。

## 后续研究

下一步应优先完成真实模型多任务盲评，再考虑语义嵌入查新、可信自动实验适配器、多模型集成和按档案改善自适应分配预算。它们尚未实现。声称“在指定任务上超越人类”之前，需要预先确定人类组、资源预算、评价指标、样本规模和独立验证方式。

当前版本 **0.2.0**。版本与边界见 [CHANGELOG](CHANGELOG.md)，当前与历史验证状态见 [validation.md](docs/validation.md)，系统细节见 [architecture.md](docs/architecture.md)。
