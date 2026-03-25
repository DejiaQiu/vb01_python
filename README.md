# Elevator Monitor

电梯振动监控与诊断项目，覆盖三条链路：
- 在线监控链路：串口采集 -> 异常检测 -> 故障规则/模型融合 -> 风险预警 -> 告警落盘
- 离线训练链路：数据集构建 -> 故障/风险模型训练 -> 发布门槛检查
- 专项诊断链路：对单个或批量振动 CSV 执行规则算法并生成诊断报告

## 架构图
静态图（推荐直接查看）：

![Architecture Diagram](docs/architecture.png)

Mermaid 源图：

```mermaid
flowchart LR
    subgraph Device[现场设备层]
        VB01[VB01 传感器\n串口数据]
    end

    subgraph Online[在线监控链路 elevator_monitor.monitor.runtime]
        Reader[RealtimeVibrationReader\nrealtime_vibration.py]
        Filter[数据过滤\n非新帧/过期帧剔除]
        Detector[OnlineAnomalyDetector\npipeline.py]
        FaultEngine[FaultTypeEngine\nfault_types.py]
        Fusion[模型/规则融合\nmodel_inference.py\ngenerated_algorithm.py]
        Risk[OnlineRiskPredictor\nrisk_predictor.py]
        Alerting[告警编排\nmonitor/alerting.py]
    end

    subgraph Output[在线输出与状态]
        DataCSV[data/elevator_rt_live.csv]
        AlertCSV[data/elevator_alerts_live.csv]
        RailCSV[data/rail_wear_alerts_live.csv]
        Health[data/monitor_health.json]
        Profile[data/profiles/<elevator_id>.json]
        Logs[logs/realtime_monitor.log]
    end

    subgraph Offline[离线训练与发布 elevator_monitor.training]
        Labels[labels CSV]
        Prep[prepare_dataset.py]
        TrainFault[train_fault_model.py]
        TrainRisk[train_risk_model.py]
        GenAlgo[generate_fault_algorithm.py]
        Gate[release_gate.py]
        Manifest[build_model_manifest.py]
        Models[data/models/*.json]
    end

    subgraph Report[专项诊断工具 report/]
        FaultAlgos[report/fault_algorithms/*.py]
        Reports[诊断报告 *.md]
    end

    VB01 --> Reader --> Filter --> Detector --> FaultEngine --> Fusion --> Risk --> Alerting
    Filter --> DataCSV
    Alerting --> AlertCSV
    Alerting --> RailCSV
    Risk --> Health
    Detector --> Profile
    Online --> Logs

    DataCSV --> Prep
    Labels --> Prep
    Prep --> TrainFault
    Prep --> TrainRisk
    Prep --> GenAlgo
    TrainFault --> Gate
    TrainRisk --> Gate
    GenAlgo --> Gate
    Gate --> Manifest --> Models

    Models -.在线加载.-> Fusion
    Models -.在线加载.-> Risk

    DataCSV --> FaultAlgos
    FaultAlgos --> Reports
```

如果当前预览器不支持 Mermaid，可看下面这份纯文本架构图：

```text
[VB01 传感器/串口]
        |
        v
[RealtimeVibrationReader]
        |
        v
[数据过滤: is_new_frame/data_age_ms]
        |
        +------------------------------> [data/elevator_rt_live.csv]
        |
        v
[OnlineAnomalyDetector]
        |
        v
[FaultTypeEngine]
        |
        v
[模型/规则融合: model_inference + generated_algorithm] <---- [data/models/*.json]
        |
        v
[OnlineRiskPredictor] <-------------------------------------- [data/models/*.json]
        |
        v
[Alerting]
   |            |                    |
   v            v                    v
[elevator_   [rail_wear_         [monitor_health.json]
alerts.csv]  alerts.csv]

离线训练链路:
[原始CSV + labels] -> [prepare_dataset] -> [train_fault/train_risk/generate_algo]
-> [release_gate] -> [build_model_manifest] -> [data/models/*.json] -> (在线加载)

专项诊断链路:
[振动CSV] -> [report/fault_algorithms/*.py] -> [诊断报告 *.md]
```

常见原因：IDE 的 Markdown 预览未启用 Mermaid 渲染（不是文档内容丢失）。

