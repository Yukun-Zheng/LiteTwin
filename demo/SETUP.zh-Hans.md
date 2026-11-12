# 运行开抽屉仿真

[English](SETUP.md) · **简体中文** · [繁體中文](SETUP.zh-Hant.md)

仿真器运行在 x86-64 Linux 上，使用 CoppeliaSim Edu 4.1 的 Ubuntu 20.04 版本。Python 3.8 仿真进程把观测发送给 Python 3.10 策略进程。先完成[离线模型构建](../README.zh-Hans.md#run-open-drawer)，再从仓库根目录执行以下命令。

## 安装仿真接口

安装 Conda、Git、C/C++ 编译工具、支持 libx264 的 FFmpeg，以及 Xvfb、xauth、patch 和 curl。单独解压 CoppeliaSim Edu 4.1。安装 PyRep 前，将下方示例路径改为实际解压位置。

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

启动脚本直接从检出的目录导入 RLBench，并从中读取场景和任务资源。

## 构建逆运动学依赖

脚本校验固定版本的源码、补丁和依赖锁定文件，然后把支持笛卡尔约束的求解器安装到独立目录中。目标目录应为空。

```bash
bash code/integrations/rlbench/build_pytracik_bounded.sh \
  "${CONDA_EXE:-$(type -P conda)}" "$PWD/third_party/pytracik-runtime"
```

## 启动回合

在启动仿真的终端保留 `COPPELIASIM_ROOT`，并设置两个 Python 解释器和依赖路径。

```bash
export LITETWIN_POLICY_PYTHON="$(conda run -n litetwin-policy python -c 'import sys; print(sys.executable)')"
export LITETWIN_SIM_PYTHON="$(conda run -n litetwin-simulator python -c 'import sys; print(sys.executable)')"
export LITETWIN_RLBENCH_ROOT="$PWD/third_party/RLBench"
export LITETWIN_PYTRACIK_PREFIX="$PWD/third_party/pytracik-runtime"
bash demo/run_online.sh open_drawer
```

结果保存在 `outputs/online/`，包括 `open_drawer.json`、`open_drawer.mp4`、`open_drawer_records/` 和 `execution_timeline.svg`。[回合配置](episodes/open_drawer.json) 给出种子、场景变体、最大步数和物理扰动。
