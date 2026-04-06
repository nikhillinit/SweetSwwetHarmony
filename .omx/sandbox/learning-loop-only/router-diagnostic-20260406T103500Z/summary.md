# Router Diagnostic Rerun

- date: `2026-04-06`
- db_path: `signals.db`
- window_days: `90`
- branch: `no_routing_problem_detected`

## Quality Stats

- labeled: `210`
- decided: `205`
- tp: `19`
- fp: `186`
- unsure: `5`
- adj: `0`
- fp_rate: `0.9073170731707317`

## Join Coverage

- decisive_joined_rows: `205`
- tp_rows: `19`
- fp_rows: `186`
- latest_row_mismatches: `0`

## Discrimination

- auc: `0.9548670062252406`
- tp_mean: `0.8026315789473685`
- fp_mean: `0.1629032258064516`
- mean_separation: `0.6397283531409168`
- score_max: `0.9`
- threshold_0_7: `{"tp": 19, "fp": 29, "fn": 0, "tn": 157}`

## Branch Recommendation

- separation is acceptable and the threshold is reachable
- mean_separation=0.639728
- auc=0.954867
- score_max=0.900000