## 核心能力
### 在线监控（生产可运行）
- 串口实时采集（VB01），支持断线自动重连、无数据超时重连、首帧超时保护
- 数据有效性过滤（`is_new_frame`、`data_age_ms`）
- 在线异常检测（滑动基线 + z-score）
- 规则故障识别（如 `sensor_missing`、`signal_frozen`、`impact_shock`、`rail_wear_*`、`temperature_*`）
- 24h 风险预测（支持 predictive-only 告警）
- 模型融合（监督模型 + 生成算法，置信度不足自动回退规则）

### 离线训练与上线闭环
- 原始数据 + 标签构建训练集
- 故障分类模型训练、风险模型训练、少样本故障算法生成
- Release Gate 校验（准确率/召回/样本支撑）
- Manifest 版本清单

### 专项诊断
- `report/fault_algorithms` 当前总控默认先做“相对健康基线的异常筛查”，异常后再做保守的 `fault_detectors` 归因
- `fault_detectors` 里的“经验模板”不是训练模型，而是一组人工设定的特征方向/区间弱先验；当前归因更依赖“相对本梯健康基线的偏移”
- 可对单个 CSV 或批量 CSV 进行离线诊断
- 输出保持保守，只给 `normal`、`watch_only`、`candidate_faults`

## 目录结构（重点）
```text
.
├── elevator_monitor/
│   ├── api/                     # FastAPI 后端（main / routers / schemas）
│   ├── monitor/                 # 在线运行时（args/constants/pipeline/runtime/alerting）
│   ├── training/                # 离线训练、评估、发布工具
│   ├── api_service.py           # FastAPI 兼容入口（转发到 api/）
│   ├── realtime_monitor.py      # 在线监控入口（兼容入口）
│   └── realtime_vibration.py    # 实时振动读取 SDK + CLI
├── report/
│   ├── fault_algorithms/        # 健康基线异常门 + 保守 rope/rubber 归因
│   ├── wire_looseness_index.py  # 保留的历史松绳实验工具（可选）
│   └── *.md                     # 诊断报告
├── deploy/
│   ├── docker-compose.monitor.yml
│   └── docker.monitor.env.example
├── data/
├── logs/
└── tests/
```

## 运行环境
- Python 3.8+
- Linux 串口访问权限（如 `/dev/ttyUSB0`）
- 依赖：

```bash
pip install -r requirements.txt
```

## 需求驱动开发
如果你希望先写需求，再让我按需求生成代码，可以使用项目内置的需求框架：

1. 填写 `requirements/feature_request.md`
2. 本地校验需求是否完整：

```bash
python -m elevator_monitor.feature_requirements requirements/feature_request.md
```

3. 然后直接告诉我“按 `requirements/feature_request.md` 实现”

项目根目录的 `AGENTS.md` 会约束后续实现流程：先校验需求，再按 `实现位置`、`验收标准`、`测试用例` 落代码和测试。

## 快速开始
### 1) 本地运行在线监控
```bash
python -m elevator_monitor.realtime_monitor \
  --elevator-id elevator-001 \
  --port /dev/ttyUSB0 \
  --baud 115200 \
  --addr 0x50 \
  --sample-hz 40 \
  --detect-hz 40 \
  --reg-count 13 \
  --output-data data/elevator_rt_live.csv \
  --output-alert data/elevator_alerts_live.csv \
  --output-rail-wear-alert data/rail_wear_alerts_live.csv \
  --health-path data/monitor_health.json \
  --log-file logs/realtime_monitor.log
```

### 2) 启动诊断 API 服务
```bash
python -m elevator_monitor.api.main \
  --host 0.0.0.0 \
  --port 8085
```

后台运行示例：
```bash
mkdir -p logs
nohup python -m elevator_monitor.api.main \
  --host 0.0.0.0 \
  --port 8085 > logs/api_service.log 2>&1 &
```

### 3) 运行定时批诊断（推荐的在线状态主链路）
```bash
python -m elevator_monitor.batch_diagnosis \
  --input-dir data/captures \
  --max-files 12 \
  --baseline-dir data/captures \
  --baseline-start-hhmm 1015 \
  --baseline-end-hhmm 1019 \
  --latest-json data/diagnosis/latest_status.json \
  --history-jsonl data/diagnosis/history.jsonl \
  --pretty
```

查询最近一次批诊断结果：
```bash
curl "http://127.0.0.1:8085/api/v1/diagnostics/latest-status?latest_json=data/diagnosis/latest_status.json"
```

