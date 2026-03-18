from __future__ import annotations

import argparse

from ..runtime_config import APP_TITLE, DEFAULT_DEVICE_NAME, env_bool, env_float, env_int, env_str, ts_csv_path


def _parse_int_auto(value: str) -> int:
    return int(value, 0)


def _default_data_path() -> str:
    return env_str("MONITOR_OUTPUT_DATA", ts_csv_path("elevator_rt"))


def _default_alert_path() -> str:
    return env_str("MONITOR_OUTPUT_ALERT", ts_csv_path("elevator_alerts"))


def _default_rail_wear_alert_path() -> str:
    return env_str("MONITOR_OUTPUT_RAIL_WEAR", ts_csv_path("rail_wear_alerts"))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{APP_TITLE} 常驻实时异常检测服务")

    parser.add_argument("--elevator-id", default=env_str("MONITOR_ELEVATOR_ID", "elevator-unknown"), help="电梯唯一ID")
    parser.add_argument("--device-name", default=env_str("MONITOR_DEVICE_NAME", DEFAULT_DEVICE_NAME), help="设备名称")
    parser.add_argument("--port", default=env_str("MONITOR_PORT", "/dev/ttyUSB0"), help="串口")
    parser.add_argument("--baud", type=int, default=env_int("MONITOR_BAUD", 115200), help="波特率")
    parser.add_argument("--addr", type=_parse_int_auto, default=env_int("MONITOR_ADDR", 0x50), help="设备地址(支持 0x 前缀)")

    parser.add_argument("--sample-hz", type=float, default=env_float("MONITOR_SAMPLE_HZ", 40.0), help="主循环采样频率")
    parser.add_argument("--detect-hz", type=int, default=env_int("MONITOR_DETECT_HZ", 40), help="设备检测周期(寄存器 0x65)")
    parser.add_argument(
        "--reg-addr",
        type=_parse_int_auto,
        default=env_int("MONITOR_REG_ADDR", 0x34),
        help="循环读取起始寄存器(支持 0x 前缀)",
    )
    parser.add_argument(
        "--reg-count",
        type=int,
        default=env_int("MONITOR_REG_COUNT", 13),
        help="循环读取寄存器数量（40Hz 默认推荐较小寄存器窗口）",
    )
    parser.add_argument("--no-set-detect-hz", action="store_true", help="不写设备检测周期")
    parser.add_argument("--startup-timeout-s", type=float, default=env_float("MONITOR_STARTUP_TIMEOUT_S", 3.0), help="连接后等待首帧超时")
    parser.add_argument("--max-data-age-ms", type=int, default=env_int("MONITOR_MAX_DATA_AGE_MS", 500), help="最大数据年龄阈值")

    parser.add_argument("--baseline-size", type=int, default=env_int("MONITOR_BASELINE_SIZE", 5000), help="在线基线样本最大缓存")
    parser.add_argument("--baseline-min-records", type=int, default=env_int("MONITOR_BASELINE_MIN_RECORDS", 300), help="构建基线最小样本数")
    parser.add_argument("--baseline-refresh-every", type=int, default=env_int("MONITOR_BASELINE_REFRESH_EVERY", 200), help="基线刷新间隔(帧)")

    parser.add_argument("--stale-limit", type=int, default=env_int("MONITOR_STALE_LIMIT", 300), help="连续相同值阈值")
    parser.add_argument("--warning-z", type=float, default=env_float("MONITOR_WARNING_Z", 3.5), help="warning 阈值")
    parser.add_argument("--anomaly-z", type=float, default=env_float("MONITOR_ANOMALY_Z", 6.0), help="anomaly 阈值")
    parser.add_argument("--alert-cooldown-s", type=float, default=env_float("MONITOR_ALERT_COOLDOWN_S", 2.0), help="同级告警最小间隔")

    parser.add_argument(
        "--fault-type-enabled",
        action=argparse.BooleanOptionalAction,
        default=env_bool("MONITOR_FAULT_TYPE_ENABLED", True),
        help="是否启用故障类型识别",
    )
    parser.add_argument(
        "--fault-type-min-level",
        choices=["warning", "anomaly"],
        default=env_str("MONITOR_FAULT_TYPE_MIN_LEVEL", "warning"),
        help="从哪个异常级别开始输出故障类型",
    )
    parser.add_argument(
        "--fault-type-top-k",
        type=int,
        default=env_int("MONITOR_FAULT_TYPE_TOP_K", 3),
        help="输出候选故障类型数量",
    )
    parser.add_argument(
        "--fault-baseline-size",
        type=int,
        default=env_int("MONITOR_FAULT_BASELINE_SIZE", 2000),
        help="故障类型算法基线样本上限",
    )
    parser.add_argument(
        "--fault-baseline-min-records",
        type=int,
        default=env_int("MONITOR_FAULT_BASELINE_MIN_RECORDS", 120),
        help="故障类型算法建基线最小样本",
    )
    parser.add_argument(
        "--fault-vibration-warning-z",
        type=float,
        default=env_float("MONITOR_FAULT_VIBRATION_WARNING_Z", 3.0),
        help="振动增大判定阈值（z）",
    )
    parser.add_argument(
        "--fault-vibration-shock-z",
        type=float,
        default=env_float("MONITOR_FAULT_VIBRATION_SHOCK_Z", 6.0),
        help="冲击判定阈值（z）",
    )
    parser.add_argument(
        "--fault-temp-rise-c",
        type=float,
        default=env_float("MONITOR_FAULT_TEMP_RISE_C", 6.0),
        help="温升判定阈值（摄氏度）",
    )
    parser.add_argument(
        "--fault-temp-overheat-c",
        type=float,
        default=env_float("MONITOR_FAULT_TEMP_OVERHEAT_C", 45.0),
        help="过温判定阈值（摄氏度）",
    )
    parser.add_argument(
        "--fault-model-path",
        default=env_str("MONITOR_FAULT_MODEL_PATH", ""),
        help="故障类型模型路径（JSON，可选）",
    )
    parser.add_argument(
        "--fault-model-min-confidence",
        type=float,
        default=env_float("MONITOR_FAULT_MODEL_MIN_CONFIDENCE", 0.60),
        help="故障类型模型最小置信度（低于该值回退规则）",
    )
    parser.add_argument(
        "--fault-model-top-k",
        type=int,
        default=env_int("MONITOR_FAULT_MODEL_TOP_K", 3),
        help="故障类型模型输出候选数量",
    )
    parser.add_argument(
        "--fault-fusion-mode",
        choices=["rule_primary", "model_primary"],
        default=env_str("MONITOR_FAULT_FUSION_MODE", "rule_primary"),
        help="故障融合模式：rule_primary=规则主判，model_primary=模型优先",
    )
    parser.add_argument(
        "--generated-algo-path",
        default=env_str("MONITOR_GENERATED_ALGO_PATH", ""),
        help="自动生成故障算法路径（JSON，可选）",
    )
    parser.add_argument(
        "--generated-algo-min-confidence",
        type=float,
        default=env_float("MONITOR_GENERATED_ALGO_MIN_CONFIDENCE", 0.62),
        help="自动生成算法最小置信度",
    )
    parser.add_argument(
        "--generated-algo-top-k",
        type=int,
        default=env_int("MONITOR_GENERATED_ALGO_TOP_K", 3),
        help="自动生成算法候选输出数量",
    )
    parser.add_argument(
        "--generated-algo-horizon-s",
        type=float,
        default=env_float("MONITOR_GENERATED_ALGO_HORIZON_S", 30.0),
        help="振动特征预测前瞻秒数",
    )
    parser.add_argument(
        "--generated-algo-forecast-min-points",
        type=int,
        default=env_int("MONITOR_GENERATED_ALGO_FORECAST_MIN_POINTS", 10),
        help="振动特征预测最小历史点数",
    )

    parser.add_argument(
        "--risk-enabled",
        action=argparse.BooleanOptionalAction,
        default=env_bool("MONITOR_RISK_ENABLED", True),
        help="是否启用故障提前预测",
    )
    parser.add_argument(
        "--risk-emit-on-normal",
        action=argparse.BooleanOptionalAction,
        default=env_bool("MONITOR_RISK_EMIT_ON_NORMAL", True),
        help="当前正常但未来风险高时是否提前告警",
    )
    parser.add_argument(
        "--risk-emit-min-level",
        choices=["watch", "high", "critical"],
        default=env_str("MONITOR_RISK_EMIT_MIN_LEVEL", "high"),
        help="提前告警的最小24h风险级别",
    )
    parser.add_argument(
        "--risk-baseline-size",
        type=int,
        default=env_int("MONITOR_RISK_BASELINE_SIZE", 3000),
        help="风险预测基线样本上限",
    )
    parser.add_argument(
        "--risk-baseline-min-records",
        type=int,
        default=env_int("MONITOR_RISK_BASELINE_MIN_RECORDS", 200),
        help="风险预测建基线最小样本",
    )
    parser.add_argument(
        "--risk-trend-window-s",
        type=float,
        default=env_float("MONITOR_RISK_TREND_WINDOW_S", 1800.0),
        help="劣化斜率计算窗口秒数",
    )
    parser.add_argument(
        "--risk-smooth-alpha",
        type=float,
        default=env_float("MONITOR_RISK_SMOOTH_ALPHA", 0.08),
        help="风险分数平滑系数",
    )
    parser.add_argument(
        "--risk-anomaly-scale",
        type=float,
        default=env_float("MONITOR_RISK_ANOMALY_SCALE", 8.0),
        help="异常分数映射到风险分数的缩放系数",
    )
    parser.add_argument(
        "--risk-fault-weight",
        type=float,
        default=env_float("MONITOR_RISK_FAULT_WEIGHT", 0.25),
        help="故障类型置信度在风险中的权重",
    )
    parser.add_argument(
        "--risk-vibration-weight",
        type=float,
        default=env_float("MONITOR_RISK_VIBRATION_WEIGHT", 0.20),
        help="振动变化在风险中的权重",
    )
    parser.add_argument(
        "--risk-temperature-weight",
        type=float,
        default=env_float("MONITOR_RISK_TEMPERATURE_WEIGHT", 0.10),
        help="温度变化在风险中的权重",
    )
    parser.add_argument(
        "--risk-model-path",
        default=env_str("MONITOR_RISK_MODEL_PATH", ""),
        help="24h风险模型路径（JSON，可选）",
    )
    parser.add_argument(
        "--risk-model-positive-label",
        default=env_str("MONITOR_RISK_MODEL_POSITIVE_LABEL", "1"),
        help="风险模型正类标签",
    )
    parser.add_argument(
        "--risk-model-weight",
        type=float,
        default=env_float("MONITOR_RISK_MODEL_WEIGHT", 0.35),
        help="风险模型概率在综合风险中的权重",
    )
    parser.add_argument(
        "--model-window-s",
        type=float,
        default=env_float("MONITOR_MODEL_WINDOW_S", 10.0),
        help="在线模型推理窗口秒数",
    )
    parser.add_argument(
        "--model-window-min-samples",
        type=int,
        default=env_int("MONITOR_MODEL_WINDOW_MIN_SAMPLES", 20),
        help="在线模型推理最小样本数",
    )
    parser.add_argument(
        "--dify-enabled",
        action=argparse.BooleanOptionalAction,
        default=env_bool("MONITOR_DIFY_ENABLED", False),
        help="是否在告警后主动调用 Dify Workflow API",
    )
    parser.add_argument(
        "--dify-base-url",
        default=env_str("MONITOR_DIFY_BASE_URL", ""),
        help="Dify API 基地址，例如 http://localhost/v1",
    )
    parser.add_argument(
        "--dify-api-key",
        default=env_str("MONITOR_DIFY_API_KEY", ""),
        help="Dify Workflow API Key",
    )
    parser.add_argument(
        "--dify-user",
        default=env_str("MONITOR_DIFY_USER", "elevator-monitor"),
        help="Dify 请求 user 字段",
    )
    parser.add_argument(
        "--dify-response-mode",
        choices=["blocking", "streaming"],
        default=env_str("MONITOR_DIFY_RESPONSE_MODE", "blocking"),
        help="Dify workflow response_mode",
    )
    parser.add_argument(
        "--dify-timeout-s",
        type=float,
        default=env_float("MONITOR_DIFY_TIMEOUT_S", 8.0),
        help="Dify 请求超时时间",
    )
    parser.add_argument(
        "--dify-verify-ssl",
        action=argparse.BooleanOptionalAction,
        default=env_bool("MONITOR_DIFY_VERIFY_SSL", True),
        help="是否校验 Dify HTTPS 证书",
    )
    parser.add_argument(
        "--dify-min-level",
        choices=["warning", "anomaly"],
        default=env_str("MONITOR_DIFY_MIN_LEVEL", "warning"),
        help="仅当告警级别不低于该值时触发 Dify",
    )
    parser.add_argument(
        "--dify-cooldown-s",
        type=float,
        default=env_float("MONITOR_DIFY_COOLDOWN_S", 30.0),
        help="两次 Dify 触发之间的最小间隔秒数",
    )
    parser.add_argument(
        "--dify-site-name",
        default=env_str("MONITOR_DIFY_SITE_NAME", ""),
        help="写入维保包的站点名称",
    )
    parser.add_argument(
        "--dify-manifest-json",
        default=env_str("MONITOR_DIFY_MANIFEST_JSON", ""),
        help="可选：模型 manifest 路径，用于补充 Dify 输入上下文",
    )
    parser.add_argument("--reconnect-backoff-s", type=float, default=env_float("MONITOR_RECONNECT_BACKOFF_S", 2.0), help="重连重试间隔")
    parser.add_argument("--reconnect-no-data-s", type=float, default=env_float("MONITOR_RECONNECT_NO_DATA_S", 8.0), help="超过该秒数无新数据则重连")

    parser.add_argument("--output-data", default=_default_data_path(), help="原始数据输出 CSV")
    parser.add_argument("--output-alert", default=_default_alert_path(), help="告警输出 CSV")
    parser.add_argument("--output-rail-wear-alert", default=_default_rail_wear_alert_path(), help="导轨磨损趋势输出 CSV")
    parser.add_argument(
        "--alert-context-enabled",
        action=argparse.BooleanOptionalAction,
        default=env_bool("MONITOR_ALERT_CONTEXT_ENABLED", True),
        help="告警时是否导出上下文原始数据切片 CSV",
    )
    parser.add_argument(
        "--alert-context-dir",
        default=env_str("MONITOR_ALERT_CONTEXT_DIR", "data/alert_context"),
        help="告警上下文 CSV 输出目录",
    )
    parser.add_argument(
        "--alert-context-pre-seconds",
        type=float,
        default=env_float("MONITOR_ALERT_CONTEXT_PRE_SECONDS", 30.0),
        help="导出告警前多少秒的原始数据",
    )
    parser.add_argument(
        "--alert-context-max-rows",
        type=int,
        default=env_int("MONITOR_ALERT_CONTEXT_MAX_ROWS", 6000),
        help="告警上下文内存缓存最大行数",
    )
    parser.add_argument(
        "--profile-path",
        default=env_str("MONITOR_PROFILE_PATH", "data/profiles/{elevator_id}.json"),
        help="分梯自适应画像文件路径，可包含 {elevator_id} 占位符",
    )
    parser.add_argument(
        "--profile-save-every-n",
        type=int,
        default=env_int("MONITOR_PROFILE_SAVE_EVERY_N", 300),
        help="每 N 条有效记录保存一次画像",
    )
    parser.add_argument(
        "--profile-max-items",
        type=int,
        default=env_int("MONITOR_PROFILE_MAX_ITEMS", 3000),
        help="画像中每类历史样本最多保留条数",
    )
    parser.add_argument(
        "--health-path",
        default=env_str("MONITOR_HEALTH_PATH", "data/monitor_health.json"),
        help="健康状态 JSON 路径",
    )
    parser.add_argument(
        "--edge-sync-enabled",
        action=argparse.BooleanOptionalAction,
        default=env_bool("MONITOR_EDGE_SYNC_ENABLED", False),
        help="是否启用边缘到云端的事件化上报",
    )
    parser.add_argument(
        "--edge-sync-base-url",
        default=env_str("MONITOR_EDGE_SYNC_BASE_URL", ""),
        help="云端接入 API 基地址，例如 http://server:8085",
    )
    parser.add_argument(
        "--edge-sync-api-token",
        default=env_str("MONITOR_EDGE_SYNC_API_TOKEN", ""),
        help="云端接入 API token（可选）",
    )
    parser.add_argument(
        "--edge-sync-site-id",
        default=env_str("MONITOR_EDGE_SYNC_SITE_ID", ""),
        help="站点 ID（可选）",
    )
    parser.add_argument(
        "--edge-sync-site-name",
        default=env_str("MONITOR_EDGE_SYNC_SITE_NAME", ""),
        help="站点名称（可选）",
    )
    parser.add_argument(
        "--edge-sync-device-id",
        default=env_str("MONITOR_EDGE_SYNC_DEVICE_ID", ""),
        help="边缘设备 ID，默认复用 elevator_id",
    )
    parser.add_argument(
        "--edge-sync-queue-path",
        default=env_str("MONITOR_EDGE_SYNC_QUEUE_PATH", "data/edge_sync_queue.sqlite3"),
        help="本地边缘补传队列 SQLite 路径",
    )
    parser.add_argument(
        "--edge-sync-heartbeat-every-s",
        type=float,
        default=env_float("MONITOR_EDGE_SYNC_HEARTBEAT_EVERY_S", 10.0),
        help="边缘心跳最小上报间隔秒数",
    )
    parser.add_argument(
        "--edge-sync-timeout-s",
        type=float,
        default=env_float("MONITOR_EDGE_SYNC_TIMEOUT_S", 5.0),
        help="边缘上报请求超时时间",
    )
    parser.add_argument(
        "--edge-sync-verify-ssl",
        action=argparse.BooleanOptionalAction,
        default=env_bool("MONITOR_EDGE_SYNC_VERIFY_SSL", True),
        help="是否校验云端接入 HTTPS 证书",
    )
    parser.add_argument(
        "--edge-sync-drain-every-s",
        type=float,
        default=env_float("MONITOR_EDGE_SYNC_DRAIN_EVERY_S", 2.0),
        help="边缘补传队列轮询发送间隔秒数",
    )
    parser.add_argument(
        "--edge-sync-drain-batch-size",
        type=int,
        default=env_int("MONITOR_EDGE_SYNC_DRAIN_BATCH_SIZE", 8),
        help="每轮最多发送多少条排队事件",
    )
    parser.add_argument(
        "--edge-sync-max-context-bytes",
        type=int,
        default=env_int("MONITOR_EDGE_SYNC_MAX_CONTEXT_BYTES", 2_000_000),
        help="告警上下文上传时允许的最大原始字节数",
    )

    parser.add_argument("--print-every-n", type=int, default=env_int("MONITOR_PRINT_EVERY_N", 50), help="每 N 条数据打印一次")
    parser.add_argument("--warn-every-n", type=int, default=env_int("MONITOR_WARN_EVERY_N", 500), help="每 N 次跳过打印一次")
    parser.add_argument("--flush-every-n", type=int, default=env_int("MONITOR_FLUSH_EVERY_N", 200), help="数据文件 flush 间隔")

    parser.add_argument("--health-every-s", type=float, default=env_float("MONITOR_HEALTH_EVERY_S", 2.0), help="健康文件刷新间隔")

    parser.add_argument("--log-file", default=env_str("MONITOR_LOG_FILE", "logs/realtime_monitor.log"), help="日志文件")
    parser.add_argument("--log-level", default=env_str("MONITOR_LOG_LEVEL", "INFO"), help="日志级别")
    parser.add_argument("--log-max-bytes", type=int, default=env_int("MONITOR_LOG_MAX_BYTES", 10 * 1024 * 1024), help="单日志文件最大字节")
    parser.add_argument("--log-backups", type=int, default=env_int("MONITOR_LOG_BACKUPS", 5), help="日志备份数")

    parser.add_argument("--run-seconds", type=float, default=None, help="测试用：运行指定秒数后退出")

    return parser


__all__ = ["build_arg_parser"]
