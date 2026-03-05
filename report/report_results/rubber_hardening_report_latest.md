# 橡胶硬化检测报告

- 生成时间: 2026-03-03 17:44:22
- 模式: `time_split`
- 判定阈值: `hardening_score >= 55.0` 且 `gap_normal_minus_hard > 0`
- 特征建模方式: 正常样本 vs 橡胶硬化样本 双模板距离评分（自动选取高区分特征）

## 数据规则
- 正常基线: `/home/orangepi/vb01_python/report/vibration_30s_20260303_101725.csv` + `/home/orangepi/vb01_python/report/vibration_30s_20260303_101836.csv`
- 自动硬化标签: 文件名时间 `HHMM >= 1104`
- 数据匹配: `/home/orangepi/vb01_python/report/vibration_30s_20260303_*.csv`

## 总体异常结论
- 是否检测到异常: `是`
- 判为硬化异常数量: `8/28`
- 最高分样本: `vibration_30s_20260303_110424` (`88.00`)
- 异常样本列表: `vibration_30s_20260303_110424(88.00), vibration_30s_20260303_110344(78.97), vibration_30s_20260303_110622(78.96), vibration_30s_20260303_110322(66.31), vibration_30s_20260303_110543(64.83), vibration_30s_20260303_105641(63.45), vibration_30s_20260303_110451(61.29), vibration_30s_20260303_103711(60.35)`

## 训练样本
- `normal_up`: `/home/orangepi/vb01_python/report/vibration_30s_20260303_101725.csv`
- `normal_down`: `/home/orangepi/vb01_python/report/vibration_30s_20260303_101836.csv`
- `hard_01`: `/home/orangepi/vb01_python/report/vibration_30s_20260303_110424.csv`
- `hard_02`: `/home/orangepi/vb01_python/report/vibration_30s_20260303_110451.csv`
- `hard_03`: `/home/orangepi/vb01_python/report/vibration_30s_20260303_110543.csv`
- `hard_04`: `/home/orangepi/vb01_python/report/vibration_30s_20260303_110622.csv`

## 验证指标（有标签样本）
- Accuracy: `1.0000` on `6` samples
```text
pred       hardening  normal
truth                       
hardening          4       0
normal             0       2
```

## 检测结论（按 hardening_score 排序）
- `vibration_30s_20260303_110424`: truth=`hardening`，pred=`hardening`，label=`rubber_hardening`，hardening_score=`88.00`，gap=`1.107`
- `vibration_30s_20260303_110344`: truth=`None`，pred=`hardening`，label=`rubber_hardening`，hardening_score=`78.97`，gap=`0.735`
- `vibration_30s_20260303_110622`: truth=`hardening`，pred=`hardening`，label=`rubber_hardening`，hardening_score=`78.96`，gap=`0.735`
- `vibration_30s_20260303_110322`: truth=`None`，pred=`hardening`，label=`rubber_hardening`，hardening_score=`66.31`，gap=`0.376`
- `vibration_30s_20260303_110543`: truth=`hardening`，pred=`hardening`，label=`rubber_hardening`，hardening_score=`64.83`，gap=`0.340`
- `vibration_30s_20260303_105641`: truth=`None`，pred=`hardening`，label=`rubber_hardening`，hardening_score=`63.45`，gap=`0.306`
- `vibration_30s_20260303_110451`: truth=`hardening`，pred=`hardening`，label=`rubber_hardening`，hardening_score=`61.29`，gap=`0.255`
- `vibration_30s_20260303_103711`: truth=`None`，pred=`hardening`，label=`rubber_hardening`，hardening_score=`60.35`，gap=`0.233`
- `vibration_30s_20260303_101624`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`44.34`，gap=`-0.126`
- `vibration_30s_20260303_105710`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`44.25`，gap=`-0.128`
- `vibration_30s_20260303_105427`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`44.03`，gap=`-0.133`
- `vibration_30s_20260303_104530`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`41.85`，gap=`-0.183`
- `vibration_30s_20260303_104718`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`40.10`，gap=`-0.223`
- `vibration_30s_20260303_103816`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`34.47`，gap=`-0.357`
- `vibration_30s_20260303_105518`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`33.84`，gap=`-0.372`
- `vibration_30s_20260303_104608`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`33.84`，gap=`-0.372`
- `vibration_30s_20260303_104100`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`33.43`，gap=`-0.383`
- `vibration_30s_20260303_101912`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`30.33`，gap=`-0.462`
- `vibration_30s_20260303_101650`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`27.56`，gap=`-0.537`
- `vibration_30s_20260303_110154`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`26.25`，gap=`-0.574`
- `vibration_30s_20260303_103912`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`25.66`，gap=`-0.591`
- `vibration_30s_20260303_105553`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`21.89`，gap=`-0.707`
- `vibration_30s_20260303_110250`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`21.77`，gap=`-0.711`
- `vibration_30s_20260303_104806`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`18.43`，gap=`-0.826`
- `vibration_30s_20260303_103751`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`16.47`，gap=`-0.902`
- `vibration_30s_20260303_101836`: truth=`normal`，pred=`normal`，label=`normal_like`，hardening_score=`16.01`，gap=`-0.921`
- `vibration_30s_20260303_104016`: truth=`None`，pred=`normal`，label=`normal_like`，hardening_score=`13.51`，gap=`-1.031`
- `vibration_30s_20260303_101725`: truth=`normal`，pred=`normal`，label=`normal_like`，hardening_score=`10.95`，gap=`-1.164`