### 4) 只做实时振动读取（CLI/SDK）
```bash
python -m elevator_monitor.realtime_vibration \
  --elevator-id elevator-001 \
  --port /dev/ttyUSB0 \
  --baud 115200 \
  --sample-hz 40 \
  --detect-hz 40 \
  --reg-count 13 \
  --limit 10
```

按固定 40Hz 输出（含 `is_new_frame` 标记）：
```bash
python -m elevator_monitor.realtime_vibration \
  --port /dev/ttyUSB0 \
  --baud 115200 \
  --emit-mode fixed \
  --emit-hz 40 \
  --detect-hz 40 \
  --reg-count 13 \
  --duration-s 30 \
  --output-csv data/vibration_fixed_40hz.csv \
  --format csv > /dev/null
```

SDK 示例：
```python
from elevator_monitor import RealtimeVibrationReader

with RealtimeVibrationReader(
    elevator_id="elevator-001",
    port="/dev/ttyUSB0",
    baud=115200,
    addr=0x50,
    sample_hz=40.0,
    detect_hz=40,
    reg_count=13,
) as reader:
    frame = reader.read_latest(wait_timeout_s=2.0)
    print(frame)
```

官方SDK兼容最小测试单元（用于先验证 Modbus 链路是否通）：
```bash
python -m elevator_monitor.integrations.vb01_sdk_minimal \
  --port /dev/ttyUSB0 \
  --baud 115200 \
  --addr 0x50 \
  --reg-addr 0x34 \
  --reg-count 19 \
  --sample-hz 5 \
  --startup-timeout-s 10 \
  --duration-s 20 \
  --pretty
```

### 5) 单个 CSV 离线诊断
```bash
python report/fault_algorithms/run_all.py \
  --input data/captures/vibration_30s_20260303_104608.csv \
  --baseline-dir data/captures \
  --baseline-start-hhmm 1015 \
  --baseline-end-hhmm 1019 \
  --pretty
```

返回 `ok=true` 说明链路可读；`startup_timeout` 说明当前没有收到首帧。

### 3) 边缘部署
边缘部署建议运行在现场网关/工控机上，负责串口采集、实时分析、本地落盘和可选的边云同步。

本地直接运行：
```bash
python -m elevator_monitor.realtime_monitor \
  --elevator-id elevator-001 \
  --port /dev/ttyUSB0 \
  --baud 115200 \
  --addr 0x50 \
  --sample-hz 40 \
  --detect-hz 40 \
  --reg-count 13 \
  --output-data data/elevator_rt_live.csv \
  --output-alert data/elevator_alerts_live.csv \
  --output-rail-wear-alert data/rail_wear_alerts_live.csv \
  --health-path data/monitor_health.json \
  --log-file logs/realtime_monitor.log
```

Docker Compose 运行：
```bash
cp deploy/docker.monitor.env.example deploy/docker.monitor.env
# 根据现场修改 deploy/docker.monitor.env

docker compose \
  --env-file deploy/docker.monitor.env \
  -f deploy/docker-compose.edge.yml \
  up -d --build
```

常用命令：
```bash
docker compose -f deploy/docker-compose.edge.yml logs -f
docker compose -f deploy/docker-compose.edge.yml ps
docker compose -f deploy/docker-compose.edge.yml down
```

兼容说明：
- 旧文件 `deploy/docker-compose.monitor.yml` 仍保留，作用等同于边缘部署 compose。
- 边缘端如果需要主动上报到云端，可配置 `MONITOR_EDGE_SYNC_*` 系列环境变量。

### 4) 云端部署
云端部署建议运行在公司服务器或中心平台上，负责接收边缘事件、提供查询 API、生成报告上下文，并供 Dify / 工单系统调用。

本地直接运行：
```bash
nohup python -m elevator_monitor.api.main --host 0.0.0.0 --port 8085 > logs/api_service.log 2>&1 &
```

Docker Compose 运行：
```bash
docker compose -f deploy/docker-compose.api.yml up -d --build
```

常用命令：
```bash
docker compose -f deploy/docker-compose.api.yml logs -f
docker compose -f deploy/docker-compose.api.yml ps
docker compose -f deploy/docker-compose.api.yml down
```

