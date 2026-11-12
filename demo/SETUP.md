# Running the Open Drawer simulation

The simulator runs on x86-64 Linux and uses the Ubuntu 20.04 build of CoppeliaSim Edu 4.1. The Python 3.8 simulator process sends observations to the Python 3.10 policy process. Run the following commands from the repository root after completing the offline stage in the [main guide](../README.md#run-open-drawer).

## Install the simulator interfaces

Install Conda, Git, a C/C++ build toolchain, FFmpeg with libx264, Xvfb, xauth, patch and curl. Extract CoppeliaSim Edu 4.1 separately and set the path to the extracted directory before installing PyRep.

```bash
export COPPELIASIM_ROOT=/path/to/CoppeliaSim_Edu_V4_1_0_Ubuntu20_04
export LD_LIBRARY_PATH="$COPPELIASIM_ROOT${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export QT_QPA_PLATFORM_PLUGIN_PATH="$COPPELIASIM_ROOT"

conda env create -f demo/environment-simulator.yml
mkdir -p third_party
git clone https://github.com/vonHartz/PyRep.git third_party/PyRep
git -C third_party/PyRep checkout b8bd1d7a3182adcd570d001649c0849047ebf197

git clone https://github.com/vonHartz/RLBench.git third_party/RLBench
git -C third_party/RLBench checkout a51b4e609dc5c3e1a8c06046bd87a9da24723da4
git -C third_party/RLBench apply \
  "$PWD/code/integrations/rlbench/patches/rlbench-a51b4e-python38-annotations.patch"

conda run -n litetwin-simulator python -m pip install --no-deps --no-build-isolation ./third_party/PyRep
```

The launcher imports RLBench directly from the checkout, which also supplies the scene and task assets.

## Build the inverse-kinematics dependency

The build script checks the pinned source, patch and package lock, then installs a bounded Cartesian solver into an isolated prefix. Start with an empty destination.

```bash
bash code/integrations/rlbench/build_pytracik_bounded.sh \
  "${CONDA_EXE:-$(type -P conda)}" "$PWD/third_party/pytracik-runtime"
```

## Start a rollout

In the shell that starts the demo, keep `COPPELIASIM_ROOT` set and export the two interpreter paths and both dependencies.

```bash
export LITETWIN_POLICY_PYTHON="$(conda run -n litetwin-policy python -c 'import sys; print(sys.executable)')"
export LITETWIN_SIM_PYTHON="$(conda run -n litetwin-simulator python -c 'import sys; print(sys.executable)')"
export LITETWIN_RLBENCH_ROOT="$PWD/third_party/RLBench"
export LITETWIN_PYTRACIK_PREFIX="$PWD/third_party/pytracik-runtime"
bash demo/run_online.sh open_drawer
```

The run writes `outputs/online/open_drawer.json`, `open_drawer.mp4`, `open_drawer_records/` and `execution_timeline.svg` under the online output directory. The [episode definition](episodes/open_drawer.json) sets the seed, variation, horizon and physical perturbation.