## 检测跳过样本
- `vibration_30s_20260303_100819`: vibration_30s_20260303_100819.csv: 有效样本太少（3）
- `vibration_30s_20260303_101536`: vibration_30s_20260303_101536.csv: 有效样本太少（1）
- `vibration_30s_20260303_103656`: vibration_30s_20260303_103656.csv: 有效样本太少（1）
- `vibration_30s_20260303_104650`: vibration_30s_20260303_104650.csv: 有效样本太少（2）
- `vibration_30s_20260303_105505`: vibration_30s_20260303_105505.csv: 有效样本太少（1）

## 区分度最高特征（模型签名）
```text
             feature  mu_normal  mu_hard  sd_normal  sd_hard   scale  separation
0             az_p2p     0.0371   0.0549     0.0020   0.0046  0.0066      2.7037
1            corr_xy    -0.7084   0.0771     0.0884   0.2526  0.3409      2.3040
2              az_cv     0.7640   1.3543     0.0078   0.2758  0.2836      2.0814
3             az_std     0.0091   0.0128     0.0004   0.0025  0.0029      1.2617
4          az_rms_ac     0.0091   0.0128     0.0004   0.0025  0.0029      1.2617
5        az_jerk_rms     0.0138   0.0197     0.0002   0.0059  0.0061      0.9780
6             mag_cv     0.0140   0.0106     0.0008   0.0029  0.0037      0.9269
7         mag_rms_ac     0.0140   0.0106     0.0008   0.0029  0.0037      0.9203
8            mag_std     0.0140   0.0106     0.0008   0.0029  0.0037      0.9203
9            ax_mean    -0.7694  -0.7708     0.0004   0.0011  0.0015      0.8974
10           az_mean     0.0119   0.0097     0.0004   0.0022  0.0026      0.8261
11        az_qspread     0.0262   0.0338     0.0023   0.0080  0.0103      0.7429
12  energy_z_over_xy     0.8651   1.3363     0.0950   0.6013  0.6962      0.6768
13           ay_mean     0.6319   0.6296     0.0011   0.0024  0.0035      0.6516
```

