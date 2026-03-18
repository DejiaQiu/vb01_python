# 电梯振动规则算法

输入字段（你现有数据可直接用）：

`ts_ms,data_ts_ms,data_age_ms,is_new_frame,Ax,Ay,Az,Gx,Gy,Gz,vx,vy,vz,ax,ay,az,t,sx,sy,sz,fx,fy,fz,A_mag,G_mag`

当前总控 `run_all.py` 只启用两类算法，其它规则脚本仍保留在目录中，但默认不参与调度。

## 当前启用的 2 类故障与主要振动特征

1. `rope_looseness`（钢丝绳松动）
- 关键特征：`lateral_ratio`、`ag_corr`、`zc_rate_hz`
- 判定方式：优先对健康基线做 `median + MAD` 的 robust 归一化；缺少基线时回退到无量纲自归一化评分

2. `rubber_hardening`（橡胶圈硬化）
- 关键特征：`az_std`、`az_p2p`、`az_cv`、`az_jerk_rms`、`corr_xy`、`energy_z_over_xy`
- 判定方式：优先对健康基线做相对评分，重点观察曳引机本体安装传感器下的竖向响应增强、阻尼退化代理量和轴间耦合变化；缺少基线时只做保守 fallback 评分

## 文件清单

- `detect_rope_looseness.py`
- `detect_rubber_hardening.py`
- `run_all.py`（当前一次输出 2 类结果）
- `run_all.py` 会同时输出：
  - 原始排序结果 `results`
  - 高置信候选 `candidate_faults`
  - 观察候选 `watch_faults`
  - 筛查状态 `screening`

目录中其余 `detect_*.py` 仍可单独执行，但已不在默认总控里启用。

## 使用示例

单个算法：

```bash
python3 report/fault_algorithms/detect_impact_shock.py \
  --input report/vibration_30s_20260303_110622.csv --pretty
```

一次跑当前启用的 2 类：

```bash
python3 report/fault_algorithms/run_all.py \
  --input report/vibration_30s_20260303_110622.csv \
  --baseline-dir report \
  --baseline-start-hhmm 1017 \
  --baseline-end-hhmm 1018 \
  --pretty
```

钢丝绳松动时间序列确认（连续命中才报警，推荐）：

```bash
python3 report/fault_algorithms/rope_looseness_timeline.py \
  --input-dir report \
  --start-hhmm 1016 \
  --end-hhmm 1057 \
  --baseline-dir report \
  --baseline-start-hhmm 1017 \
  --baseline-end-hhmm 1018 \
  --min-score 60 \
  --confirm-windows 2 \
  --pretty
```

## 注意

- 脚本会优先使用 `is_new_frame=1` 的真实点，自动规避 fixed100 补点干扰。
- 当有效样本很少或有效采样率很低时，结果会自动降低 `quality_factor`。
- 钢丝绳松动算法在提供健康基线时，对整体幅值尺度变化更稳；换梯迁移时建议先给一小段健康样本建基线。
- 橡胶圈硬化算法更依赖单梯健康基线；如果传感器装在曳引机本体，优先比较竖向响应、耦合结构和阻尼代理量的变化，不建议只看总振动绝对值。
- 多故障总控优先输出高置信候选故障；没有高置信候选时，会保持 `normal` 或 `watch_only`，不把原始最高分直接当作维保结论。
- 这是规则算法，适合快速筛查，不等同于高采样频谱诊断。
