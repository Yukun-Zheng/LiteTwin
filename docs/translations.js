const translations = {
  "en": {
    "title": "LiteTwin · Robot manipulation demos",
    "home": "LiteTwin home",
    "language": "Language",
    "headline": "When a grasp slips or an action arrives late",
    "intro": "Compare two runs of the same motion policy. Both start from the same scene and encounter the same disturbance. The right-hand run adds LiteTwin task-state feedback.",
    "choose": "Choose a task",
    "previous": "Previous task",
    "next": "Next task",
    "episode": "Episode",
    "motion": "Motion policy",
    "baseView": "Motion policy, front and overhead views",
    "feedbackView": "Motion policy with LiteTwin, front and overhead views",
    "success": "Completed",
    "failure": "Incomplete",
    "file": "Open MP4 ↗",
    "play": "Play pair",
    "pause": "Pause pair",
    "replay": "Replay pair",
    "seek": "Seek both recordings",
    "loading": "Loading…",
    "error": "Video unavailable · open the MP4",
    "dataError": "Gallery unavailable · open the MP4",
    "interrupted": "Playback interrupted · retry or open the MP4",
    "seeking": "Seeking…",
    "finished": "Finished",
    "playing": "Playing",
    "paused": "Paused",
    "ready": "Ready",
    "physics": "Front and overhead views · physics-step capture at 2× speed. The shorter recording holds its final frame.",
    "control": "Front and overhead views · control-step replay at 12 fps. The shorter recording holds its final frame.",
    "explore": "Explore the tasks",
    "count": "12 recorded pairs",
    "traceTitle": "Inside the Open Drawer run",
    "traceBody": "In a fresh run of the packaged demo, contact breaks at cycle 72. Recovery starts at 78 and the controller returns to supported progress at 121. RLBench reports completion after 135 cycles.",
    "traceLink": "Open full trace ↗",
    "traceAlt": "Commanded task state, controller mode and physical contact by control cycle",
    "reproduce": "Reproduce Open Drawer ↗",
    "relation_loss": "Contact loss",
    "missed_interaction": "Missed interaction",
    "actuation_delay": "Actuation delay",
    "coordination_delay": "Arm coordination delay",
    "environment_change": "Scene change",
    "tasks": {
      "open_drawer": {
        "name": "Open Drawer",
        "description": "The gripper loses contact during the pull. Watch it return to the handle before the drawer opens."
      },
      "insert_square_peg": {
        "name": "Fit Ring onto Peg",
        "description": "The first grasp misses the square ring. Watch whether the ring moves with the gripper toward the target post."
      },
      "handover_item": {
        "name": "Handover Item",
        "description": "A grasp is disturbed during the transfer. Watch which hand holds the item as the arms separate."
      },
      "stack_cups": {
        "name": "Stack Cups",
        "description": "Contact breaks during stacking. Follow the cup through the release and check the final stack."
      },
      "close_jar": {
        "name": "Close Jar",
        "description": "A missed interaction interrupts the lid pickup. Follow the lid from the table to the target jar."
      },
      "place_cups_3": {
        "name": "Hang Three Cups",
        "description": "An action is delayed while three cups are being hung. Count how many reach the rack in each view."
      },
      "remove_cups_2": {
        "name": "Remove Two Cups",
        "description": "The scene changes partway through unloading the rack. Follow each cup from its hook to the table."
      },
      "push_buttons_2": {
        "name": "Press Two Buttons",
        "description": "An action is delayed early in the sequence. Both policies finish the two-button task in this episode."
      },
      "sweep_to_dustpan": {
        "name": "Sweep into Dustpan",
        "description": "The arm loses contact during sweeping. Neither controller finishes moving the debris into the dustpan in this episode."
      },
      "bimanual_lift_tray": {
        "name": "Lift Tray Together",
        "description": "A coordination delay puts the arms out of step. Compare the two ends of the tray as it lifts."
      },
      "bimanual_put_bottle_in_fridge": {
        "name": "Put Bottle in Fridge",
        "description": "Contact breaks during the two-arm task. Track the bottle relative to the fridge door and shelf."
      },
      "bimanual_sweep_to_dustpan": {
        "name": "Sweep with Two Arms",
        "description": "An early interaction is missed. Watch the brush and dustpan meet as the two arms coordinate."
      }
    }
  },
  "zh-Hans": {
    "title": "LiteTwin · 机器人操作演示",
    "home": "LiteTwin 首页",
    "language": "语言",
    "headline": "当抓取松脱，或动作慢了一拍",
    "intro": "对照观看同一运动策略的两次执行。每组从相同场景开始，遇到相同扰动；右侧加入了 LiteTwin 的任务状态反馈。",
    "choose": "选择任务",
    "previous": "上一个任务",
    "next": "下一个任务",
    "episode": "回合",
    "motion": "运动策略",
    "baseView": "运动策略，正面和俯视画面",
    "feedbackView": "运动策略加入 LiteTwin，正面和俯视画面",
    "success": "已完成",
    "failure": "未完成",
    "file": "打开 MP4 ↗",
    "play": "播放对照",
    "pause": "暂停播放",
    "replay": "重新播放",
    "seek": "调整两段录像的播放进度",
    "loading": "加载中…",
    "error": "视频加载失败，可单独打开 MP4",
    "dataError": "任务列表加载失败，可单独打开 MP4",
    "interrupted": "播放中断，可重试或打开 MP4",
    "seeking": "跳转中…",
    "finished": "播放完毕",
    "playing": "播放中",
    "paused": "已暂停",
    "ready": "就绪",
    "physics": "正面与俯视双视角 · 按物理仿真步采样，以 2 倍速播放。较短的录像结束后停在最后一帧。",
    "control": "正面与俯视双视角 · 按控制步记录，以每秒 12 帧回放。较短的录像结束后停在最后一帧。",
    "explore": "浏览全部任务",
    "count": "12 组配对录像",
    "traceTitle": "开抽屉时，控制器做了什么？",
    "traceBody": "在仓库示例的一次复跑中，第 72 个控制周期发生接触丢失，第 78 个周期开始恢复，第 121 个周期回到模型支持的进度位置。135 个周期后，RLBench 判定任务完成。",
    "traceLink": "查看完整时间线 ↗",
    "traceAlt": "任务状态指令、控制器模式和物理接触随控制周期的变化",
    "reproduce": "复现开抽屉任务 ↗",
    "relation_loss": "接触丢失",
    "missed_interaction": "交互未建立",
    "actuation_delay": "动作执行延迟",
    "coordination_delay": "双臂配合延迟",
    "environment_change": "场景变化",
    "tasks": {
      "open_drawer": {
        "name": "打开抽屉",
        "description": "拉动时夹爪与抽屉脱离。留意右侧夹爪如何回到把手，再把抽屉拉开。"
      },
      "insert_square_peg": {
        "name": "方环套柱",
        "description": "第一次抓取错过了方环。观察夹爪移向目标柱时，有没有带上方环。"
      },
      "handover_item": {
        "name": "双臂交接物体",
        "description": "交接过程中抓取受到扰动。两臂分开时，观察物体留在哪只手中。"
      },
      "stack_cups": {
        "name": "叠放杯子",
        "description": "叠杯过程中接触中断。观察松脱后的杯子，以及最后叠成的杯摞。"
      },
      "close_jar": {
        "name": "盖上罐盖",
        "description": "取罐盖时抓取落空。观察罐盖能否从桌面移到目标罐口。"
      },
      "place_cups_3": {
        "name": "挂起三个杯子",
        "description": "挂杯过程中出现动作延迟。分别数一数两侧最终挂上杯架的杯子。"
      },
      "remove_cups_2": {
        "name": "取下两个杯子",
        "description": "从杯架取杯时场景发生变化。观察两个杯子能否分别从挂钩移到桌上。"
      },
      "push_buttons_2": {
        "name": "按下两个按钮",
        "description": "按键序列开始后出现动作延迟。这一回合中，两种策略都完成了两次按键。"
      },
      "sweep_to_dustpan": {
        "name": "单臂清扫",
        "description": "清扫时接触中断。这一回合中，两种策略都未把碎屑全部扫进簸箕。"
      },
      "bimanual_lift_tray": {
        "name": "双臂抬托盘",
        "description": "双臂的动作因延迟而错开。观察抬起时托盘两端如何移动。"
      },
      "bimanual_put_bottle_in_fridge": {
        "name": "把瓶子放进冰箱",
        "description": "双臂操作时接触中断。观察瓶子相对冰箱门和搁板的位置。"
      },
      "bimanual_sweep_to_dustpan": {
        "name": "双臂配合清扫",
        "description": "清扫初期的一次交互未能建立。观察两臂如何让刷子与簸箕对齐。"
      }
    }
  },
  "zh-Hant": {
    "title": "LiteTwin · 機器人操作示範",
    "home": "LiteTwin 首頁",
    "language": "語言",
    "headline": "當夾爪鬆脫，或動作慢了一拍",
    "intro": "比較同一運動策略的兩次執行。每組都從相同場景開始，遇到相同擾動。右側加入 LiteTwin 的任務狀態回饋。",
    "choose": "選擇任務",
    "previous": "上一個任務",
    "next": "下一個任務",
    "episode": "回合",
    "motion": "運動策略",
    "baseView": "運動策略，正面與俯視畫面",
    "feedbackView": "運動策略加入 LiteTwin，正面與俯視畫面",
    "success": "已完成",
    "failure": "未完成",
    "file": "開啟 MP4 ↗",
    "play": "播放對照",
    "pause": "暫停播放",
    "replay": "重新播放",
    "seek": "調整兩段影片的播放進度",
    "loading": "載入中…",
    "error": "影片載入失敗，可單獨開啟 MP4",
    "dataError": "任務清單載入失敗，可單獨開啟 MP4",
    "interrupted": "播放中斷，可重試或開啟 MP4",
    "seeking": "跳轉中…",
    "finished": "播放完畢",
    "playing": "播放中",
    "paused": "已暫停",
    "ready": "就緒",
    "physics": "正面與俯視雙視角 · 按物理模擬步取樣，以 2 倍速播放。較短的影片結束後停在最後一格。",
    "control": "正面與俯視雙視角 · 按控制步記錄，以每秒 12 格重播。較短的影片結束後停在最後一格。",
    "explore": "瀏覽全部任務",
    "count": "12 組配對影片",
    "traceTitle": "打開抽屜時，控制器做了什麼？",
    "traceBody": "在倉庫範例的一次重跑中，第 72 個控制週期失去接觸，第 78 個週期開始恢復，第 121 個週期回到模型支持的進度位置。135 個週期後，RLBench 判定任務完成。",
    "traceLink": "檢視完整時間軸 ↗",
    "traceAlt": "任務狀態指令、控制器模式和物理接觸隨控制週期的變化",
    "reproduce": "重現打開抽屜的任務 ↗",
    "relation_loss": "失去接觸",
    "missed_interaction": "未能建立互動",
    "actuation_delay": "動作執行延遲",
    "coordination_delay": "雙臂協調延遲",
    "environment_change": "場景變化",
    "tasks": {
      "open_drawer": {
        "name": "打開抽屜",
        "description": "拉動時夾爪與抽屜脫離。留意右側夾爪如何回到把手，再把抽屜拉開。"
      },
      "insert_square_peg": {
        "name": "方環套柱",
        "description": "第一次抓取錯過了方環。觀察夾爪移向目標柱時，有沒有帶上方環。"
      },
      "handover_item": {
        "name": "雙臂交接物體",
        "description": "交接過程中抓取受到擾動。兩臂分開時，觀察物體留在哪隻手中。"
      },
      "stack_cups": {
        "name": "堆疊杯子",
        "description": "疊杯過程中接觸中斷。觀察鬆脫後的杯子，以及最後堆疊的結果。"
      },
      "close_jar": {
        "name": "蓋上罐蓋",
        "description": "取罐蓋時抓取落空。觀察罐蓋能否從桌面移到目標罐口。"
      },
      "place_cups_3": {
        "name": "掛起三個杯子",
        "description": "掛杯過程中出現動作延遲。分別數一數兩側最後掛上杯架的杯子。"
      },
      "remove_cups_2": {
        "name": "取下兩個杯子",
        "description": "從杯架取杯時場景發生變化。觀察兩個杯子能否分別從掛鉤移到桌上。"
      },
      "push_buttons_2": {
        "name": "按下兩個按鈕",
        "description": "按鍵序列開始後出現動作延遲。這一回合中，兩種策略都完成了兩次按鍵。"
      },
      "sweep_to_dustpan": {
        "name": "單臂清掃",
        "description": "清掃時接觸中斷。這一回合中，兩種策略都未將碎屑全部掃進畚箕。"
      },
      "bimanual_lift_tray": {
        "name": "雙臂抬托盤",
        "description": "雙臂的動作因延遲而錯開。觀察抬起時托盤兩端如何移動。"
      },
      "bimanual_put_bottle_in_fridge": {
        "name": "把瓶子放進冰箱",
        "description": "雙臂操作時接觸中斷。觀察瓶子相對冰箱門和層架的位置。"
      },
      "bimanual_sweep_to_dustpan": {
        "name": "雙臂協作清掃",
        "description": "清掃初期的一次互動未能建立。觀察兩臂如何讓刷子與畚箕對齊。"
      }
    }
  }
};