## 训练样本特征表
```text
             ax_mean  ax_std  ax_p2p  ax_rms_ac  ax_kurt  ax_jerk_rms  ax_qspread   ax_cv  ay_mean  ay_std  ay_p2p  ay_rms_ac  ay_kurt  ay_jerk_rms  ay_qspread   ay_cv  az_mean  az_std  az_p2p  az_rms_ac  az_kurt  az_jerk_rms  az_qspread   az_cv  mag_mean  mag_std  mag_p2p  mag_rms_ac  mag_kurt  mag_jerk_rms  mag_qspread  mag_cv  corr_xy  corr_xz  corr_yz  energy_x_over_y  energy_z_over_xy     n       role
normal_up    -0.7690  0.0137  0.0508     0.0137  -0.1950       0.0203      0.0422  0.0178   0.6331  0.0090  0.0361     0.0090   0.2993       0.0127      0.0261  0.0142   0.0115  0.0087  0.0352     0.0087   2.3513       0.0139      0.0239  0.7562    0.9962   0.0147   0.0539      0.0147   -0.1934        0.0220       0.0457  0.0148  -0.6201   0.1462  -0.6696           1.5212            0.7702  11.0     normal
normal_down  -0.7698  0.0095  0.0420     0.0095   0.8229       0.0118      0.0303  0.0124   0.6308  0.0102  0.0498     0.0102   3.4407       0.0116      0.0260  0.0162   0.0123  0.0095  0.0391     0.0095   0.7657       0.0136      0.0284  0.7718    0.9954   0.0132   0.0647      0.0132    2.7145        0.0158       0.0355  0.0133  -0.7968  -0.5910   0.3708           0.9342            0.9601  16.0     normal
hard_01      -0.7719  0.0092  0.0298     0.0092  -0.0068       0.0159      0.0247  0.0119   0.6263  0.0056  0.0176     0.0056   0.2510       0.0084      0.0144  0.0090   0.0124  0.0170  0.0576     0.0170   0.7675       0.0298      0.0450  1.3724    0.9943   0.0069   0.0226      0.0069    0.2625        0.0092       0.0185  0.0069   0.3014   0.4747   0.5912           1.6290            2.2992   7.0  hardening
hard_02      -0.7703  0.0125  0.0557     0.0125   0.8878       0.0161      0.0461  0.0163   0.6293  0.0092  0.0449     0.0092   2.0286       0.0102      0.0275  0.0145   0.0115  0.0104  0.0518     0.0104   5.8010       0.0152      0.0231  0.8981    0.9949   0.0102   0.0510      0.0102    1.7293        0.0144       0.0298  0.0103   0.2354  -0.3808   0.1212           1.3694            0.9550  18.0  hardening
hard_03      -0.7718  0.0155  0.0605     0.0155   0.1726       0.0216      0.0535  0.0201   0.6331  0.0169  0.0806     0.0169   1.2450       0.0182      0.0513  0.0267   0.0075  0.0118  0.0493     0.0118   0.3248       0.0182      0.0306  1.5693    0.9985   0.0151   0.0610      0.0151    3.4670        0.0198       0.0478  0.0151   0.1167  -0.0917  -0.5361           0.9213            0.7258  17.0  hardening
hard_04      -0.7692  0.0093  0.0488     0.0093   2.9363       0.0152      0.0283  0.0121   0.6298  0.0082  0.0469     0.0082   5.0306       0.0109      0.0142  0.0130   0.0076  0.0119  0.0610     0.0119   2.9821       0.0157      0.0366  1.5773    0.9942   0.0102   0.0533      0.0102    2.9602        0.0158       0.0350  0.0103  -0.3451  -0.2207  -0.5044           1.1402            1.3653  21.0  hardening
```

