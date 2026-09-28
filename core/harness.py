"""
Uniform run record -- compatibility import path.

The implementation moved to agentkit/record/run_record.py: same TaskRun / Step / Harness API as before
(`TaskRun(task_id, prompt)`, `add_step(kind, target, ok, detail)`,
`save(proofs_dir)`, `TaskRun.load(path)`), now with atomic writes, a
"saved first as running" option (`open_in(run_dir)`) and the harness
fields (run_id, dry_run, finding, warnings, ...).
"""

from agentkit.record.run_record import Harness, Step, TaskRun

__all__ = ["Harness", "Step", "TaskRun"]
