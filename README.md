# LiteTwin

**English** · [简体中文](README.zh-Hans.md) · [繁體中文](README.zh-Hant.md)

**Task-state feedback for robot manipulation**

LiteTwin adds task progress and gripper–object contact to a frozen motion policy. When the motion reference moves ahead but the object stays behind, the controller can pause, recover contact and resume the task.

[Interactive demos](https://yukun-zheng.github.io/LiteTwin/?lang=en) · [Quick start](#quickstart) · [Policy API](code/litetwin/policy/feedback/README.md) · [MIT license](LICENSE)

<a id="film"></a>

## Project film · 40 seconds

[![A short preview of three recorded RLBench comparisons. The film follows the tray, drawer and jar lid through the same disturbance on both sides.](docs/film/en.gif)](https://yukun-zheng.github.io/LiteTwin/?lang=en#film)

A short preview of three recorded RLBench comparisons. The film follows the tray, drawer and jar lid through the same disturbance on both sides.

[**Play the full film →**](https://yukun-zheng.github.io/LiteTwin/?lang=en#film) · [Open MP4](https://yukun-zheng.github.io/LiteTwin/film/en.mp4)

| Chapter | Follow the object |
| --- | --- |
| [0:00 · Tray](https://yukun-zheng.github.io/LiteTwin/?lang=en#film-tray) | One arm lifts first. Watch whether the cube rises with the tray. |
| [0:12 · Drawer](https://yukun-zheng.github.io/LiteTwin/?lang=en#film-drawer) | As the arm moves away, does the top drawer move with it? |
| [0:22 · Jar lid](https://yukun-zheng.github.io/LiteTwin/?lang=en#film-jar) | After the missed pickup, follow the gray lid to the red jar. |

On the interactive site, each chapter opens its full two-camera comparison. The shorter side holds its last frame.

[View the three outcomes side by side](docs/figures/rollout-comparison.jpg) · [Task library](https://yukun-zheng.github.io/LiteTwin/?lang=en#library)

<a id="quickstart"></a>

## Quick start

With Git and Python 3, serve all 13 recorded comparisons from a local checkout.

```bash
git clone https://github.com/Yukun-Zheng/LiteTwin.git
cd LiteTwin
python3 -m http.server 8000 --directory docs
```

Open `http://localhost:8000`. To build a policy on CPU and run a new Open Drawer episode in RLBench, continue below. Open Drawer is the runnable task included in this release.

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

## Task gallery

<details><summary>Explore all 12 tasks and 13 recorded pairs</summary>

The gallery also includes episodes where both controllers finish or both fall short. Open Drawer has two recordings with different disturbances. The runnable example below uses the contact-loss episode.

<table>
  <tr>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=en#open_drawer"><img src="docs/media/open_drawer/preview.jpg" alt="Open Drawer" width="260"><br>Open Drawer</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=en#insert_square_peg"><img src="docs/media/insert_square_peg/preview.jpg" alt="Fit Ring onto Peg" width="260"><br>Fit Ring onto Peg</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=en#handover_item"><img src="docs/media/handover_item/preview.jpg" alt="Handover Item" width="260"><br>Handover Item</a></td>
  </tr>
  <tr>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=en#stack_cups"><img src="docs/media/stack_cups/preview.jpg" alt="Stack Cups" width="260"><br>Stack Cups</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=en#close_jar"><img src="docs/media/close_jar/preview.jpg" alt="Close Jar" width="260"><br>Close Jar</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=en#place_cups_3"><img src="docs/media/place_cups_3/preview.jpg" alt="Hang Three Cups" width="260"><br>Hang Three Cups</a></td>
  </tr>
  <tr>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=en#remove_cups_2"><img src="docs/media/remove_cups_2/preview.jpg" alt="Remove Two Cups" width="260"><br>Remove Two Cups</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=en#push_buttons_2"><img src="docs/media/push_buttons_2/preview.jpg" alt="Press Two Buttons" width="260"><br>Press Two Buttons</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=en#sweep_to_dustpan"><img src="docs/media/sweep_to_dustpan/preview.jpg" alt="Sweep into Dustpan" width="260"><br>Sweep into Dustpan</a></td>
  </tr>
  <tr>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=en#bimanual_lift_tray"><img src="docs/media/bimanual_lift_tray/preview.jpg" alt="Lift Tray Together" width="260"><br>Lift Tray Together</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=en#bimanual_put_bottle_in_fridge"><img src="docs/media/bimanual_put_bottle_in_fridge/preview.jpg" alt="Put Bottle in Fridge" width="260"><br>Put Bottle in Fridge</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=en#bimanual_sweep_to_dustpan"><img src="docs/media/bimanual_sweep_to_dustpan/preview.jpg" alt="Sweep with Two Arms" width="260"><br>Sweep with Two Arms</a></td>
  </tr>
</table>

Open any of the [26 individual recordings](docs/media/) from the player. To run the gallery locally, use `python -m http.server 8000 --directory docs` and open `http://localhost:8000`.

</details>

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