如果需要限制边缘写入权限，可在云端设置：
```bash
export MONITOR_INGEST_SHARED_TOKEN=change-me
```

核心接口：
- `GET /api/v1/health/monitor`：读取监控健康状态并校验时效
- `POST /api/v1/ingest/heartbeat`：边缘设备上报健康状态与最新运行摘要
- `POST /api/v1/ingest/alert`：边缘设备上报告警事件
- `POST /api/v1/ingest/context`：边缘设备上传告警上下文证据片段
- `GET /api/v1/elevators/{elevator_id}/latest-status`：查询某台电梯的边缘同步最新状态
- `GET /api/v1/elevators/{elevator_id}/alerts`：查询某台电梯最近的边缘同步告警列表
- `GET /api/v1/alerts/{event_id}`：查询单次告警事件详情
- `POST /api/v1/diagnostics/rule-engine`：执行 8 类规则诊断，支持 `rows`、`csv_text` 或 `csv_path`
- `POST /api/v1/workflows/maintenance-package`：生成维保工单包，返回 `dify_inputs`
- `POST /api/v1/workflows/diagnosis-report`：聚合“诊断结果 + 维保包”并输出 `dify_report_inputs`（供 Dify 生成报告）
- `GET/POST /api/v1/workflows/diagnosis-report-latest`：按电梯直接读取最新批诊断并返回完整报告上下文（Dify 推荐用 POST JSON）
- `POST /api/v1/workflows/diagnosis-report-by-event`：按边缘同步事件生成报告上下文与 Dify 输入

Dify 接法：
- 直接用 HTTP Request 节点调用上述接口即可，不要求每个算法单独做一个脚本入口
- 更合理的方式是“算法服务统一对外暴露 JSON API”，由 Dify 只负责编排、通知、知识库和工单流转
- 在线主链路建议优先读取 `diagnosis-report-latest`，直接按电梯生成最新报告；`latest-status` / `alerts` / `diagnosis-report-by-event` 作为补充接口保留
- 上传 CSV 更适合保留为调试和离线验证入口，不建议继续作为默认 Dify 交互方式
- 如果要由监控服务主动触发 Dify，再去调用 Dify 的 Workflow API；这时可以是“主动调用 API”，不要求你一定做 webhook
- 标准节点输入输出设计见 `docs/dify_workflow_design.md`

## 离线训练与发布
### 1) 准备标签
参考 `examples/labels_template.csv`，至少包含：
- `elevator_id`
- `start_ts_ms`
- `end_ts_ms`（可选）
- `fault_type`
- `confirmed`

### 2) 构建训练集
```bash
python -m elevator_monitor.training.prepare_dataset \
  --data-glob "data/*.csv" \
  --label-csv examples/labels_template.csv \
  --output data/train_dataset.csv \
  --window-s 10 \
  --step-s 5 \
  --horizon-s 86400 \
  --min-samples 20
```

说明：
- 训练集会自动写出 `source_file` 列，后续训练默认按采集文件分组切分训练/验证集，避免同一份采样窗口同时落入两侧导致验证指标虚高。

### 3) 训练故障模型
```bash
python -m elevator_monitor.training.train_fault_model \
  --dataset-csv data/train_dataset.csv \
  --output-model data/models/fault_model.json \
  --min-class-samples 8 \
  --normal-max-ratio 5
```

### 4) 训练风险模型
```bash
python -m elevator_monitor.training.train_risk_model \
  --dataset-csv data/train_dataset.csv \
  --output-model data/models/risk_model.json \
  --min-class-samples 20 \
  --negative-max-ratio 4
```

### 5) 生成少样本故障算法（可选）
```bash
python -m elevator_monitor.training.generate_fault_algorithm \
  --dataset-csv data/train_dataset.csv \
  --output-json data/models/generated_fault_algo.json \
  --target-column target_fault_type \
  --normal-label normal
```

### 6) Release Gate（上线前）
```bash
python -m elevator_monitor.training.release_gate \
  --model-json data/models/fault_model.json \
  --expected-task fault_type \
  --min-accuracy 0.70 \
  --min-macro-f1 0.65 \
  --min-support 100
```

### 7) 构建模型 Manifest
```bash
python -m elevator_monitor.training.build_model_manifest \
  --model-json data/models/fault_model.json \
  --model-json data/models/risk_model.json \
  --output data/models/manifest.json \
  --project elevator-monitor \
  --environment prod \
  --created-by ops
```