## 检测结果表
```text
                                   truth pred_binary  hardening_score             label  dist_to_normal  dist_to_hardening  gap_normal_minus_hard                                                                                                                                                             evidence_top
file                                                                                                                                                                                                                                                                                                                      
vibration_30s_20260303_101624       None      normal          44.3443       normal_like          0.8739             1.0001                -0.1262                    az_cv(x=1.368,N=0.764,H=1.354); ax_mean(x=-0.7734,N=-0.7694,H=-0.7708); az_mean(x=0.007161,N=0.0119,H=0.00975); mag_cv(x=0.01134,N=0.01403,H=0.01064)
vibration_30s_20260303_101650       None      normal          27.5571       normal_like          0.6900             1.2270                -0.5370           mag_cv(x=0.0102,N=0.01403,H=0.01064); mag_rms_ac(x=0.01014,N=0.01398,H=0.01059); mag_std(x=0.01014,N=0.01398,H=0.01059); az_mean(x=0.01035,N=0.0119,H=0.00975)
vibration_30s_20260303_101725     normal      normal          10.9541       normal_like          0.1828             1.3469                -1.1641                                                                                                                                                                      N/A
vibration_30s_20260303_101836     normal      normal          16.0141       normal_like          0.1828             1.1034                -0.9207                                                                                                                                                                      N/A
vibration_30s_20260303_101912       None      normal          30.3293       normal_like          2.0488             2.5108                -0.4620  mag_cv(x=0.001339,N=0.01403,H=0.01064); mag_rms_ac(x=0.001333,N=0.01398,H=0.01059); mag_std(x=0.001333,N=0.01398,H=0.01059); energy_z_over_xy(x=2.134,N=0.8651,H=1.336)
vibration_30s_20260303_103711       None   hardening          60.3490  rubber_hardening          0.9377             0.7044                 0.2333       corr_xy(x=0.1508,N=-0.7084,H=0.07713); mag_cv(x=0.008298,N=0.01403,H=0.01064); mag_rms_ac(x=0.008286,N=0.01398,H=0.01059); mag_std(x=0.008286,N=0.01398,H=0.01059)
vibration_30s_20260303_103751       None      normal          16.4715       normal_like          2.0178             2.9198                -0.9020                                                                                             ax_mean(x=-0.778,N=-0.7694,H=-0.7708); az_mean(x=0.01031,N=0.0119,H=0.00975)
vibration_30s_20260303_103816       None      normal          34.4727       normal_like          1.8220             2.1788                -0.3568       corr_xy(x=0.3775,N=-0.7084,H=0.07713); mag_cv(x=0.004702,N=0.01403,H=0.01064); mag_rms_ac(x=0.004673,N=0.01398,H=0.01059); mag_std(x=0.004673,N=0.01398,H=0.01059)
vibration_30s_20260303_103912       None      normal          25.6635       normal_like          0.7681             1.3590                -0.5909                   az_cv(x=1.233,N=0.764,H=1.354); az_mean(x=0.007904,N=0.0119,H=0.00975); ay_mean(x=0.6295,N=0.6319,H=0.6296); az_qspread(x=0.03186,N=0.02618,H=0.03383)
vibration_30s_20260303_104016       None      normal          13.5089       normal_like          0.3589             1.3904                -1.0315                                                                                                                                                                      N/A
vibration_30s_20260303_104100       None      normal          33.4298       normal_like          1.3500             1.7327                -0.3827         corr_xy(x=0.2967,N=-0.7084,H=0.07713); mag_cv(x=0.008457,N=0.01403,H=0.01064); mag_rms_ac(x=0.00842,N=0.01398,H=0.01059); mag_std(x=0.00842,N=0.01398,H=0.01059)
vibration_30s_20260303_104530       None      normal          41.8504       normal_like          1.6979             1.8806                -0.1827           corr_xy(x=0.6676,N=-0.7084,H=0.07713); mag_cv(x=0.002411,N=0.01403,H=0.01064); mag_rms_ac(x=0.0024,N=0.01398,H=0.01059); mag_std(x=0.0024,N=0.01398,H=0.01059)
vibration_30s_20260303_104608       None      normal          33.8389       normal_like          1.7466             2.1191                -0.3725         corr_xy(x=0.7318,N=-0.7084,H=0.07713); mag_cv(x=0.006654,N=0.01403,H=0.01064); mag_rms_ac(x=0.00664,N=0.01398,H=0.01059); mag_std(x=0.00664,N=0.01398,H=0.01059)
vibration_30s_20260303_104718       None      normal          40.0957       normal_like          1.1762             1.3993                -0.2230           corr_xy(x=0.527,N=-0.7084,H=0.07713); mag_cv(x=0.01037,N=0.01403,H=0.01064); mag_rms_ac(x=0.01033,N=0.01398,H=0.01059); mag_std(x=0.01033,N=0.01398,H=0.01059)
vibration_30s_20260303_104806       None      normal          18.4331       normal_like          1.4251             2.2513                -0.8263                                              mag_cv(x=0.006725,N=0.01403,H=0.01064); mag_rms_ac(x=0.006726,N=0.01398,H=0.01059); mag_std(x=0.006726,N=0.01398,H=0.01059)
vibration_30s_20260303_105427       None      normal          44.0302       normal_like          1.5785             1.7118                -0.1333       corr_xy(x=0.8238,N=-0.7084,H=0.07713); mag_cv(x=0.003214,N=0.01403,H=0.01064); mag_rms_ac(x=0.003203,N=0.01398,H=0.01059); mag_std(x=0.003203,N=0.01398,H=0.01059)
vibration_30s_20260303_105518       None      normal          33.8389       normal_like          2.0906             2.4631                -0.3725       corr_xy(x=0.4752,N=-0.7084,H=0.07713); mag_cv(x=0.002576,N=0.01403,H=0.01064); mag_rms_ac(x=0.002571,N=0.01398,H=0.01059); mag_std(x=0.002571,N=0.01398,H=0.01059)
vibration_30s_20260303_105553       None      normal          21.8897       normal_like          0.6410             1.3477                -0.7067                                                                                            corr_xy(x=0.1095,N=-0.7084,H=0.07713); ax_mean(x=-0.7734,N=-0.7694,H=-0.7708)
vibration_30s_20260303_105641       None   hardening          63.4471  rubber_hardening          1.5324             1.2261                 0.3064                 az_p2p(x=0.06299,N=0.03711,H=0.05493); az_cv(x=1.539,N=0.764,H=1.354); az_std(x=0.01603,N=0.009098,H=0.01277); az_rms_ac(x=0.01603,N=0.009098,H=0.01277)
vibration_30s_20260303_105710       None      normal          44.2479       normal_like          0.8065             0.9349                -0.1284                                                                                           corr_xy(x=0.08899,N=-0.7084,H=0.07713); ax_mean(x=-0.7746,N=-0.7694,H=-0.7708)
vibration_30s_20260303_110154       None      normal          26.2474       normal_like          1.0167             1.5907                -0.5740         mag_cv(x=0.007398,N=0.01403,H=0.01064); mag_rms_ac(x=0.007347,N=0.01398,H=0.01059); mag_std(x=0.007347,N=0.01398,H=0.01059); ay_mean(x=0.6276,N=0.6319,H=0.6296)
vibration_30s_20260303_110250       None      normal          21.7731       normal_like          1.1028             1.8133                -0.7105                                                       ax_mean(x=-0.772,N=-0.7694,H=-0.7708); az_mean(x=0.006871,N=0.0119,H=0.00975); ay_mean(x=0.6305,N=0.6319,H=0.6296)
vibration_30s_20260303_110322       None   hardening          66.3085  rubber_hardening          1.4425             1.0663                 0.3762                corr_xy(x=0.9721,N=-0.7084,H=0.07713); az_cv(x=1.797,N=0.764,H=1.354); mag_cv(x=0.007565,N=0.01403,H=0.01064); mag_rms_ac(x=0.007541,N=0.01398,H=0.01059)
vibration_30s_20260303_110344       None   hardening          78.9707  rubber_hardening          1.5444             0.8094                 0.7351          az_p2p(x=0.0542,N=0.03711,H=0.05493); corr_xy(x=0.3233,N=-0.7084,H=0.07713); mag_cv(x=0.005234,N=0.01403,H=0.01064); mag_rms_ac(x=0.005225,N=0.01398,H=0.01059)
vibration_30s_20260303_110424  hardening   hardening          88.0048  rubber_hardening          2.1015             0.9943                 1.1072                     az_p2p(x=0.05762,N=0.03711,H=0.05493); corr_xy(x=0.3014,N=-0.7084,H=0.07713); az_cv(x=1.372,N=0.764,H=1.354); az_std(x=0.01704,N=0.009098,H=0.01277)
vibration_30s_20260303_110451  hardening   hardening          61.2900  rubber_hardening          0.8218             0.5665                 0.2553           corr_xy(x=0.2354,N=-0.7084,H=0.07713); az_p2p(x=0.05176,N=0.03711,H=0.05493); mag_cv(x=0.01028,N=0.01403,H=0.01064); mag_rms_ac(x=0.01023,N=0.01398,H=0.01059)
vibration_30s_20260303_110543  hardening   hardening          64.8268  rubber_hardening          1.0526             0.7129                 0.3397                     corr_xy(x=0.1167,N=-0.7084,H=0.07713); az_cv(x=1.569,N=0.764,H=1.354); az_p2p(x=0.04932,N=0.03711,H=0.05493); ax_mean(x=-0.7718,N=-0.7694,H=-0.7708)
vibration_30s_20260303_110622  hardening   hardening          78.9551  rubber_hardening          1.2188             0.4843                 0.7346                  az_p2p(x=0.06104,N=0.03711,H=0.05493); az_cv(x=1.577,N=0.764,H=1.354); mag_cv(x=0.01027,N=0.01403,H=0.01064); mag_rms_ac(x=0.01021,N=0.01398,H=0.01059)
```