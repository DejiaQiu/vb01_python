# 橡胶硬化检测报告

- 生成时间: 2026-03-03 15:39:04
- 判定阈值: `hardening_score >= 55.0` 且 `gap_normal_minus_hard > 0`
- 特征建模方式: 正常样本 vs 橡胶硬化样本 双模板距离评分（自动选取高区分特征）

## 输入文件
- `normal_up`: `/home/orangepi/.zeroclaw/workspace/vibration_30s_20260303_101725.csv`
- `normal_down`: `/home/orangepi/.zeroclaw/workspace/vibration_30s_20260303_101836.csv`
- `hard_up`: `/home/orangepi/.zeroclaw/workspace/vibration_30s_20260303_110451.csv`
- `hard_down`: `/home/orangepi/.zeroclaw/workspace/vibration_30s_20260303_110543.csv`

## 检测结论
- `hard_down`: label=`rubber_hardening`，hardening_score=`95.38`，gap=`1.682`，evidence=`corr_xy(x=0.1167,N=-0.7084,H=0.1761); az_p2p(x=0.04932,N=0.03711,H=0.05054); az_std(x=0.01176,N=0.009098,H=0.01106); az_rms_ac(x=0.01176,N=0.009098,H=0.01106)`
- `hard_up`: label=`rubber_hardening`，hardening_score=`72.72`，gap=`0.545`，evidence=`corr_xy(x=0.2354,N=-0.7084,H=0.1761); az_p2p(x=0.05176,N=0.03711,H=0.05054); ax_p2p(x=0.05566,N=0.04639,H=0.05811); ax_qspread(x=0.04612,N=0.03625,H=0.04982)`
- `normal_down`: label=`normal_like`，hardening_score=`6.64`，gap=`-1.468`，evidence=`N/A`
- `normal_up`: label=`normal_like`，hardening_score=`6.33`，gap=`-1.497`，evidence=`N/A`

## 区分度最高特征（模型签名）
```text
        feature  mu_normal  mu_hard  sd_normal  sd_hard   scale  separation
0       corr_xy    -0.7084   0.1761     0.0884   0.0594  0.1769      5.9880
1        az_p2p     0.0371   0.0505     0.0020   0.0012  0.0032      4.2308
2        az_std     0.0091   0.0111     0.0004   0.0007  0.0011      1.8247
3     az_rms_ac     0.0091   0.0111     0.0004   0.0007  0.0011      1.8247
4   az_jerk_rms     0.0138   0.0167     0.0002   0.0015  0.0017      1.7415
5        ax_p2p     0.0464   0.0581     0.0044   0.0024  0.0068      1.7143
6       ax_mean    -0.7694  -0.7710     0.0004   0.0007  0.0011      1.4322
7    ax_qspread     0.0363   0.0498     0.0060   0.0037  0.0097      1.4010
8         az_cv     0.7640   1.2337     0.0078   0.3356  0.3433      1.3681
9    ay_qspread     0.0261   0.0394     0.0001   0.0119  0.0120      1.1144
10      az_mean     0.0119   0.0095     0.0004   0.0020  0.0024      1.0052
11       ay_p2p     0.0430   0.0627     0.0068   0.0178  0.0247      0.8020
12        ay_cv     0.0152   0.0206     0.0010   0.0061  0.0070      0.7694
13       ay_std     0.0096   0.0130     0.0006   0.0039  0.0045      0.7659
```

## 样本特征表
```text
             ax_mean  ax_std  ax_p2p  ax_rms_ac  ax_kurt  ax_jerk_rms  ax_qspread   ax_cv  ay_mean  ay_std  ay_p2p  ay_rms_ac  ay_kurt  ay_jerk_rms  ay_qspread   ay_cv  az_mean  az_std  az_p2p  az_rms_ac  az_kurt  az_jerk_rms  az_qspread   az_cv  mag_mean  mag_std  mag_p2p  mag_rms_ac  mag_kurt  mag_jerk_rms  mag_qspread  mag_cv  corr_xy  corr_xz  corr_yz  energy_x_over_y  energy_z_over_xy     n
normal_up    -0.7690  0.0137  0.0508     0.0137  -0.1950       0.0203      0.0422  0.0178   0.6331  0.0090  0.0361     0.0090   0.2993       0.0127      0.0261  0.0142   0.0115  0.0087  0.0352     0.0087   2.3513       0.0139      0.0239  0.7562    0.9962   0.0147   0.0539      0.0147   -0.1934        0.0220       0.0457  0.0148  -0.6201   0.1462  -0.6696           1.5212            0.7702  11.0
normal_down  -0.7698  0.0095  0.0420     0.0095   0.8229       0.0118      0.0303  0.0124   0.6308  0.0102  0.0498     0.0102   3.4407       0.0116      0.0260  0.0162   0.0123  0.0095  0.0391     0.0095   0.7657       0.0136      0.0284  0.7718    0.9954   0.0132   0.0647      0.0132    2.7145        0.0158       0.0355  0.0133  -0.7968  -0.5910   0.3708           0.9342            0.9601  16.0
hard_up      -0.7703  0.0125  0.0557     0.0125   0.8878       0.0161      0.0461  0.0163   0.6293  0.0092  0.0449     0.0092   2.0286       0.0102      0.0275  0.0145   0.0115  0.0104  0.0518     0.0104   5.8010       0.0152      0.0231  0.8981    0.9949   0.0102   0.0510      0.0102    1.7293        0.0144       0.0298  0.0103   0.2354  -0.3808   0.1212           1.3694            0.9550  18.0
hard_down    -0.7718  0.0155  0.0605     0.0155   0.1726       0.0216      0.0535  0.0201   0.6331  0.0169  0.0806     0.0169   1.2450       0.0182      0.0513  0.0267   0.0075  0.0118  0.0493     0.0118   0.3248       0.0182      0.0306  1.5693    0.9985   0.0151   0.0610      0.0151    3.4670        0.0198       0.0478  0.0151   0.1167  -0.0917  -0.5361           0.9213            0.7258  17.0
```

## 检测结果表
```text
             hardening_score             label  dist_to_normal  dist_to_hardening  gap_normal_minus_hard                                                                                                                                                    evidence_top
file                                                                                                                                                                                                                                                                    
normal_up               6.33       normal_like          0.3029             1.7994                -1.4965                                                                                                                                                             N/A
normal_down             6.64       normal_like          0.3029             1.7712                -1.4684                                                                                                                                                             N/A
hard_up                72.72  rubber_hardening          1.2299             0.6854                 0.5446   corr_xy(x=0.2354,N=-0.7084,H=0.1761); az_p2p(x=0.05176,N=0.03711,H=0.05054); ax_p2p(x=0.05566,N=0.04639,H=0.05811); ax_qspread(x=0.04612,N=0.03625,H=0.04982)
hard_down              95.38  rubber_hardening          2.3678             0.6854                 1.6824  corr_xy(x=0.1167,N=-0.7084,H=0.1761); az_p2p(x=0.04932,N=0.03711,H=0.05054); az_std(x=0.01176,N=0.009098,H=0.01106); az_rms_ac(x=0.01176,N=0.009098,H=0.01106)
```