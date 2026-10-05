# 如何检验创造力是否改善

项目把有用、可行、可测试的多样方案作为搜索目标。模型评分用于筛选候选，不能充当真实实验结果或人类创造力证据。任务集包含 12 个中文科学、产品与叙事问题；它是公开的探索性任务集，尚未经过心理测量验证，也不是全球原创性检索。

## 同条件基线

`run_benchmark(provider_factory, judge_factory, tasks, rounds=2, candidates=4, max_calls=30, seed=42)` 返回包含原始运行与用量的 JSON。两个 factory 都是零参数函数，每个任务、每个模式重新创建 provider；`judge_factory=None` 时使用生成 provider 评审。tasks 可为任务列表，或 `creativity_lab/data/tasks.json` 的整体对象。任务文件作为包数据一起安装。

lab 使用多个探索操作和档案反馈；baseline 独立直接生成，不使用档案反馈。两者使用相同任务、参考材料、模型设置、轮数、每轮候选数、筛选与去重规则，以及每个任务/模式的模型调用上限。上限包括生成和评审，理论调度上界为 `rounds * (candidates + 1)`，实际调用数另行记录。重复候选和预算耗尽可能减少实际评审次数。调用上限是最大额度，不是自动消耗额度。

每种模式记录实际生成数、筛选数、档案覆盖、去重数量、实际调用数和供应商报告的输入/输出 token。没有 token 用量时明确记录缺失。模型请求失败时，保留引擎已记录的部分结果与已用预算，继续独立的后续任务；`summary.incomplete_runs` 与 `protocol.issues` 标记未完成运行。没有可验证部分结果的初始化错误仍会抛出，不能捏造一个完成结果。`provider_error` 运行不能制作盲评候选；预算耗尽的候选会在结果中标记未完成搜索。**相同调用上限不等于相同 token、时间或费用。** 不应把更多 token 或更长提示产生的偏好写成资源无关的创造力提升。模型评分、词汇距离和档案覆盖只作为探索性指标；不用它们宣布超越人类。

## 盲评包

```python
from creativity_lab.evaluation import make_pack, summarize_votes

reviewer_pack, private_mapping = make_pack(benchmark["pairs"], seed=42)
# 将 reviewer_pack 交给评审；private_mapping 留在组织者处。
summary = summarize_votes(reviewer_pack, private_mapping, votes)
```

`make_pack(pairs, seed=42, selection_count=1)` 返回 `(reviewer_pack, private_mapping)`。pair 结构是 `{"task_id": "...", "lab": run, "baseline": run}`。`build_blind_pack(lab_run, baseline_run, seed=42)` 是单任务包装。每个模型运行按照评审质量选择相同数量的候选；质量只包含有用性、可行性和可测试性，**不以新颖性标量排名**。质量相同时按提交顺序选择。默认每个任务/运行对各选一项，不把档案大小当作人类独立样本数量。

生成包前应冻结选择规则、模型设置、任务、种子、评审标准与分析计划；在看见偏好结果后换候选会引入选择偏差。评审端只有任务和 A/B 方案的标题、机制、假设、风险与测试。来源、模型身份、分数、预算、运行 ID、操作名称和谱系保存在私有映射；A/B 与项目顺序由固定种子随机化。代码能移除结构化来源元数据，但无法保证文本里没有暴露来源的措辞，正式评估前须人工检查而不改变方案机制。

每个项目录入一个真实的、汇总裁决后的选择；程序不自动生成、补全或伪造人类投票：

```json
[
  {"item_id": "item-0001", "choice": "A", "reason": "可选：说明判断依据"},
  {"item_id": "item-0002", "choice": "tie"}
]
```

允许 `A`、`B`、`tie`；重复、未知项目、非法选择会被拒绝。未填写项目保持缺失。若多个评审员评审同一个项目，应在预注册方案中定义共识裁决或使用额外的分层统计分析，不能把重复判断伪装为多个独立项目。

## 导入人类创作方案

若要比较人类创作者，必须提供真实的人类方案。程序不以 DemoProvider 生成的文本代替人类，不给人类伪造模型分数。使用与原模型运行**完全一致**的任务文本，并记录收集协议、作者来源、可用时间、工具限制和提交顺序。最小输入如下，`ideas` 的字段与模型方案一致：

```json
{
  "schema_version": 1,
  "comparison_kind": "human",
  "demo": false,
  "task": "与 lab 运行完全一致的任务文本",
  "provenance": {
    "kind": "human",
    "collection_protocol": "记录实际收集方式、参与者抽样、时间、工具与冻结提交规则"
  },
  "resource_limits": {"time_minutes": 30, "tools": ["纸笔"]},
  "ideas": [{
    "id": "human-001",
    "title": "作者实际提交的标题",
    "mechanism": "combine",
    "test_type": "prototype",
    "description": "作者实际提交的因果机制与方案",
    "assumptions": ["作者实际声明的假设"],
    "risks": ["作者实际声明的风险"],
    "test": "作者实际设计的验证方式"
  }]
}
```

通过 `{"task_id": "...", "lab": lab_run, "baseline": human_run, "comparator_label": "human"}` 导入。人类方案按预先冻结的提交顺序选取；不凭空赋分。JSON 的 `human` 标签只是外部声明，不能自行证明作者身份与协议执行。保留真实收集记录，避免公开个人信息。

## 解释统计结果

`summarize_votes(pack, mapping, votes)` 验证映射和公开内容的哈希一致性，统计 lab 胜/平/负，分别按任务和比较来源汇总。胜率以胜加负为分母，平局单独报告；没有明确胜负时胜率与区间均为 `null`。95% Wilson 区间是描述性二项区间，假设明确胜负项目独立。

同一个任务的多个候选或种子属于聚类观测，不是独立任务。程序根据规范化任务文本统计不同任务；给同一文本更换 ID 不能增加独立任务数量。它保留按任务结果并明确提示伪重复；正式研究应对任务和评审员进行分层分析或按任务重采样，不能直接用汇总 Wilson 区间宣布显著性。至少 20 个明确胜负、12 个不同任务只是本项目的最低提示阈值，并非足够统计功效的保证。12 个任务默认盲评本身可能不足以满足该阈值；扩充独立任务比堆积同任务候选更有价值。

演示数据、未完成投票、少量项目、缺失/不同资源用量、未加入人类创作比较，都会产生明确限制。即使全部投票偏好 lab，程序也不会自动宣布“超越人类”。任何结论须限定到具体任务、参与者、模型、资源与评审协议；实用性还需要完成预先声明的现实实验。
