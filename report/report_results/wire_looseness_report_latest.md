# Wire Looseness Generic Algorithm Report

- Generated at: 2026-03-03 16:55:47
- Data glob: `data/captures/vibration_30s_20260303_*.csv`
- Label ranges (normal): `16-19,50-57`
- Label ranges (loose_1): `36-41`
- Label ranges (loose_2): `45-48`
- Target hour filter: `10` (`-1` means all hours)
- Decision threshold: `score >= 55.0` => loose, `<= 45.0` => normal

## Algorithm Formula
- Step1: extract window-level vibration features from each 30s file.
- Step2: build three templates from labeled ranges: normal, loose_1, loose_2.
- Step3: for each selected feature, compute scaled distances to all templates.
- Step4: define loose distance as `dist_to_loose = min(dist_to_loose_1, dist_to_loose_2)`.
- Step5: aggregate distance gap `gap = mean(dist_to_normal) - mean(dist_to_loose)`.
- Step6: map to looseness score `score = 100 * sigmoid(1.8 * gap)`.

## Labeled Training Files
```text
                                 file  time_key  minute truth_stage truth_binary
8   vibration_30s_20260303_101624.csv  10:16:24      16      normal       normal
9   vibration_30s_20260303_101650.csv  10:16:50      16      normal       normal
10  vibration_30s_20260303_101725.csv  10:17:25      17      normal       normal
11  vibration_30s_20260303_101836.csv  10:18:36      18      normal       normal
12  vibration_30s_20260303_101912.csv  10:19:12      19      normal       normal
13  vibration_30s_20260303_103711.csv  10:37:11      37     loose_1        loose
14  vibration_30s_20260303_103751.csv  10:37:51      37     loose_1        loose
15  vibration_30s_20260303_103816.csv  10:38:16      38     loose_1        loose
16  vibration_30s_20260303_103912.csv  10:39:12      39     loose_1        loose
17  vibration_30s_20260303_104016.csv  10:40:16      40     loose_1        loose
18  vibration_30s_20260303_104100.csv  10:41:00      41     loose_1        loose
19  vibration_30s_20260303_104530.csv  10:45:30      45     loose_2        loose
20  vibration_30s_20260303_104608.csv  10:46:08      46     loose_2        loose
21  vibration_30s_20260303_104718.csv  10:47:18      47     loose_2        loose
22  vibration_30s_20260303_104806.csv  10:48:06      48     loose_2        loose
23  vibration_30s_20260303_105427.csv  10:54:27      54      normal       normal
24  vibration_30s_20260303_105518.csv  10:55:18      55      normal       normal
25  vibration_30s_20260303_105553.csv  10:55:53      55      normal       normal
26  vibration_30s_20260303_105641.csv  10:56:41      56      normal       normal
27  vibration_30s_20260303_105710.csv  10:57:10      57      normal       normal
```

## Selected Signature Features
```text
    feature  separation  mu_normal  mu_loose   scale  mu_loose_1  mu_loose_2
0  mag_kurt      0.3932     0.1054    1.3609  3.1929      1.8209      0.6709
1    az_std      0.3162     0.0083    0.0064  0.0062      0.0071      0.0053
2    gz_std      0.3124     0.0610    0.0716  0.0341      0.0748      0.0669
3    ax_std      0.2531     0.0091    0.0119  0.0110      0.0134      0.0095
4  temp_std      0.2082     0.1289    0.1143  0.0701      0.1249      0.0983
5    gy_std      0.1771     0.0920    0.0829  0.0515      0.0859      0.0785
6    ay_std      0.1338     0.0096    0.0083  0.0093      0.0102      0.0055
7    gx_std      0.1218     0.1318    0.1262  0.0460      0.1356      0.1122
```

## Validation (On Labeled Files)
- Accuracy: `0.7500` on `20` samples
```text
pred    loose  normal
truth                
loose       7       3
normal      2       8
```

