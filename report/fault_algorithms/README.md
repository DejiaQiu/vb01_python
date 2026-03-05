# 电梯振动 8 类故障规则算法

输入字段（你现有数据可直接用）：

`ts_ms,data_ts_ms,data_age_ms,is_new_frame,Ax,Ay,Az,Gx,Gy,Gz,vx,vy,vz,ax,ay,az,t,sx,sy,sz,fx,fy,fz,A_mag,G_mag`

## 8 类故障与主要振动特征

1. `mechanical_looseness`（机械松动）
- 关键特征：`a_std`、`a_crest`、`a_kurt`、`jerk_rms`

2. `impact_shock`（冲击撞击）
- 关键特征：`a_crest`、`peak_rate_hz`、`jerk_rms`、`g_p2p`

3. `guide_rail_wear`（导轨磨损）
- 关键特征：`lateral_ratio`、`a_rms_ac`、`zc_rate_hz`

4. `rope_looseness`（钢丝绳松动）
- 关键特征：`a_p2p`、`ag_corr`、`zc_rate_hz`

5. `traction_motor_bearing_wear`（曳引机轴承磨损）
- 关键特征：`g_std`、`jerk_rms`、`peak_rate_hz`、`a_crest`

6. `coupling_misalignment`（联轴器不对中）
- 关键特征：`gx_ax_corr`、`gy_ay_corr`、`lateral_ratio`

7. `brake_jitter`（制动器抖动）
- 关键特征：`g_p2p`、`zc_rate_hz`、`peak_rate_hz`、`sx/sy/sz` 波动

8. `car_imbalance`（轿厢偏载/不平衡）
- 关键特征：`lateral_ratio`、`jerk_rms`（低） 、`peak_rate_hz`（低）

## 文件清单

- `detect_mechanical_looseness.py`
- `detect_impact_shock.py`
- `detect_rail_wear.py`
- `detect_rope_looseness.py`
- `detect_bearing_wear.py`
- `detect_coupling_misalignment.py`
- `detect_brake_jitter.py`
- `detect_car_imbalance.py`
- `run_all.py`（一次输出 8 类结果）

## 使用示例

单个算法：

```bash
python3 report/fault_algorithms/detect_impact_shock.py \
  --input report/vibration_30s_20260303_110622.csv --pretty
```

一次跑 8 类：

```bash
python3 report/fault_algorithms/run_all.py \
  --input report/vibration_30s_20260303_110622.csv --pretty
```

钢丝绳松动时间序列确认（连续命中才报警，推荐）：

```bash
python3 report/fault_algorithms/rope_looseness_timeline.py \
  --input-dir report \
  --start-hhmm 1016 \
  --end-hhmm 1057 \
  --min-score 60 \
  --confirm-windows 2 \
  --pretty
```

## 注意

- 脚本会优先使用 `is_new_frame=1` 的真实点，自动规避 fixed100 补点干扰。
- 当有效样本很少或有效采样率很低时，结果会自动降低 `quality_factor`。
- 这是规则算法，适合快速筛查，不等同于高采样频谱诊断。
