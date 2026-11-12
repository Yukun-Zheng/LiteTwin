# LiteTwin

**Task-state feedback for robot manipulation.** A gripper can follow its planned path after the object has slipped away. LiteTwin tracks the physical relation between hand and object alongside task progress. When the two disagree, it can hold the motion reference, recover contact and resume from a state supported by the scene.

[**Watch the paired rollouts →**](https://yukun-zheng.github.io/LiteTwin/) · [Run Open Drawer](#run-open-drawer) · [Explore the policy](code/litetwin/policy/feedback/)

[![Open Drawer. Two front-camera recordings start together. The motion policy runs on the left and the same policy with LiteTwin runs on the right.](docs/figures/open-drawer.gif)](https://yukun-zheng.github.io/LiteTwin/#open_drawer)

*Open Drawer, front view. Both controllers start with the same drawer and encounter the same loss of contact. The left rollout ends with the task incomplete. On the right, the gripper re-establishes contact and finishes the pull.*

## Three disturbances to watch

Each row links to a recorded pair with the same initialization, seed and disturbance. The viewer plays the front and overhead cameras together and lets you scrub both runs on a shared clock. These are three selected episodes. The runnable example below covers Open Drawer.

| Task | What to watch | Paired recordings |
| --- | --- | --- |
| Open Drawer | The drawer slips during the pull. Watch the gripper return while the task reference is held. | [Compare ↗](https://yukun-zheng.github.io/LiteTwin/#open_drawer) |
| Insert Square Peg | The first grasp misses the peg. Watch whether the arm travels toward the socket with the peg in hand. | [Compare ↗](https://yukun-zheng.github.io/LiteTwin/#insert_square_peg) |
| Handover Item | The grasp is disturbed during a two-arm transfer. Watch which hand holds the item as the arms separate. | [Compare ↗](https://yukun-zheng.github.io/LiteTwin/#handover_item) |

The [six MP4 files](docs/media/) also open individually. To serve the viewer from a clone, run `python -m http.server 8000 --directory docs` and open `http://localhost:8000`.

## Run Open Drawer

### 1. Build the model from five demonstrations

From the repository root, create the policy environment and run the offline stage. This stage uses the bundled low-dimensional trajectories and frozen motion checkpoint. It runs on CPU and needs no simulator.

```bash
conda env create -f demo/environment-policy.yml
conda activate litetwin-policy
python -m pip install -e ./code
export LITETWIN_POLICY_PYTHON="$(python -c 'import sys; print(sys.executable)')"
bash demo/run_offline.sh
```

The five demonstrations have different lengths but share **three ordered skills**. The fitted model has **101 task states** distributed **59 / 15 / 27**, two guarded boundaries and one recovery anchor. The [segmentation plot](docs/figures/task-structure.svg) shows the measured boundaries for each trajectory. Inspect the same values in `outputs/offline/model_summary.json` and the generated policy bundle in `outputs/models/litetwin/open_drawer/`.

### 2. Run the perturbed episode

Follow the [Linux simulator setup](demo/SETUP.md), then run the online stage from the same checkout.

```bash
bash demo/run_online.sh open_drawer
```

The launcher uses the saved model, starts a seeded RLBench scene, induces a loss of contact and records the run. It checks that the physical disturbance occurred and that RLBench reported completion. The output includes a continuous MP4, a JSON outcome, per-cycle records and an execution trace under `outputs/online/`. To rebuild and run both stages together, use `bash demo/run.sh open_drawer`. Rebuilding replaces the previous model and the online outputs derived from it.

### Read the execution trace

![A step plot shows the controller's task-state reference, with aligned bands for controller mode and physical relation during the Open Drawer episode.](docs/figures/execution-timeline.svg)

This trace comes from a fresh run of the packaged demo. Contact loss occurred at cycle **72**, recovery began at **78**, and the controller re-entered at **121**. RLBench reported success after **135 cycles**. The [cycle data](docs/figures/open-drawer-trace.csv) accompany the plot.

Read downward at the same control cycle. The line gives the **commanded task-state reference**, the middle strip gives the **controller mode**, and the lower strip records the **physical relation**. At re-entry the controller can deliberately return to an earlier supported state. The horizontal axis counts control cycles. The videos use elapsed playback time.

## Implementation

| Component | Role |
| --- | --- |
| [Task model](code/litetwin/policy/feedback/model/) | Align demonstrated skills and extract progress-indexed motion, relation and scene support. |
| [Inference](code/litetwin/policy/feedback/inference/) | Update the relation and progress estimates from observations and executed actions. |
| [Control](code/litetwin/policy/feedback/control/) | Decide when to hold, advance or cross a skill boundary. |
| [Recovery](code/litetwin/policy/feedback/recovery/) | Verify contact, repair a mismatch and choose a supported re-entry state. |
| [RLBench integration](code/integrations/rlbench/rlbench_litetwin/) | Connect the policy to observations, perturbations and simulator actions. |

The action backbone is a frozen object-centric DynaMAC implementation. LiteTwin supplies task-state feedback around that backbone. The [core API](code/litetwin/policy/feedback/README.md) describes the policy lifecycle and interfaces.

## License

LiteTwin's original code is available under the [MIT License](LICENSE). External simulator components retain their upstream licenses. See the [third-party notices and pinned revisions](code/integrations/rlbench/THIRD_PARTY.md).
