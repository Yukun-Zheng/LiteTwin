# LiteTwin policy

`litetwin.policy.feedback` is the benchmark-independent core. It augments an object-centric motion policy with a
structured task-state model and a closed execution loop.

## Architecture

```text
feedback/
├── model/       offline task states, relations, scene factors, and boundaries
├── inference/   runtime features and coupled relation/progress inference
├── control/     stream roles, guarded execution, and boundary transactions
├── recovery/    active verification, relation repair, and legal re-entry
├── policy.py    complete policy lifecycle and action transaction
├── config.py    typed configuration aggregation
├── state.py     public cycle input/output state
├── diagnostics.py
```

The core has no RLBench imports. A benchmark adapter provides task-relevant
frame observations, end-effector state, executed-action feedback, and a
read-only query interface for the underlying motion policy.

## Execution cycle

Each committed control cycle follows one causal order:

1. predict local progress from the previously executed action;
2. update external/linked relation beliefs from the observed physical response;
3. correct task progress with motion, scene, and relation compatibility;
4. route reference streams and choose hold, advance, or within-skill realignment;
5. evaluate local, relation, and scene guards at task boundaries;
6. verify ambiguous required relations or repair reliable mismatches; and
7. re-enter only a state supported by the current robot, relation, and scene state.

Normal progress is frozen during verification and recovery. In bimanual tasks,
each arm keeps its own progress belief while shared boundaries are committed
atomically.

## Main interfaces

- `LiteTwinTaskModelBuilder` constructs a task model from successful
  demonstrations and a fitted motion policy.
- `LiteTwinTaskModel` stores progress-indexed motion, relation, scene, event, and
  boundary support.
- `BeliefUpdater` performs the coupled online relation/progress update.
- `LiteTwinExecutionController` maps the inferred state to reference and stream-role
  decisions.
- `LiteTwinRecoveryManager` coordinates verification, relation repair, and re-entry.
- `LiteTwinMultiStreamPolicy` exposes the reset/act/commit/abort lifecycle used by
  environment adapters.

Public types are re-exported from `litetwin.policy.feedback`. Serialized task models
bind their underlying motion checkpoints by content digest so that incompatible
policy/model pairs fail during loading rather than during execution.

## Configuration

The default configuration is split by responsibility:

```text
configs/litetwin_task_model.json
configs/litetwin_inference.json
configs/litetwin_execution.json
configs/litetwin_recovery.json
integrations/rlbench/configs/open_drawer/boundary.json
```

Task configuration defines the observable entities, candidate reference
frames, and boundary support required by the generic controller.
