# 电梯振动规则算法

输入字段（你现有数据可直接用）：

`ts_ms,data_ts_ms,data_age_ms,is_new_frame,Ax,Ay,Az,Gx,Gy,Gz,vx,vy,vz,ax,ay,az,t,sx,sy,sz,fx,fy,fz,A_mag,G_mag`

当前总控 `run_all.py` 只保留“相对健康基线的异常筛查”主链。

## 当前主链

- 目标：只回答“当前窗口是否明显偏离这台电梯的健康状态”
- 判定方式：优先对健康基线做 `median + MAD` 的 robust 归一化；缺少基线时回退到少量自归一化特征
- 输出状态：
  - `normal`
  - `watch_only`
  - `candidate_faults`
- 当前使用的共享异常特征以整体振动、方向比例和低频结构为主，例如：
  - `a_rms_ac`
  - `a_p2p`
  - `g_std`
  - `a_peak_std`
  - `a_pca_primary_ratio`
  - `a_band_log_ratio_0_5_over_5_20`
  - `lateral_ratio`
  - `lat_dom_freq_hz`
  - `lat_low_band_ratio`
  - `z_peak_ratio`

## 文件清单

- `detect_rope_looseness.py`
- `detect_rubber_hardening.py`
- `run_all.py`（当前主入口）
- `run_all.py` 主要输出：
  - 通用异常门结果 `system_abnormality`
  - 高置信异常 `candidate_faults`
  - 观察级异常 `watch_faults`
  - 筛查状态 `screening`

目录中其余 `detect_*.py` 仍可单独执行，但已不再参与默认总控决策。

## 使用示例

单个算法：

```bash
python3 report/fault_algorithms/detect_impact_shock.py \
  --input report/vibration_30s_20260303_110622.csv --pretty
```

一次跑当前异常筛查主链：

```bash
python3 report/fault_algorithms/run_all.py \
  --input report/vibration_30s_20260303_110622.csv \
  --baseline-dir report \
  --baseline-start-hhmm 1017 \
  --baseline-end-hhmm 1018 \
  --pretty
```

## 注意

- 脚本会优先使用 `is_new_frame=1` 的真实点，自动规避 fixed100 补点干扰。
- 当有效样本很少或有效采样率很低时，结果会自动降低 `quality_factor`。
- 当前总控优先依赖健康基线；换梯迁移时建议先给一小段健康样本建基线。
- 没有健康基线也能跑，但结果会更保守。
- 当前用户可见结论以“异常/不异常”为主，不把原始最高分直接当作具体故障结论。
- 这是规则算法，适合快速筛查，不等同于高采样频谱诊断。
