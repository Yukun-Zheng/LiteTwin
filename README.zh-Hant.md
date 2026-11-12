# LiteTwin

[English](README.md) · [简体中文](README.zh-Hans.md) · **繁體中文**

[**進入 LiteTwin 互動示範網站 →**](https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant) · [執行打開抽屜範例](#run-open-drawer) · [檢視策略實作](code/litetwin/policy/feedback/)

**用於機器人操作的任務狀態回饋。** 物體從夾爪中滑落時，機械臂仍可能沿原軌跡前進。LiteTwin 同時追蹤任務進度，以及夾爪與物體的接觸關係。兩者不一致時，控制器可以暫停推進，恢復接觸，再從當前場景支持的狀態繼續執行。

[![雙臂抬托盤的正面並排影片，觀察托盤中央的方塊。](docs/figures/tray-comparison.zh-Hant.gif)](https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#bimanual_lift_tray)

*留意托盤中央的方塊。約第 8 秒，左側托盤開始傾倒；右側兩端隨後一起升起，方塊仍留在托盤上。*

## 三組值得對照看的動作

留意物體有沒有跟著夾爪移動。每組的起始場景、隨機種子與擾動都相同。兩側影片從頭同步播放，註解會隨動作變化。

| 任務 | 觀察時段 | 運動策略 | 運動策略 + LiteTwin | 影片 |
| --- | --- | --- | --- | --- |
| [雙臂抬托盤](https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#bimanual_lift_tray) | 8–10 秒 | 一端先抬起，托盤翻倒在桌上。 | 兩端一起抬起，方塊隨托盤升高。 | [MP4](https://yukun-zheng.github.io/LiteTwin/highlights/bimanual_lift_tray.zh-Hant.mp4) |
| [打開抽屜](https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#open_drawer_delay) | 6–8 秒 | 機械臂空手移開，最上層抽屜仍關著。 | 同一時段內，夾爪已把最上層抽屜拉開。 | [MP4](https://yukun-zheng.github.io/LiteTwin/highlights/open_drawer_delay.zh-Hant.mp4) |
| [蓋上罐蓋](https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#close_jar) | 7–16 秒 | 空夾爪移到罐口，灰色蓋子還在桌上。 | 首次抓取落空後重新抓住蓋子，將它放到紅罐上。 | [MP4](https://yukun-zheng.github.io/LiteTwin/highlights/close_jar.zh-Hant.mp4) |

<details><summary>展開全部 12 項任務、13 組配對影片</summary>

示範庫也保留了雙方都完成及雙方都未完成的回合。打開抽屜有兩組影片，擾動不同。下方可重新執行的範例對應接觸中斷的那一組。

<table>
  <tr>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#open_drawer"><img src="docs/media/open_drawer/preview.jpg" alt="打開抽屜" width="260"><br>打開抽屜</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#insert_square_peg"><img src="docs/media/insert_square_peg/preview.jpg" alt="方環套柱" width="260"><br>方環套柱</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#handover_item"><img src="docs/media/handover_item/preview.jpg" alt="雙臂交接物體" width="260"><br>雙臂交接物體</a></td>
  </tr>
  <tr>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#stack_cups"><img src="docs/media/stack_cups/preview.jpg" alt="堆疊杯子" width="260"><br>堆疊杯子</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#close_jar"><img src="docs/media/close_jar/preview.jpg" alt="蓋上罐蓋" width="260"><br>蓋上罐蓋</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#place_cups_3"><img src="docs/media/place_cups_3/preview.jpg" alt="掛起三個杯子" width="260"><br>掛起三個杯子</a></td>
  </tr>
  <tr>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#remove_cups_2"><img src="docs/media/remove_cups_2/preview.jpg" alt="取下兩個杯子" width="260"><br>取下兩個杯子</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#push_buttons_2"><img src="docs/media/push_buttons_2/preview.jpg" alt="按下兩個按鈕" width="260"><br>按下兩個按鈕</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#sweep_to_dustpan"><img src="docs/media/sweep_to_dustpan/preview.jpg" alt="單臂清掃" width="260"><br>單臂清掃</a></td>
  </tr>
  <tr>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#bimanual_lift_tray"><img src="docs/media/bimanual_lift_tray/preview.jpg" alt="雙臂抬托盤" width="260"><br>雙臂抬托盤</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#bimanual_put_bottle_in_fridge"><img src="docs/media/bimanual_put_bottle_in_fridge/preview.jpg" alt="把瓶子放進冰箱" width="260"><br>把瓶子放進冰箱</a></td>
    <td width="33%" align="center"><a href="https://yukun-zheng.github.io/LiteTwin/?lang=zh-Hant#bimanual_sweep_to_dustpan"><img src="docs/media/bimanual_sweep_to_dustpan/preview.jpg" alt="雙臂協作清掃" width="260"><br>雙臂協作清掃</a></td>
  </tr>
</table>

播放器內可單獨開啟全部 [26 段原視角影片](docs/media/)。在倉庫根目錄執行 `python -m http.server 8000 --directory docs`，然後開啟 `http://localhost:8000`。

</details>

<a id="run-open-drawer"></a>

## 執行打開抽屜範例

### 1. 以五條示範軌跡建立模型

在倉庫根目錄安裝策略環境並執行離線階段。此階段使用隨附的低維軌跡及凍結的運動策略檢查點，可在 CPU 上執行，無須安裝模擬器。

```bash
conda env create -f demo/environment-policy.yml
conda activate litetwin-policy
python -m pip install -e ./code
export LITETWIN_POLICY_PYTHON="$(python -c 'import sys; print(sys.executable)')"
bash demo/run_offline.sh
```

五條軌跡長度不一，但共享三項依序執行的技能。模型得到 **101 個任務狀態**，三項技能分別有 **59、15、27** 個狀態，另有兩個帶條件的技能邊界及一個恢復錨點。可對照[軌跡分段圖](docs/figures/task-structure.zh-Hant.svg)、`outputs/offline/model_summary.json` 及 `outputs/models/litetwin/open_drawer/` 下的模型。

### 2. 執行帶擾動的回合

按照 [Linux 模擬器安裝說明](demo/SETUP.zh-Hant.md) 設定相依元件，然後執行：

```bash
bash demo/run_online.sh open_drawer
```

程式載入建立的模型，在指定種子的 RLBench 場景中引入接觸中斷，檢查擾動是否實際發生、任務是否完成，並在 `outputs/online/` 下儲存連續影片、JSON 結果、逐週期紀錄及時間軸。執行 `bash demo/run.sh open_drawer` 可依序執行兩個階段。重新建立會取代先前的模型與相關的線上輸出。

### 閱讀控制時間軸

![按控制週期排列的任務狀態參考、控制器模式與物理接觸](docs/figures/execution-timeline.zh-Hant.svg)

在一次重跑中，第 **72** 個控制週期失去接觸，第 **78** 個週期開始恢復，第 **121** 個週期重新進入受支持的任務狀態，**135** 個週期後模擬器回報成功。上方曲線是指令給出的任務狀態參考，中間色帶是控制器模式，下方色帶是觀測到的物理接觸。水平線表示進度維持。橫軸以控制週期計，影片以播放時間計。[逐週期資料](docs/figures/open-drawer-trace.csv)

## 程式碼結構

[任務模型](code/litetwin/policy/feedback/model/) 從示範中擷取技能邊界、物體關係及場景限制。[推論](code/litetwin/policy/feedback/inference/) 更新進度與關係估計，[控制](code/litetwin/policy/feedback/control/) 決定維持、推進或跨越技能邊界，[恢復](code/litetwin/policy/feedback/recovery/) 負責驗證接觸、重新取得物體及選擇重新進入的狀態。[RLBench 介面](code/integrations/rlbench/rlbench_litetwin/) 連接觀測、擾動及模擬器動作。底層動作參考由凍結的 DynaMAC 運動策略提供。

## 授權條款

LiteTwin 原創程式碼採用 [MIT 授權條款](LICENSE)。外部模擬元件沿用各自的上游授權，詳見[第三方聲明與固定版本](code/integrations/rlbench/THIRD_PARTY.md)。