## Scoring Output (All Matched Files)
```text
                                 file  time_key truth_stage truth_binary pred_binary stage_hint      label  looseness_score  gap_normal_minus_loose                                                                                                                                                                                                   evidence_top
0   vibration_30s_20260303_110154.csv  11:01:54        None         None       loose    loose_2      loose          59.9002                  0.2229           mag_kurt(x=4.916,N=0.1054,L1=1.821,L2=0.6709); temp_std(x=0.06229,N=0.1289,L1=0.1249,L2=0.09828); gx_std(x=0.09135,N=0.1318,L1=0.1356,L2=0.1122); ay_std(x=0.00628,N=0.009567,L1=0.01017,L2=0.00555)
1   vibration_30s_20260303_110250.csv  11:02:50        None         None       loose    loose_1      loose          63.0941                  0.2979        mag_kurt(x=3.277,N=0.1054,L1=1.821,L2=0.6709); temp_std(x=0.07554,N=0.1289,L1=0.1249,L2=0.09828); gz_std(x=0.1246,N=0.06097,L1=0.07481,L2=0.06688); ax_std(x=0.01744,N=0.009092,L1=0.01342,L2=0.009548)
2   vibration_30s_20260303_110322.csv  11:03:22        None         None       loose    loose_1      loose          58.0073                  0.1795      temp_std(x=0.07758,N=0.1289,L1=0.1249,L2=0.09828); gz_std(x=0.109,N=0.06097,L1=0.07481,L2=0.06688); ax_std(x=0.0149,N=0.009092,L1=0.01342,L2=0.009548); gy_std(x=0.06824,N=0.09203,L1=0.08585,L2=0.07849)
3   vibration_30s_20260303_110344.csv  11:03:44        None         None      normal    loose_2  uncertain          54.3178                  0.0962      temp_std(x=0.06554,N=0.1289,L1=0.1249,L2=0.09828); ay_std(x=0.004554,N=0.009567,L1=0.01017,L2=0.00555); gz_std(x=0.06857,N=0.06097,L1=0.07481,L2=0.06688); mag_kurt(x=0.5309,N=0.1054,L1=1.821,L2=0.6709)
4   vibration_30s_20260303_110424.csv  11:04:24        None         None       loose    loose_2      loose          55.1493                  0.1148      temp_std(x=0.07387,N=0.1289,L1=0.1249,L2=0.09828); gx_std(x=0.09472,N=0.1318,L1=0.1356,L2=0.1122); ay_std(x=0.005638,N=0.009567,L1=0.01017,L2=0.00555); gy_std(x=0.08454,N=0.09203,L1=0.08585,L2=0.07849)
5   vibration_30s_20260303_110451.csv  11:04:51        None         None      normal    loose_1  uncertain          54.7914                  0.1068       mag_kurt(x=1.729,N=0.1054,L1=1.821,L2=0.6709); temp_std(x=0.08674,N=0.1289,L1=0.1249,L2=0.09828); ax_std(x=0.01253,N=0.009092,L1=0.01342,L2=0.009548); gz_std(x=0.06714,N=0.06097,L1=0.07481,L2=0.06688)
6   vibration_30s_20260303_110543.csv  11:05:43        None         None       loose    loose_1      loose          57.0127                  0.1569        mag_kurt(x=3.467,N=0.1054,L1=1.821,L2=0.6709); gz_std(x=0.07531,N=0.06097,L1=0.07481,L2=0.06688); ax_std(x=0.01554,N=0.009092,L1=0.01342,L2=0.009548); temp_std(x=0.1103,N=0.1289,L1=0.1249,L2=0.09828)
7   vibration_30s_20260303_110622.csv  11:06:22        None         None      normal    loose_1  uncertain          54.5155                  0.1006           mag_kurt(x=2.96,N=0.1054,L1=1.821,L2=0.6709); gy_std(x=0.06766,N=0.09203,L1=0.08585,L2=0.07849); gz_std(x=0.07131,N=0.06097,L1=0.07481,L2=0.06688); temp_std(x=0.1082,N=0.1289,L1=0.1249,L2=0.09828)
8   vibration_30s_20260303_101624.csv  10:16:24      normal       normal      normal    loose_1  uncertain          47.5076                 -0.0554                                                                                                              gx_std(x=0.1186,N=0.1318,L1=0.1356,L2=0.1122); ay_std(x=0.01153,N=0.009567,L1=0.01017,L2=0.00555)
9   vibration_30s_20260303_101650.csv  10:16:50      normal       normal      normal    loose_2  uncertain          49.8164                 -0.0041                                                               gx_std(x=0.116,N=0.1318,L1=0.1356,L2=0.1122); gy_std(x=0.07744,N=0.09203,L1=0.08585,L2=0.07849); temp_std(x=0.115,N=0.1289,L1=0.1249,L2=0.09828)
10  vibration_30s_20260303_101725.csv  10:17:25      normal       normal      normal    loose_1  uncertain          49.7600                 -0.0053                                                          ax_std(x=0.01368,N=0.009092,L1=0.01342,L2=0.009548); gz_std(x=0.06565,N=0.06097,L1=0.07481,L2=0.06688); gx_std(x=0.1714,N=0.1318,L1=0.1356,L2=0.1122)
11  vibration_30s_20260303_101836.csv  10:18:36      normal       normal      normal    loose_1  uncertain          54.1067                  0.0915             mag_kurt(x=2.715,N=0.1054,L1=1.821,L2=0.6709); gz_std(x=0.08074,N=0.06097,L1=0.07481,L2=0.06688); ay_std(x=0.0102,N=0.009567,L1=0.01017,L2=0.00555); gx_std(x=0.1206,N=0.1318,L1=0.1356,L2=0.1122)
12  vibration_30s_20260303_101912.csv  10:19:12      normal       normal       loose    loose_2      loose          61.1158                  0.2512   az_std(x=0.002326,N=0.008303,L1=0.007065,L2=0.005289); temp_std(x=0.06949,N=0.1289,L1=0.1249,L2=0.09828); ay_std(x=0.00102,N=0.009567,L1=0.01017,L2=0.00555); gx_std(x=0.09766,N=0.1318,L1=0.1356,L2=0.1122)
13  vibration_30s_20260303_103711.csv  10:37:11     loose_1        loose      normal    loose_1  uncertain          51.7036                  0.0379        temp_std(x=0.09849,N=0.1289,L1=0.1249,L2=0.09828); gz_std(x=0.07243,N=0.06097,L1=0.07481,L2=0.06688); gx_std(x=0.1403,N=0.1318,L1=0.1356,L2=0.1122); ay_std(x=0.01164,N=0.009567,L1=0.01017,L2=0.00555)
14  vibration_30s_20260303_103751.csv  10:37:51     loose_1        loose       loose    loose_1      loose          62.6553                  0.2875          mag_kurt(x=3.454,N=0.1054,L1=1.821,L2=0.6709); temp_std(x=0.061,N=0.1289,L1=0.1249,L2=0.09828); gz_std(x=0.1224,N=0.06097,L1=0.07481,L2=0.06688); ax_std(x=0.02617,N=0.009092,L1=0.01342,L2=0.009548)
15  vibration_30s_20260303_103816.csv  10:38:16     loose_1        loose       loose    loose_2      loose          58.1460                  0.1826    az_std(x=0.003229,N=0.008303,L1=0.007065,L2=0.005289); gx_std(x=0.09185,N=0.1318,L1=0.1356,L2=0.1122); gz_std(x=0.07527,N=0.06097,L1=0.07481,L2=0.06688); gy_std(x=0.07808,N=0.09203,L1=0.08585,L2=0.07849)
16  vibration_30s_20260303_103912.csv  10:39:12     loose_1        loose      normal    loose_1  uncertain          54.3933                  0.0979            mag_kurt(x=6.01,N=0.1054,L1=1.821,L2=0.6709); ax_std(x=0.02001,N=0.009092,L1=0.01342,L2=0.009548); gx_std(x=0.1181,N=0.1318,L1=0.1356,L2=0.1122); gz_std(x=0.06552,N=0.06097,L1=0.07481,L2=0.06688)
17  vibration_30s_20260303_104016.csv  10:40:16     loose_1        loose      normal    loose_1  uncertain          50.5491                  0.0122        gz_std(x=0.06824,N=0.06097,L1=0.07481,L2=0.06688); mag_kurt(x=0.6495,N=0.1054,L1=1.821,L2=0.6709); ay_std(x=0.01275,N=0.009567,L1=0.01017,L2=0.00555); temp_std(x=0.1174,N=0.1289,L1=0.1249,L2=0.09828)
18  vibration_30s_20260303_104100.csv  10:41:00     loose_1        loose       loose    loose_2      loose          57.2288                  0.1618    az_std(x=0.00444,N=0.008303,L1=0.007065,L2=0.005289); ay_std(x=0.003669,N=0.009567,L1=0.01017,L2=0.00555); gy_std(x=0.04795,N=0.09203,L1=0.08585,L2=0.07849); mag_kurt(x=1.304,N=0.1054,L1=1.821,L2=0.6709)
19  vibration_30s_20260303_104530.csv  10:45:30     loose_2        loose       loose    loose_2      loose          60.0874                  0.2273   temp_std(x=0.08778,N=0.1289,L1=0.1249,L2=0.09828); ay_std(x=0.003408,N=0.009567,L1=0.01017,L2=0.00555); gz_std(x=0.07364,N=0.06097,L1=0.07481,L2=0.06688); gy_std(x=0.07364,N=0.09203,L1=0.08585,L2=0.07849)
20  vibration_30s_20260303_104608.csv  10:46:08     loose_2        loose       loose    loose_2      loose          57.1431                  0.1598  az_std(x=0.003993,N=0.008303,L1=0.007065,L2=0.005289); temp_std(x=0.08481,N=0.1289,L1=0.1249,L2=0.09828); gx_std(x=0.08784,N=0.1318,L1=0.1356,L2=0.1122); ax_std(x=0.01257,N=0.009092,L1=0.01342,L2=0.009548)
21  vibration_30s_20260303_104718.csv  10:47:18     loose_2        loose       loose    loose_1      loose          59.4425                  0.2124     mag_kurt(x=2.498,N=0.1054,L1=1.821,L2=0.6709); ay_std(x=0.005613,N=0.009567,L1=0.01017,L2=0.00555); gz_std(x=0.08667,N=0.06097,L1=0.07481,L2=0.06688); ax_std(x=0.01519,N=0.009092,L1=0.01342,L2=0.009548)
22  vibration_30s_20260303_104806.csv  10:48:06     loose_2        loose       loose    loose_2      loose          59.2057                  0.2069    az_std(x=0.004271,N=0.008303,L1=0.007065,L2=0.005289); temp_std(x=0.08227,N=0.1289,L1=0.1249,L2=0.09828); ay_std(x=0.003336,N=0.009567,L1=0.01017,L2=0.00555); gx_std(x=0.109,N=0.1318,L1=0.1356,L2=0.1122)
23  vibration_30s_20260303_105427.csv  10:54:27      normal       normal       loose    loose_1      loose          57.0165                  0.1570   ay_std(x=0.005249,N=0.009567,L1=0.01017,L2=0.00555); gz_std(x=0.07866,N=0.06097,L1=0.07481,L2=0.06688); mag_kurt(x=1.404,N=0.1054,L1=1.821,L2=0.6709); az_std(x=0.006231,N=0.008303,L1=0.007065,L2=0.005289)
24  vibration_30s_20260303_105518.csv  10:55:18      normal       normal      normal    loose_2  uncertain          54.4214                  0.0985   az_std(x=0.002467,N=0.008303,L1=0.007065,L2=0.005289); ay_std(x=0.004376,N=0.009567,L1=0.01017,L2=0.00555); gz_std(x=0.06886,N=0.06097,L1=0.07481,L2=0.06688); gx_std(x=0.1388,N=0.1318,L1=0.1356,L2=0.1122)
25  vibration_30s_20260303_105553.csv  10:55:53      normal       normal      normal    loose_1  uncertain          52.9619                  0.0659  temp_std(x=0.08749,N=0.1289,L1=0.1249,L2=0.09828); ax_std(x=0.01353,N=0.009092,L1=0.01342,L2=0.009548); gy_std(x=0.07612,N=0.09203,L1=0.08585,L2=0.07849); ay_std(x=0.01544,N=0.009567,L1=0.01017,L2=0.00555)
26  vibration_30s_20260303_105641.csv  10:56:41      normal       normal      normal    loose_1  uncertain          50.0531                  0.0012      ax_std(x=0.01342,N=0.009092,L1=0.01342,L2=0.009548); gx_std(x=0.1442,N=0.1318,L1=0.1356,L2=0.1122); ay_std(x=0.01748,N=0.009567,L1=0.01017,L2=0.00555); gz_std(x=0.06434,N=0.06097,L1=0.07481,L2=0.06688)
27  vibration_30s_20260303_105710.csv  10:57:10      normal       normal      normal    loose_1  uncertain          51.3873                  0.0308       gy_std(x=0.06041,N=0.09203,L1=0.08585,L2=0.07849); ax_std(x=0.01269,N=0.009092,L1=0.01342,L2=0.009548); gx_std(x=0.151,N=0.1318,L1=0.1356,L2=0.1122); ay_std(x=0.01362,N=0.009567,L1=0.01017,L2=0.00555)
```