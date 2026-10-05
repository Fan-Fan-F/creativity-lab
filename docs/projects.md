# GitHub research: creativity as search plus verification

Research date: 2026-10-06, Asia/Shanghai. Public repositories were located with the agent-reach GitHub route (`gh search repos`) and checked using GitHub REST metadata, current README content, license content where special, and default-branch commit IDs. GitHub web pages were cross-checked through browsing. This is a source review; none of these external projects was installed or executed here. No external source code was copied into the proposed project.

## Eight strongest sources

| Project | What the current repository actually provides | Mechanism useful to a creativity system | Verified license / limitation |
| --- | --- | --- | --- |
| [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) | LLM-assisted program evolution, evaluators, examples, islands, MAP-Elites archive, model ensembles, artifact feedback, customizable feature bins | Preserve diverse good candidates; feed execution errors into the next proposal | Apache-2.0. Requires an evaluation function; repository performance claims are task-specific and were not reproduced here. Seed controls do not guarantee deterministic remote-model APIs. |
| [ShinkaEvolve](https://github.com/SakanaAI/ShinkaEvolve) | Scientific code evolution, successful-solution archive, local/Slurm evaluation, model ensembles, novelty generator example, API and CLI backends | Adaptive model/operator scheduling, novelty checks, separate proposal and evaluation throughput | Apache-2.0. README calls it particularly suited to tasks with a verifier. Config documents `llm_dynamic_selection="ucb"`, embedding similarity thresholds and parent-selection strategies. |
| [FunSearch](https://github.com/google-deepmind/funsearch) | Official companion code/data for cap sets, admissible sets, bin packing, cyclic graphs and a single-threaded evolution pipeline | Generate programs; execute a trusted evaluator; keep performant diverse programs rather than accepting fluent prose | Apache-2.0 software; CC-BY-4.0 other materials. README explicitly excludes the generating language model, untrusted-code sandbox, and distributed infrastructure. |
| [OpenELM](https://github.com/CarperAI/OpenELM) | Evolution of code/natural language with MAP-Elites variants, LM mutation/crossover, prompts, puzzles, images and QDAIF poetry | Model-assisted mutation/crossover plus a quality-diversity archive | MIT. QDAIF poetry is in main; other QDAIF/LMX experiments are on `qdaif-lmx-expt`. Main branch is older and has ecosystem/version drift risk. |
| [QDHF](https://github.com/ld-ing/qdhf) | Official ICML 2024 implementation; experiments infer diversity metrics from human judgments of similarity; robotics/RL and image generation demos | Let human similarity judgments inform what counts as meaningfully different | MIT. It is an experimental diversity-learning method, not a universal text creativity enhancer. A text adaptation requires new validation. |
| [LLaMEA](https://github.com/XAI-liacs/LLaMEA) | Automatic metaheuristic generation/refinement with fitness/error feedback; niching; optional hyperparameter optimization; diff edits | Separate structural invention from numerical tuning; maintain niches and return concrete failure feedback | MIT. Results depend on the chosen benchmark and evaluator; code execution and HPO have real compute costs. |
| [Darwin Gödel Machine](https://github.com/jennyzzt/dgm) | Agent code self-modification followed by empirical coding-benchmark evaluation, archived variants, SWE-bench/Polyglot setup | Save stepping stones and lineage; validate improvement before adopting a variant | Apache-2.0. Coding-agent capability evidence is narrower than general creativity or superhuman invention. Foundation-model weights remain frozen in the reported setup. |
| [AI Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2) | Hypothesis generation, literature novelty checks, experiment tree search, data analysis and manuscript generation | Convert an idea into falsifiable experiments; branch instead of repeatedly polishing one answer | **Custom AI Scientist Source Code License v1.0, December 2025**, with use restrictions and prominent AI manuscript disclosure. Do not call it Apache/MIT. README says v2 need not beat v1 and can have lower success rates under broader exploration. |

## Snapshot provenance

Current default-branch commits read from GitHub REST:

| Repository | Commit | Commit date (UTC) |
| --- | --- | --- |
| algorithmicsuperintelligence/openevolve | `4f4b0c4f40906f434d64fe5089aff24e927f7e24` | 2026-09-29 03:59:52 |
| SakanaAI/ShinkaEvolve | `8adc053a2ce4511ad2ac310e004c530a73fb974a` | 2026-10-05 04:18:27 |
| google-deepmind/funsearch | `cc53f274237d7ab05c19df939edbc1f9616a7c19` | 2024-02-05 10:32:17 |
| CarperAI/OpenELM | `c844e149e3f59fef546e0bc55f4e12e0f192feb9` | 2023-10-21 16:25:10 |
| ld-ing/qdhf | `8b06b3a8d9783aa70b02f241975e03006c58d489` | 2025-04-06 20:54:18 |
| XAI-liacs/LLaMEA | `0b08a42789d130081ee3dd3ab2b65b2ee339dced` | 2026-10-02 09:42:47 |
| jennyzzt/dgm | `a565fd2d1dca504ef5104a7cc0f3bdc4ab9b4fd2` | 2025-08-13 10:40:14 |
| SakanaAI/AI-Scientist-v2 | `96bd51617cfdbb494a9fc283af00fe090edfae48` | 2025-12-19 07:46:31 |

License evidence should be linked directly in a user-facing research document:

- [OpenEvolve LICENSE](https://github.com/algorithmicsuperintelligence/openevolve/blob/4f4b0c4f40906f434d64fe5089aff24e927f7e24/LICENSE)
- [ShinkaEvolve LICENSE](https://github.com/SakanaAI/ShinkaEvolve/blob/8adc053a2ce4511ad2ac310e004c530a73fb974a/LICENSE)
- [FunSearch license description](https://github.com/google-deepmind/funsearch#license-and-disclaimer)
- [OpenELM LICENSE](https://github.com/CarperAI/OpenELM/blob/c844e149e3f59fef546e0bc55f4e12e0f192feb9/LICENSE)
- [QDHF LICENSE](https://github.com/ld-ing/qdhf/blob/8b06b3a8d9783aa70b02f241975e03006c58d489/LICENSE)
- [LLaMEA LICENSE](https://github.com/XAI-liacs/LLaMEA/blob/0b08a42789d130081ee3dd3ab2b65b2ee339dced/LICENSE)
- [DGM LICENSE](https://github.com/jennyzzt/dgm/blob/a565fd2d1dca504ef5104a7cc0f3bdc4ab9b4fd2/LICENSE)
- [AI Scientist-v2 custom LICENSE](https://github.com/SakanaAI/AI-Scientist-v2/blob/96bd51617cfdbb494a9fc283af00fe090edfae48/LICENSE)

## Relevant auxiliary sources and exclusions

- [QDAIF scripts and data](https://github.com/qdaif/qdaif_scripts_data) contains archives, experiment-analysis scripts, raw histories and plotting support. No license was visible in repository metadata or its root README, so treat it as a reference; do not import its code/data without resolving rights. OpenELM is the better permissively licensed implementation source for the method.
- [Kaimen-Inc/Co-Scientist](https://github.com/Kaimen-Inc/Co-Scientist) is an **independent Apache-2.0 reimplementation**, not Google's official system. Its generation/reflection/ranking/evolution/proximity/meta-review roster is useful as an architectural reference, but its own evaluations were not reproduced here.
- [jataware/open-coscientist](https://github.com/jataware/open-coscientist) has **MIT plus Commons Clause**, not unrestricted MIT. The license restricts selling products/services substantially derived from its functionality. Reference the published algorithm instead of casually incorporating this implementation.
- [HKUDS/AI-Researcher](https://github.com/HKUDS/AI-Researcher) describes autonomous scientific innovation and NeurIPS 2025 work. GitHub metadata returned no license; it was not selected as a code dependency.

## Original synthesis for the new project

The following is an engineering inference from the reviewed sources, not evidence that this combination is already proven:

1. A user brief defines a target, constraints, evidence corpus and a frozen evaluation rubric.
2. Independent proposal operators explore analogy transfer, assumption inversion, mechanism recombination, constraint removal and adversarial repair. Different roles/operators should change mechanisms, not merely tone.
3. Every proposal is a structured record: mechanism, predicted benefit, assumptions, nearest-known alternatives, falsification test, implementation plan, evidence IDs and uncertainty.
4. Assess utility and feasibility separately from novelty. Reject contradictions and unsupported factual claims before archive admission. Novelty is relative to a stated corpus, not proof that an idea is unprecedented globally.
5. Keep the best proposal in each behavior niche. Descriptors should describe mechanism/domain/interaction/cost/risk; do not collapse them into a single novelty score.
6. Sample both successful parents and underexplored niches. Preserve nonwinning stepping stones with lineage. Recombine candidates from different mechanisms.
7. Adapt operator selection with a bandit over archive improvement per cost. Keep a deliberate exploration quota so early rewards cannot eliminate risky but promising directions.
8. Use frozen validators and append-only event logs; the proposed system must not rewrite its evaluator or treat its own praise as observed real-world success.
9. Deliver a shortlist with experiments, uncertainty and provenance. An idea is a hypothesis until its predicted result is observed.

## Evaluation needed before strong claims

Compare against plain generation and best-of-N using the same model, total generation calls/tokens and candidate budget. Repeat across multiple seeds and tasks. Report archive coverage, duplicate rate, feasible-candidate rate, held-out quality, uncertainty, cost and independent blinded ratings. Numerical novelty based on lexical similarity is a transparent proxy only. An LLM judge is a screening tool, not ground truth. Human comparisons require named task population, blinded rubric and confidence intervals; no reviewed project establishes universal superhuman creativity.

Agent Reach check-update returned current version v1.5.0 / already latest. No upgrade performed.
