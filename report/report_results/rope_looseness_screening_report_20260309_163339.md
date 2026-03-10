# 钢丝绳候选故障筛查回放报告

- 生成时间: 2026-03-09T16:33:39
- 报告文件: `rope_looseness_screening_report_20260309_163339.md`
- 明细文件: `rope_looseness_screening_report_20260309_163339.json`

## 第二台梯回放

- 数据目录: `/Users/qiudejia/Downloads/vb01_python/data/captures`
- 基线模式: `dir`
- 基线样本数: `5`
- 基线特征数: `11`
- 基线时间窗: `1015-1019`

### 分阶段统计

- `normal_1015_1037`: 状态分布 `{'low_quality': 2, 'normal': 5, 'watch_only': 2}`，top_fault 分布 `{'car_imbalance': 7, 'rope_looseness': 2}`
- `loose1_1038_1044`: 状态分布 `{'watch_only': 4}`，top_fault 分布 `{'rope_looseness': 4}`
- `loose2_1045_1049`: 状态分布 `{'normal': 1, 'candidate_faults': 1, 'low_quality': 2, 'watch_only': 1}`，top_fault 分布 `{'car_imbalance': 2, 'rope_looseness': 3}`
- `recover_1050_1104`: 状态分布 `{'normal': 7, 'low_quality': 3, 'watch_only': 2}`，top_fault 分布 `{'car_imbalance': 8, 'rope_looseness': 4}`
- `rubber_1105_1106`: 状态分布 `{'normal': 2}`，top_fault 分布 `{'car_imbalance': 2}`
- `unlabeled`: 状态分布 `{'low_quality': 1}`，top_fault 分布 `{'rope_looseness': 1}`

### 高置信候选窗口

- `104608` `vibration_30s_20260303_104608.csv`: status=`candidate_faults` top=`rope_looseness` score=`68.08` candidate=`['rope_looseness']` n=`13`

### Watch 窗口

- `101725` `vibration_30s_20260303_101725.csv`: top=`rope_looseness` score=`59.0` watch=`['rope_looseness']` n=`11`
- `103751` `vibration_30s_20260303_103751.csv`: top=`rope_looseness` score=`58.4` watch=`['rope_looseness']` n=`9`
- `103816` `vibration_30s_20260303_103816.csv`: top=`rope_looseness` score=`51.16` watch=`['rope_looseness']` n=`11`
- `103912` `vibration_30s_20260303_103912.csv`: top=`rope_looseness` score=`48.28` watch=`['rope_looseness']` n=`16`
- `104016` `vibration_30s_20260303_104016.csv`: top=`rope_looseness` score=`54.76` watch=`['rope_looseness']` n=`12`
- `104100` `vibration_30s_20260303_104100.csv`: top=`rope_looseness` score=`45.89` watch=`['rope_looseness']` n=`9`
- `104718` `vibration_30s_20260303_104718.csv`: top=`rope_looseness` score=`56.85` watch=`['rope_looseness']` n=`11`
- `105553` `vibration_30s_20260303_105553.csv`: top=`rope_looseness` score=`59.0` watch=`['rope_looseness']` n=`9`
- `110250` `vibration_30s_20260303_110250.csv`: top=`car_imbalance` score=`53.86` watch=`['rope_looseness']` n=`14`

### 低质量窗口

- `100819` `vibration_30s_20260303_100819.csv`: n_effective=`3` n_raw=`3` top=`rope_looseness` score=`18.81`
- `101536` `vibration_30s_20260303_101536.csv`: n_effective=`1` n_raw=`1` top=`car_imbalance` score=`9.0`
- `103656` `vibration_30s_20260303_103656.csv`: n_effective=`1` n_raw=`1` top=`car_imbalance` score=`9.0`
- `104650` `vibration_30s_20260303_104650.csv`: n_effective=`2` n_raw=`2` top=`car_imbalance` score=`11.83`
- `104806` `vibration_30s_20260303_104806.csv`: n_effective=`4` n_raw=`4` top=`rope_looseness` score=`17.55`
- `105505` `vibration_30s_20260303_105505.csv`: n_effective=`1` n_raw=`1` top=`car_imbalance` score=`9.0`
- `110322` `vibration_30s_20260303_110322.csv`: n_effective=`4` n_raw=`4` top=`car_imbalance` score=`9.89`
- `110424` `vibration_30s_20260303_110424.csv`: n_effective=`7` n_raw=`7` top=`rope_looseness` score=`12.21`

## 第一台梯回放

- 数据目录: `/Users/qiudejia/Downloads/vb01_python/report/data_files`
- 说明: 当前正常样本不足以构建稳定 robust 基线，因此本组结果主要来自统一筛查的 fallback/规则评分。

- `异常.csv`: status=`watch_only` top=`rope_looseness` score=`47.87` candidate=`[]` watch=`['rope_looseness']` n=`15`
- `异常1.csv`: status=`watch_only` top=`rope_looseness` score=`46.25` candidate=`[]` watch=`['rope_looseness']` n=`12`
- `正常上行.csv`: status=`normal` top=`car_imbalance` score=`26.34` candidate=`[]` watch=`[]` n=`36`
- `正常下行.csv`: status=`normal` top=`car_imbalance` score=`28.84` candidate=`[]` watch=`[]` n=`32`
- `螺栓松动异常上行.csv`: status=`normal` top=`car_imbalance` score=`24.75` candidate=`[]` watch=`[]` n=`40`
- `螺栓松动异常下行.csv`: status=`watch_only` top=`rope_looseness` score=`47.64` candidate=`[]` watch=`['rope_looseness']` n=`20`

## 结论

- 第二台梯当前最明确的钢丝绳候选窗口是 `104608`，结果为 `candidate_faults -> ['rope_looseness']`，分数 `68.08`。
- 算法当前按高精度优先运行，宁可漏报，也会压低单窗弱证据导致的误报。
- 如果后续要继续提高第二台梯的识别稳定性，优先建议补更多干净正常样本，并按上行/下行分开建基线。
