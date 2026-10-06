# 接入模型：协议、模型列表与连接检查

Creativity Lab 0.3.0 可以直接在网页接入 OpenAI Chat Completions、OpenAI Responses 或 Anthropic Messages 接口。需要服务商提供的地址、API 密钥和可用模型名称。若使用本机兼容模型，密钥可留空。

## 三步开始

1. 双击 `launch.bat`，点击右上角 **设置**。选择服务商声明的 **接口协议**，填根地址与密钥，点击 **获取模型**。生成模型输入框可用列表筛选，也可以手填准确的模型 id。评审模型留空时使用生成模型。
2. 点击 **测试连接**。它用当前草稿发一次短生成请求，检查文本 JSON，可能产生调用费用；不会自动重试或应用设置。测试采用所设最大输出 token，而不是固定的小输出额度。成功后确认模型名称和参数。
3. 点击 **应用设置**。界面切换到真实模型，填写问题与约束，点击 **开始探索**。运行完成后导出需要保留的 JSON 或 Markdown。

**获取模型、测试连接、应用设置是三个独立动作。** 获取模型只向服务商读取模型列表，不生成文本，不修改服务配置；列表查询成功不能证明该模型支持本程序的 JSON 生成。连接测试检查当前生成模型，不会逐个测试评审模型或整个目录，也不是创造力评测。应用设置只保存到当前服务进程。

## 如何选择协议和地址

按服务商 API 文档选择协议；同一个模型可以由不同协议提供，单凭名称无法判断。选择后，设置窗口会显示实际生成请求路径。

| 界面协议 | 程序标识 | 根地址示例 | 生成路径 | 自动鉴权 |
| --- | --- | --- | --- | --- |
| OpenAI · Chat Completions | `openai_chat` | `https://api.openai.com/v1` | `/chat/completions` | `Authorization: Bearer` |
| OpenAI · Responses | `openai_responses` | `https://api.openai.com/v1` | `/responses` | `Authorization: Bearer` |
| Anthropic · Messages | `anthropic` | `https://api.anthropic.com` 或 `https://api.anthropic.com/v1` | `/v1/messages` | `x-api-key` |

