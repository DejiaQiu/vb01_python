# Wire Looseness Inference Report (4 files)

- Generated at: 2026-03-03 17:05:55
- Model: `/home/orangepi/vb01_python/report/wire_looseness_model_latest.json`
- Threshold: `score >= 55.0` => loose

## Input Files
- `/home/orangepi/vb01_python/data/captures/vibration_30s_20260303_101725.csv`
- `/home/orangepi/vb01_python/data/captures/vibration_30s_20260303_101836.csv`
- `/home/orangepi/vb01_python/data/captures/vibration_30s_20260303_104608.csv`
- `/home/orangepi/vb01_python/data/captures/vibration_30s_20260303_104650.csv`

## Summary
- `vibration_30s_20260303_101725.csv`: score=`49.76`，pred=`normal`，stage_hint=`loose_1`，gap=`-0.0053`
- `vibration_30s_20260303_101836.csv`: score=`54.11`，pred=`normal`，stage_hint=`loose_1`，gap=`0.0915`
- `vibration_30s_20260303_104608.csv`: score=`57.14`，pred=`loose`，stage_hint=`loose_2`，gap=`0.1598`
- `vibration_30s_20260303_104650.csv`: skipped，原因：vibration_30s_20260303_104650.csv: too few valid Ax/Ay/Az rows (2)

## Detailed Output
```text
                             file  status  sample_count  duration_s  looseness_score pred_binary     label stage_hint  gap_normal_minus_loose                                                                                                                                                                                                  evidence_top                                                              error
vibration_30s_20260303_101725.csv      ok          11.0      22.937          49.7600      normal uncertain    loose_1                 -0.0053                                                         ax_std(x=0.01368,N=0.009092,L1=0.01342,L2=0.009548); gz_std(x=0.06565,N=0.06097,L1=0.07481,L2=0.06688); gx_std(x=0.1714,N=0.1318,L1=0.1356,L2=0.1122)                                                                   
vibration_30s_20260303_101836.csv      ok          16.0      28.548          54.1067      normal uncertain    loose_1                  0.0915            mag_kurt(x=2.715,N=0.1054,L1=1.821,L2=0.6709); gz_std(x=0.08074,N=0.06097,L1=0.07481,L2=0.06688); ay_std(x=0.0102,N=0.009567,L1=0.01017,L2=0.00555); gx_std(x=0.1206,N=0.1318,L1=0.1356,L2=0.1122)                                                                   
vibration_30s_20260303_104608.csv      ok          13.0      29.422          57.1431       loose     loose    loose_2                  0.1598 az_std(x=0.003993,N=0.008303,L1=0.007065,L2=0.005289); temp_std(x=0.08481,N=0.1289,L1=0.1249,L2=0.09828); gx_std(x=0.08784,N=0.1318,L1=0.1356,L2=0.1122); ax_std(x=0.01257,N=0.009092,L1=0.01342,L2=0.009548)                                                                   
vibration_30s_20260303_104650.csv skipped           NaN         NaN              NaN        None      None       None                     NaN                                                                                                                                                                                                          None vibration_30s_20260303_104650.csv: too few valid Ax/Ay/Az rows (2)
```
