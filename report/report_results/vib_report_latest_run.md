# 振动异常检测报告

- 生成时间: 2026-03-03 17:23:46
- 轴模式: `mag`
- 判别阈值: `score >= 55.0` 判为异常倾向
- 算法口径: 基于 `normal` 与 `abnormal` 模板的距离差做规则匹配评分
- 稳健化: 特征尺度带下限，低样本文件自动跳过；样本置信系数=`1.00`

## 输入文件
- `normal_up`: `/home/orangepi/vb01_python/report/vibration_30s_20260303_101725.csv`
- `normal_down`: `/home/orangepi/vb01_python/report/vibration_30s_20260303_101836.csv`
- `abn_up`: `/home/orangepi/vb01_python/report/vibration_30s_20260303_104530.csv`
- `abn_down`: `/home/orangepi/vb01_python/report/vibration_30s_20260303_104608.csv`

## 结论摘要
- `abn_up`: `alarm_strict`，score=`99.52`，gap=`2.9665`(raw=`2.9665`)；更接近松动/摩擦型振动增强（波动和能量更高）
- `abn_down`: `alarm_strict`，score=`95.49`，gap=`1.6965`(raw=`1.6965`)；更接近松动/摩擦型振动增强（波动和能量更高）

## 检测结果表
```text
                 role  score_0_100         level  gap_raw  gap_normal_minus_abnormal  dist_normal  dist_abnormal                        hit_feats               inference
file                                                                                                                                                                     
normal_up      normal         0.66        normal  -2.7874                    -2.7874       0.3524         3.1290                                                  更接近正常模板
normal_down    normal         1.20        normal  -2.4486                    -2.4486       0.3524         2.7629                                                  更接近正常模板
abn_up       abnormal        99.52  alarm_strict   2.9665                     2.9665       3.5780         0.6320  p2p,std,rms_ac,jerk_rms,qspread  更接近松动/摩擦型振动增强（波动和能量更高）
abn_down     abnormal        95.49  alarm_strict   1.6965                     1.6965       2.3140         0.6320  p2p,std,rms_ac,qspread,jerk_rms  更接近松动/摩擦型振动增强（波动和能量更高）
```

## 特征签名模型
```text
    feature  mu_normal  mu_abnormal  sd_normal  sd_abnormal  gap_abs   scale  separation
0       p2p     0.0593       0.0178     0.0054       0.0074   0.0415  0.0128      3.2417
1       std     0.0140       0.0045     0.0008       0.0021   0.0095  0.0030      3.1521
2    rms_ac     0.0140       0.0045     0.0008       0.0021   0.0095  0.0030      3.1521
3  jerk_rms     0.0133       0.0037     0.0013       0.0023   0.0096  0.0036      2.6289
4   qspread     0.0406       0.0127     0.0051       0.0058   0.0278  0.0109      2.5551
```

## 原始特征表
```text
            status    mean     std     p2p     rms  rms_ac  qspread    kurt  jerk_rms   n      role
file                                                                                               
normal_up       ok  0.9962  0.0147  0.0539  0.9963  0.0147   0.0457 -0.1934    0.0146  11    normal
normal_down     ok  0.9954  0.0132  0.0647  0.9955  0.0132   0.0355  2.7145    0.0120  16    normal
abn_up          ok  0.9957  0.0024  0.0104  0.9957  0.0024   0.0070  0.9364    0.0014  13  abnormal
abn_down        ok  0.9978  0.0066  0.0252  0.9978  0.0066   0.0185  0.0798    0.0061  13  abnormal
```