这些只是地址格式示例，实际可用的模型和权限以你的服务商为准。两种 OpenAI 协议的请求与响应字段不同；选择 Responses 时，程序会使用 Responses 请求体与输出文本块解析，不会把 Chat 请求直接发过去。[OpenAI Chat Completions 文档](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[Responses 文档](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)与 [Anthropic Messages 文档](https://platform.claude.com/docs/en/api/messages/create)说明各自 API 格式。

网页会移除尾部误粘贴的 `/chat/completions`、`/responses`、`/messages` 或 `/models`，再按协议追加正确路由。Anthropic 裸域名自动补 `/v1`；已有路径前缀会保留。例如自定义网关的 Messages 服务若位于 `https://gateway.example/anthropic/v1/messages`，根地址应填写 `https://gateway.example/anthropic/v1`。不要假设程序会给任意自定义前缀添加版本路径。

远程地址使用 HTTPS；`localhost`、`127.0.0.1` 和 `::1` 可使用 HTTP 与自定义端口。地址不能包含用户名、密码、查询参数或片段。接口若返回跳转，请直接填写服务商公布的最终 API 地址。

## 获取模型和手动填写

**获取模型**使用当前尚未应用的地址、协议、鉴权方式与密钥，允许模型名称暂时留空。OpenAI 协议读取根地址后的 `/models`；Anthropic 读取 `/models?limit=100`，并按 `has_more` / `last_id` 翻页。官方列表契约见 [OpenAI 模型列表](https://developers.openai.com/api/reference/resources/models/methods/list)与 [Anthropic 模型列表](https://platform.claude.com/docs/en/api/models/list)。

程序支持标准 `data` 数组和部分兼容网关的 `models` 对象，去除重复 id，最多返回 500 个模型。Anthropic 最多读取 5 页，每页网络 socket 超时最多 30 秒；配置的超时更短时使用较短值。它不是固定 30 秒的整次查询截止时间。

成功后，生成模型和评审模型共用候选列表。你仍可手动输入目录之外的模型 id；某些服务不提供列表路由，或目录未及时更新。列表为空、404 或查询失败时，原有设置与模型输入不会被自动清空。手填后使用 **测试连接** 验证生成接口。

## 调用与兼容性

多数情况下先保持默认设置。遇到明确参数错误时再根据服务商文档调整。

| 设置 | 默认与范围 | 何时调整 |
| --- | --- | --- |
| JSON 输出 | OpenAI 默认 API JSON 模式 `json_object`，也可选 `prompt`；本版本 Anthropic 使用 `prompt` | 网关不接受 JSON 参数时，改为提示模型输出 JSON |
| 鉴权方式 | `auto`；可选 `bearer`、`x_api_key` | 网关文档规定不同认证头时显式选择 |
| 请求超时 | 120 秒；网页接受 5–300 的整数 | 慢响应模型可提高；这个值约束网络 socket 操作，不限制整次探索的总时长 |
| 最大输出 | 4096；256–32768 的整数 | 输出截断时提高，同时遵守模型自身上限 |
| Temperature | 留空；填写时为 0–2 | 留空不发送该参数，使用模型默认；模型拒绝温度参数时清空 |
| 输出长度参数 | `auto` | 只对 Chat 手动选择 `max_completion_tokens` 或较旧接口的 `max_tokens`；Responses 与 Messages 使用各自固定参数 |

`auto` 输出参数在 Chat 中使用 `max_completion_tokens`，Responses 使用 `max_output_tokens`，Messages 使用 `max_tokens`。程序不会因参数被拒绝而自动改参数再发一次请求。Chat 的旧 `max_tokens` 并不适用于所有推理模型；部分模型的推理 token 也占用输出预算。[OpenAI 参数说明](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)与 [Anthropic 输出限制说明](https://platform.claude.com/docs/en/api/messages/create)给出具体定义。

API JSON 模式要求接口输出 JSON，并不保证对象满足 Creativity Lab 的所有业务字段。提示词模式依赖模型遵循 JSON 指令；两种模式都会继续解析与校验。Anthropic API 的其他结构化输出功能未在本版本适配，不能据此推断服务商没有该能力。

评审模型使用相同地址、密钥、协议及调用设置。填写另一个模型只改变评审模型名称，不会建立另一个服务连接。

## 常见问题

| 提示 | 检查与处理 |
| --- | --- |
| HTTP 404/405：接口或模型不存在 | 先看请求路径预览，核对协议、根地址、自定义前缀与模型 id。只在获取列表时失败，可以手填后测试生成 |
| HTTP 400：参数组合被拒绝 | 核对协议；兼容接口可试提示词 JSON；Temperature 留空；Chat 旧接口按文档选择 `max_tokens`。确认输出上限未超过模型限制 |
| HTTP 401：密钥无效 | 检查密钥是否过期、复制是否完整，鉴权头是否符合服务商文档 |
| HTTP 403：拒绝访问 | 检查账号和所选模型权限，或服务商访问限制 |
| HTTP 429：限流或额度不足 | 检查额度与并发，稍后手动测试；程序不会暗中继续付费调用 |
| HTTP 5xx：服务不可用 | 等服务恢复后手动测试 |
| 超时 | 确认模型服务和网络可用；慢模型可提高超时。一次探索包含多次生成与评审，可能持续数分钟 |
| 已收到响应，但输出被截断 | 提高最大输出 token；推理模型可能在返回 JSON 前用完预算。截断是已连接后的输出问题，不应反复更换密钥 |
| 没有收到 JSON 对象 | 核对模型的文本 JSON 能力和 JSON 输出方式；程序不会把普通文字或错误页面当作成功 |
| 响应格式与协议不一致 | 检查协议是否选错。Responses 的 `output` 文本块、Messages 的 `content` 文本块和 Chat 的 `choices` 不能混用 |
| HTTPS 证书失败 | 检查服务证书、系统时间和网络环境；程序保持证书验证 |
| 域名解析、连接失败 | 核对域名、本机服务端口、服务是否运行及网络；和 JSON 参数错误分别处理 |

程序还会识别模型拒绝、未完成文本和输出上限终止，避免只看 HTTP 200 就报告成功。每次连接测试最多一个生成调用，成功或失败都返回调用预算；运行中的失败调用同样计入上限。错误信息不展示上游响应体或密钥。

网页查询任务状态时，每次读取最多尝试 3 次。持续中断后，当前页面会保留任务编号并显示 **恢复查询**；点击后继续读取原任务，不重新发送生成请求。服务可能仍在工作，先恢复查询再决定后续操作。完整刷新页面不会恢复内存中的页面任务编号；停止本地服务也会丢失未导出的运行。

## 设置能保留多久

应用后的设置与密钥只保存在当前本地服务进程，不写入磁盘或浏览器存储；配置查询只返回是否已有密钥，不返回其值。运行结果和导出文件也不包含密钥。关闭设置弹窗会清空尚未应用的密钥输入，其他草稿保留；更换接口地址需要重新填写该地址的密钥。

关闭浏览器窗口通常不会停止终端里的本地服务。再次双击启动器或执行 `serve --open`，同安装路径、同版本的现有服务会被直接打开，沿用它的设置和任务。升级版本会留下旧服务，为新版找空闲端口；新版不会从旧服务继承密钥。关闭终端或按 Ctrl+C 停止服务后，网页配置随进程结束，下次需要重填，或使用环境变量配置。**恢复启动配置**会清除网页覆盖并重新采用当前服务的环境配置。

CLI 和网页服务是不同进程，网页设置不会自动应用到另一个终端里的命令。环境变量可用于 CLI 或启动网页服务，例如 PowerShell：

```powershell
$env:CREATIVITY_BASE_URL = 'https://api.openai.com/v1'
$env:CREATIVITY_MODEL = '服务商提供的模型 id'
$env:CREATIVITY_API_KEY = '你的密钥'
$env:CREATIVITY_PROTOCOL = 'openai_chat'
$env:CREATIVITY_JSON_MODE = 'json_object'
$env:CREATIVITY_AUTH_TYPE = 'auto'
$env:CREATIVITY_TOKEN_PARAM = 'auto'
$env:CREATIVITY_TIMEOUT = '120'
$env:CREATIVITY_MAX_OUTPUT_TOKENS = '4096'
# 可选：CREATIVITY_JUDGE_MODEL 为评审模型 id
# 可选：CREATIVITY_TEMPERATURE；不设置时不发送温度
python -m creativity_lab serve --open
```

使用 Anthropic 时，将协议改为 `anthropic`，JSON 方式改为 `prompt`，并填服务商的 Messages 根地址。使用 Responses 时，将协议改为 `openai_responses`。用量以服务商实际返回为准；未返回用量会标记为不完整。

## 设计来源与验证范围

模型设置参考官方 [DeepSeek Harness Models 页面](https://github.com/deepseek-ai/deepseek-harness/blob/5badb15009ae1756c3afe0ae0cef1faafc290ccc/packages/client/ui-settings-models/README.zh.md)在固定 commit `5badb15009ae1756c3afe0ae0cef1faafc290ccc` 的协议选择、草稿探测和模型发现设计。Creativity Lab 的 Python 适配器与网页独立实现，没有复制该项目源码。接口字段依据上述 OpenAI 和 Anthropic 官方文档。

源码和本地 HTTP 模拟服务验证不能代替你配置的外部服务实测。当前验证记录见 [validation.md](validation.md)；本版本没有新增真实模型创造力提升幅度或超越人类的实验结论。
