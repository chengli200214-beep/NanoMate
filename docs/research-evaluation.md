# Evaluating research workflows

The reproducible runner is `scripts/evaluate_research.py`; its input matrix and usage
are documented in `evals/research/README.md` in the source repository.

By default it freezes the 12 synthetic scenarios and the 32-execution first-round
matrix, plus four preregistered reruns, without calling a model. Explicit fixture
execution drives the real store and orchestrator with scripted `MockLLM` responses.
Each attempt retains its database, reports, source snapshots, calls, and failures in
a separate directory. Setup usage is separate from target-update usage. Existing
result directories cannot be overwritten.

Fixture traces verify workflow execution only. They do not establish semantic evidence
support, model accuracy, useful reminders, or improvement over a baseline. Live mode
uses the configured model against the same synthetic inputs. Draft annotations still
require human review, and absent token usage remains null. Read the runner's README
for the precise scope of its two experimental interventions.

## 中文

评测运行器默认只冻结场景、SHA-256 和运行矩阵，不请求模型。显式选择 fixture 后，才会
用固定资料和 `MockLLM` 真实执行存储、编排及报告流程。12 个合成场景对应 32 次首轮运行，
另有四次预先指定的复跑。每次尝试保留独立数据库、报告、调用和失败，不覆盖历史结果。

离线通过只能证明脚本流程运行，不能写成语义正确率、有效提醒或模型效果改善。live 模式
沿用现有模型配置，输入仍为同一组合成资料。标注待人工审查，缺失 token 不估算补全。
