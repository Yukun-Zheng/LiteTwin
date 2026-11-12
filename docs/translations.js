const translations = {
  "en": {
    "title": "LiteTwin · Robot manipulation demos",
    "home": "LiteTwin home",
    "language": "Language",
    "headline": "The arm moved.",
    "intro": "LiteTwin tracks what happens to the object. When a gripper misses or loses contact, the motion policy can recover its grasp and carry on.",
    "choose": "Choose a task",
    "previous": "Previous task",
    "next": "Next task",
    "episode": "Episode",
    "motion": "Motion policy",
    "baseView": "Motion policy, front and overhead views",
    "feedbackView": "Motion policy with LiteTwin, front and overhead views",
    "success": "Completed",
    "failure": "Incomplete",
    "file": "Open MP4",
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
    "count": "12 tasks · 13 recorded pairs",
    "traceTitle": "Recovering the drawer handle",
    "traceBody": "During a fresh Open Drawer run, LiteTwin checks contact with the handle before advancing the pull. The trace follows the controller cycle by cycle until the simulator reports success at cycle 135.",
    "traceLink": "Open the trace",
    "traceAlt": "Commanded task state, controller mode and physical contact by control cycle",
    "reproduce": "Run Open Drawer",
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
        "description": "Lift the tray and the cube together while one arm is delayed. Watch the two handles as the lift begins."
      },
      "bimanual_put_bottle_in_fridge": {
        "name": "Put Bottle in Fridge",
        "description": "Contact breaks during the two-arm task. Track the bottle relative to the fridge door and shelf."
      },
      "bimanual_sweep_to_dustpan": {
        "name": "Sweep with Two Arms",
        "description": "An early interaction is missed. Watch the brush and dustpan meet as the two arms coordinate."
      },
      "open_drawer_delay": {
        "name": "Open Drawer · Delayed Action",
        "description": "Watch the top drawer as the grippers pull away from the cabinet."
      }
    },
    "featured": "Featured comparisons",
    "annotated": "Annotated MP4",
    "jump": "Jump to divergence",
    "featuredCapture": "Front camera · same playback clock, full runs at 12 fps. Each original MP4 also includes the overhead view.",
    "frontBase": "Motion policy, front view",
    "frontFeedback": "Motion policy with LiteTwin, front view",
    "navDemo": "Comparisons",
    "navLibrary": "Task library",
    "navMethod": "How it works",
    "heroKicker": "Task-state feedback",
    "heroSecond": "Did the object?",
    "watch": "Play the full comparison",
    "browse": "Browse tasks",
    "navigation": "Navigation",
    "menuOpen": "Open navigation",
    "menuClose": "Close navigation",
    "skip": "Skip to demos",
    "compareKicker": "Featured recordings",
    "compareTitle": "Compare the runs",
    "compareIntro": "Both runs start with the same scene, seed and disturbance. Follow the object to see where the outcomes diverge.",
    "viewFront": "Show front only",
    "viewBoth": "Show both cameras",
    "frontCapture": "Front camera",
    "expand": "Expand player",
    "collapse": "Exit expanded view",
    "libraryIntro": "Explore 13 paired recordings across 12 tasks. Choose a disturbance or search by task name.",
    "filter": "Filter by disturbance",
    "all": "All recordings",
    "delay": "Delays",
    "search": "Find a task",
    "noMatches": "No recordings match your search.",
    "reset": "Clear filters",
    "results": "{n} / {total} recordings",
    "methodKicker": "Controller trace",
    "top": "Back to top",
    "featureNames": {
      "bimanual_lift_tray": "Lift the tray",
      "open_drawer_delay": "Open the drawer",
      "close_jar": "Close the jar"
    },
    "heroCapture": "Simulation replay",
    "studyName": "Lifting the loaded tray",
    "studyCamera": "Front camera",
    "studyStep": "Choose a moment",
    "stageApproach": "Approach",
    "stageLift": "Lift",
    "stageResult": "Result",
    "studyNotes": [
      "The grippers approach opposite ends of the same tray.",
      "The tray tilts on the left. On the right, both handles stay level.",
      "The left tray tips. On the right, the cube rises with the tray."
    ],
    "bothCameraLabel": "Front and overhead views",
    "traceLoss": "Contact lost",
    "traceRecover": "Recovery begins",
    "traceResume": "Progress resumes"
  },
  "zh-Hans": {
    "title": "LiteTwin · 机器人操作演示",
    "home": "LiteTwin 首页",
    "language": "语言",
    "headline": "机械臂动了，",
    "intro": "LiteTwin 跟踪夹爪与物体的关系。抓取落空或接触中断时，运动策略可以重新抓稳物体，继续完成任务。",
    "choose": "选择任务",
    "previous": "上一个任务",
    "next": "下一个任务",
    "episode": "回合",
    "motion": "运动策略",
    "baseView": "运动策略，正面和俯视画面",
    "feedbackView": "运动策略加入 LiteTwin，正面和俯视画面",
    "success": "已完成",
    "failure": "未完成",
    "file": "打开 MP4",
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
    "count": "12 个任务 · 13 组配对录像",
    "traceTitle": "夹爪怎样重新抓住把手？",
    "traceBody": "在一次开抽屉复跑中，LiteTwin 检查夹爪是否仍与把手接触，再决定何时继续拉动。时间线按控制周期记录了这次恢复，仿真器在第 135 个周期判定任务完成。",
    "traceLink": "查看完整时间线",
    "traceAlt": "任务状态指令、控制器模式和物理接触随控制周期的变化",
    "reproduce": "复现开抽屉任务",
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
        "description": "一条机械臂出现延迟。任务要求把托盘和方块一起抬起，留意起升时两个把手的高度。"
      },
      "bimanual_put_bottle_in_fridge": {
        "name": "把瓶子放进冰箱",
        "description": "双臂操作时接触中断。观察瓶子相对冰箱门和搁板的位置。"
      },
      "bimanual_sweep_to_dustpan": {
        "name": "双臂配合清扫",
        "description": "清扫初期的一次交互未能建立。观察两臂如何让刷子与簸箕对齐。"
      },
      "open_drawer_delay": {
        "name": "打开抽屉 · 动作延迟",
        "description": "观察夹爪移开时，最上层抽屉有没有跟着移动。"
      }
    },
    "featured": "精选对照",
    "annotated": "带注释的 MP4",
    "jump": "跳到关键动作",
    "featuredCapture": "正面视角 · 两侧使用同一时间轴，完整回放为每秒 12 帧。单独打开原始 MP4 还可看俯视画面。",
    "frontBase": "运动策略，正面视角",
    "frontFeedback": "加入 LiteTwin 的运动策略，正面视角",
    "navDemo": "演示对照",
    "navLibrary": "任务库",
    "navMethod": "工作原理",
    "heroKicker": "机器人操作 · 任务状态反馈",
    "heroSecond": "物体呢？",
    "watch": "播放完整对照",
    "browse": "浏览任务库",
    "navigation": "页面导航",
    "menuOpen": "打开导航",
    "menuClose": "关闭导航",
    "skip": "跳到演示区",
    "compareKicker": "精选仿真录像",
    "compareTitle": "并排看演示",
    "compareIntro": "两侧使用相同的场景、随机种子和扰动。跟着物体看，找到两组结果开始分岔的那一刻。",
    "viewFront": "只看正面",
    "viewBoth": "查看正面和俯视",
    "frontCapture": "正面视角",
    "expand": "放大播放器",
    "collapse": "退出放大",
    "libraryIntro": "12 项任务，13 组配对录像。可以按扰动筛选，也可以搜索任务名称。",
    "filter": "按扰动筛选",
    "all": "全部录像",
    "delay": "动作延迟",
    "search": "搜索任务",
    "noMatches": "没有找到匹配的录像。",
    "reset": "清除筛选",
    "results": "显示 {n} / {total} 组录像",
    "methodKicker": "控制器时间线",
    "top": "回到顶部",
    "featureNames": {
      "bimanual_lift_tray": "双臂抬托盘",
      "open_drawer_delay": "打开抽屉",
      "close_jar": "盖上罐盖"
    },
    "heroCapture": "仿真回放",
    "studyName": "双臂抬起载物托盘",
    "studyCamera": "正面视角",
    "studyStep": "选择观察时刻",
    "stageApproach": "接近",
    "stageLift": "起升",
    "stageResult": "结果",
    "studyNotes": [
      "两侧夹爪分别向托盘两端靠近。",
      "左侧托盘一端先抬起。右侧两个把手保持水平。",
      "左侧托盘翻倒。右侧托盘平稳抬起，方块仍在上面。"
    ],
    "bothCameraLabel": "正面与俯视双视角",
    "traceLoss": "接触丢失",
    "traceRecover": "开始恢复",
    "traceResume": "恢复推进"
  },
  "zh-Hant": {
    "title": "LiteTwin · 機器人操作示範",
    "home": "LiteTwin 首頁",
    "language": "語言",
    "headline": "機械臂動了，",
    "intro": "LiteTwin 追蹤夾爪與物體的關係。抓取落空或接觸中斷時，運動策略可以重新抓穩物體，繼續完成任務。",
    "choose": "選擇任務",
    "previous": "上一個任務",
    "next": "下一個任務",
    "episode": "回合",
    "motion": "運動策略",
    "baseView": "運動策略，正面與俯視畫面",
    "feedbackView": "運動策略加入 LiteTwin，正面與俯視畫面",
    "success": "已完成",
    "failure": "未完成",
    "file": "開啟 MP4",
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
    "count": "12 個任務 · 13 組配對影片",
    "traceTitle": "夾爪如何重新抓住把手？",
    "traceBody": "在一次開抽屜的重跑中，LiteTwin 檢查夾爪是否仍與把手接觸，再決定何時繼續拉動。時間線依控制週期記錄這次恢復，模擬器在第 135 個週期判定任務完成。",
    "traceLink": "查看完整時間線",
    "traceAlt": "任務狀態指令、控制器模式和物理接觸隨控制週期的變化",
    "reproduce": "重現開抽屜任務",
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
        "description": "一隻機械臂出現延遲。任務要求把托盤與方塊一起抬起，留意升起時兩個把手的高度。"
      },
      "bimanual_put_bottle_in_fridge": {
        "name": "把瓶子放進冰箱",
        "description": "雙臂操作時接觸中斷。觀察瓶子相對冰箱門和層架的位置。"
      },
      "bimanual_sweep_to_dustpan": {
        "name": "雙臂協作清掃",
        "description": "清掃初期的一次互動未能建立。觀察兩臂如何讓刷子與畚箕對齊。"
      },
      "open_drawer_delay": {
        "name": "打開抽屜 · 動作延遲",
        "description": "觀察夾爪移開時，最上層抽屜有沒有跟著移動。"
      }
    },
    "featured": "精選對照",
    "annotated": "附註解的 MP4",
    "jump": "跳至關鍵動作",
    "featuredCapture": "正面視角 · 兩側使用同一時間軸，完整播放為每秒 12 格。單獨開啟原始 MP4 亦可觀看俯視畫面。",
    "frontBase": "運動策略，正面視角",
    "frontFeedback": "加入 LiteTwin 的運動策略，正面視角",
    "navDemo": "示範對照",
    "navLibrary": "任務庫",
    "navMethod": "運作原理",
    "heroKicker": "機器人操作 · 任務狀態回饋",
    "heroSecond": "物體呢？",
    "watch": "播放完整對照",
    "browse": "瀏覽任務庫",
    "navigation": "頁面導覽",
    "menuOpen": "開啟導覽",
    "menuClose": "關閉導覽",
    "skip": "跳至示範區",
    "compareKicker": "精選模擬影片",
    "compareTitle": "並排看示範",
    "compareIntro": "兩側採用相同的場景、隨機種子與擾動。跟著物體看，找出兩組結果開始分歧的那一刻。",
    "viewFront": "只看正面",
    "viewBoth": "查看正面與俯視",
    "frontCapture": "正面視角",
    "expand": "放大播放器",
    "collapse": "離開放大檢視",
    "libraryIntro": "12 項任務，13 組配對影片。可按擾動篩選，也可搜尋任務名稱。",
    "filter": "按擾動篩選",
    "all": "全部影片",
    "delay": "動作延遲",
    "search": "搜尋任務",
    "noMatches": "沒有找到符合條件的影片。",
    "reset": "清除篩選",
    "results": "顯示 {n} / {total} 組影片",
    "methodKicker": "控制器時間線",
    "top": "回到頂部",
    "featureNames": {
      "bimanual_lift_tray": "雙臂抬托盤",
      "open_drawer_delay": "打開抽屜",
      "close_jar": "蓋上罐蓋"
    },
    "heroCapture": "模擬回放",
    "studyName": "雙臂抬起載物托盤",
    "studyCamera": "正面視角",
    "studyStep": "選擇觀察時刻",
    "stageApproach": "接近",
    "stageLift": "抬升",
    "stageResult": "結果",
    "studyNotes": [
      "兩側夾爪分別向托盤兩端靠近。",
      "左側托盤一端先抬起。右側兩個把手保持水平。",
      "左側托盤翻倒。右側托盤平穩抬起，方塊仍在上面。"
    ],
    "bothCameraLabel": "正面與俯視雙視角",
    "traceLoss": "接觸中斷",
    "traceRecover": "開始恢復",
    "traceResume": "恢復推進"
  }
};
