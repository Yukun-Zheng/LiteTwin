# 執行打開抽屜模擬

[English](SETUP.md) · [简体中文](SETUP.zh-Hans.md) · **繁體中文**

模擬器在 x86-64 Linux 上執行，使用 CoppeliaSim Edu 4.1 的 Ubuntu 20.04 版本。Python 3.8 模擬程序將觀測傳給 Python 3.10 策略程序。先完成[離線模型建立](../README.zh-Hant.md#run-open-drawer)，再從倉庫根目錄執行以下指令。

## 安裝模擬介面

安裝 Conda、Git、C/C++ 編譯工具、支援 libx264 的 FFmpeg，以及 Xvfb、xauth、patch 和 curl。另行解壓縮 CoppeliaSim Edu 4.1。安裝 PyRep 前，請將下方範例路徑改為實際解壓縮位置。

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

啟動指令碼直接從取出的目錄匯入 RLBench，並從中讀取場景與任務資源。

## 建置逆運動學相依元件

指令碼會檢查固定版本的原始碼、修補檔及套件鎖定檔，再將支援笛卡兒約束的求解器安裝到獨立目錄。目標目錄應為空。

```bash
bash code/integrations/rlbench/build_pytracik_bounded.sh \
  "${CONDA_EXE:-$(type -P conda)}" "$PWD/third_party/pytracik-runtime"
```

## 啟動回合

在啟動模擬的終端機保留 `COPPELIASIM_ROOT`，並設定兩個 Python 直譯器及相依元件的路徑。

```bash
export LITETWIN_POLICY_PYTHON="$(conda run -n litetwin-policy python -c 'import sys; print(sys.executable)')"
export LITETWIN_SIM_PYTHON="$(conda run -n litetwin-simulator python -c 'import sys; print(sys.executable)')"
export LITETWIN_RLBENCH_ROOT="$PWD/third_party/RLBench"
export LITETWIN_PYTRACIK_PREFIX="$PWD/third_party/pytracik-runtime"
bash demo/run_online.sh open_drawer
```

結果儲存在 `outputs/online/`，包括 `open_drawer.json`、`open_drawer.mp4`、`open_drawer_records/` 及 `execution_timeline.svg`。[回合設定](episodes/open_drawer.json) 列出種子、場景變體、最大步數及物理擾動。