### 8) 生成维保闭环工单包（Dify/运维编排输入）
```bash
python -m elevator_monitor.maintenance_workflow \
  --alert-csv data/elevator_alerts_live.csv \
  --health-json data/monitor_health.json \
  --manifest-json data/models/manifest.json \
  --site-name "Building-A" \
  --output-json data/workflow/maintenance_package.json \
  --output-md data/workflow/maintenance_package.md
```

输出结果包含：
- 面向值班/维保的优先级、处置模式、推荐动作、备件建议
- `dify_inputs` 字段，可直接作为 Dify Workflow 的结构化入参
- Markdown 版值班报告，便于钉钉/企业微信/工单系统直接引用

## 专项诊断（report/fault_algorithms）
单文件异常筛查：
```bash
python3 report/fault_algorithms/run_all.py \
  --input report/vibration_30s_20260303_110622.csv --pretty
```

带健康基线的筛查：
```bash
python3 report/fault_algorithms/run_all.py \
  --input report/vibration_30s_20260303_110622.csv \
  --baseline-dir data/baselines/elevator_002/healthy \
  --pretty
```

## 在线输出文件
- `data/elevator_rt_live.csv`：原始实时数据
- `data/elevator_alerts_live.csv`：告警结果（含模型调试字段）
- `data/rail_wear_alerts_live.csv`：导轨磨损趋势 8 列结果
- `data/monitor_health.json`：运行健康状态
- `data/alert_context/*.csv`：告警前上下文切片
- `data/workflow/*.json|*.md`：维保闭环工单包（可选，由 `maintenance_workflow` 生成）
- `data/profiles/{elevator_id}.json`：分梯画像（基线持久化）
- `logs/realtime_monitor.log`：服务日志

## 关键环境变量（常用）
完整变量见 `deploy/docker.monitor.env.example`，常用项：
- 串口与采样：`MONITOR_PORT`、`MONITOR_BAUD`、`MONITOR_ADDR`、`MONITOR_SAMPLE_HZ`
- 40Hz 链路建议：`MONITOR_DETECT_HZ=40`、`MONITOR_REG_COUNT=13`（按需再扩到 19）、
  `MONITOR_REG_ADDR=0x34`
- 输出路径：`MONITOR_OUTPUT_DATA`、`MONITOR_OUTPUT_ALERT`、`MONITOR_OUTPUT_RAIL_WEAR`
- 规则阈值：`MONITOR_FAULT_VIBRATION_WARNING_Z`、`MONITOR_FAULT_VIBRATION_SHOCK_Z`
- 融合模式：`MONITOR_FAULT_FUSION_MODE=rule_primary|model_primary`
- 模型路径：`MONITOR_FAULT_MODEL_PATH`、`MONITOR_GENERATED_ALGO_PATH`、`MONITOR_RISK_MODEL_PATH`
- 风险预测：`MONITOR_RISK_ENABLED`、`MONITOR_RISK_EMIT_ON_NORMAL`、`MONITOR_RISK_EMIT_MIN_LEVEL`
- 边缘到云端同步：`MONITOR_EDGE_SYNC_ENABLED`、`MONITOR_EDGE_SYNC_BASE_URL`、`MONITOR_EDGE_SYNC_API_TOKEN`、
  `MONITOR_EDGE_SYNC_QUEUE_PATH`、`MONITOR_EDGE_SYNC_HEARTBEAT_EVERY_S`
- Dify主动回调：`MONITOR_DIFY_ENABLED`、`MONITOR_DIFY_BASE_URL`、`MONITOR_DIFY_API_KEY`、`MONITOR_DIFY_MIN_LEVEL`

## 数据接入最小字段（40Hz）
必需字段：
- `elevator_id, ts_ms, ts, Ax, Ay, Az, Gx, Gy, Gz, t`

可选字段（用于增强特征）：
- `vx, vy, vz, ax, ay, az, sx, sy, sz, fx, fy, fz`

## 回归检查
```bash
python check_project.py
```

## 当前能力边界
- 已具备：异常检测、风险预警、规则故障识别、轻量模型融合、上线闭环工具
- 尚不具备：无标签条件下的高可信部件级精细分类
- 建议：先在线采数 + 标签闭环，再持续迭代模型与阈值
