# 电梯振动规则算法

输入字段（你现有数据可直接用）：

`ts_ms,data_ts_ms,data_age_ms,is_new_frame,Ax,Ay,Az,Gx,Gy,Gz,vx,vy,vz,ax,ay,az,t,sx,sy,sz,fx,fy,fz,A_mag,G_mag`

当前总控 `run_all.py` 采用两阶段规则：

1. 先做“相对健康基线的异常筛查”
2. 只有异常门命中后，才进入保守的 `rope_vs_rubber` 归因层

## 当前主链

- 第一阶段目标：回答“当前窗口是否明显偏离这台电梯的健康状态”
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

## 第二阶段归因

- 只在第一阶段已经判成异常时运行
- 当前只尝试区分：
  - `rope_looseness`
  - `rubber_hardening`
- 归因规则是保守的：
  - 一边证据明显领先时，才给具体类型
  - 两边证据混合时，继续返回 `unknown`
- 当前使用的归因特征以横向/竖向耦合、能量分配和方向集中度为主，例如：
  - `energy_x_over_y`
  - `corr_xy`
  - `corr_xz`
  - `a_pca_primary_ratio`
  - `z_peak_ratio`
  - `az_cv`
  - `az_jerk_rms`
  - `lateral_ratio`
  - `lat_dom_freq_hz`

## 经验模板说明

- 这里的“经验模板”不是训练模型，也不是“一刀切”的硬阈值。
- 它本质上是一组人工设定的弱先验：每个特征只给出大致的 `direction / lo / hi / weight`，表示“典型 rope / rubber 往往更像什么形状”。
- 当前归因分支不会只靠模板打分。默认是：
  - `65%` 来自“相对本梯健康基线的偏移”
  - `35%` 来自“经验模板先验”
- 这样做的目的，是在健康样本偏少、不同电梯安装姿态不完全一致时，减少归因完全漂掉或跨梯直接失灵。
- 用户可见口径必须保持保守：
  - 如果某一类分支明显领先，可以说“当前更像/偏向钢丝绳松动”或“当前更像/偏向橡胶圈硬化”
  - 如果两边证据混合，继续返回 `unknown`
  - 不要把这层归因写成“已经确诊”

## 文件清单

- `run_all.py`（当前主入口）
- `rope_vs_rubber.py`（异常后的保守归因层）
- `run_all.py` 主要输出：
  - 通用异常门结果 `system_abnormality`
  - 归因层结果 `rope_primary` / `rubber_primary`
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
- 低振动但形态已经明显偏离健康基线的窗口，也可能被保守放进 `watch_only`，避免被固定运行态门全部压掉。
- 当前用户可见结论仍保持保守：优先回答“异常/不异常”，只有归因层领先足够明显时才补充类型。
- 这是规则算法，适合快速筛查，不等同于高采样频谱诊断。
