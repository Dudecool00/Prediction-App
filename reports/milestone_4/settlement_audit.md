# Settlement probability audit

The same saved 2023–2024 forecasts and calibration errors are used. Thresholds are fixed diagnostic events, with no historical prices or ROI. This checks the explicit whole-yard rounding approximation added for manual integer/half-yard markets.

## Half-yard probability change

Compare the rounded settlement CDF with Milestone 3's continuous signed-residual tail at 150.5, 200.5, 250.5, and 300.5. Differences can arise at exact boundaries.

| model | n | max_probability_change | mean_probability_change |
| --- | --- | --- | --- |
| prior_five_mean | 5308 | 0.010 | 0.000 |
| ridge | 5308 | 0.000 | 0.000 |
| season_to_date_mean | 5308 | 0.014 | 0.000 |
| xgb_context | 5308 | 0.000 | 0.000 |
| xgb_qb | 5308 | 0.000 | 0.000 |
| xgb_schedule | 5308 | 0.000 | 0.000 |

## Half-yard probability scores

| model | line | season | n | brier | log_loss |
| --- | --- | --- | --- | --- | --- |
| prior_five_mean | 150.500 | 2023 | 663 | 0.178 | 0.547 |
| prior_five_mean | 150.500 | 2024 | 664 | 0.164 | 0.503 |
| prior_five_mean | 200.500 | 2023 | 663 | 0.215 | 0.627 |
| prior_five_mean | 200.500 | 2024 | 664 | 0.223 | 0.643 |
| prior_five_mean | 250.500 | 2023 | 663 | 0.199 | 0.584 |
| prior_five_mean | 250.500 | 2024 | 664 | 0.203 | 0.589 |
| prior_five_mean | 300.500 | 2023 | 663 | 0.133 | 0.426 |
| prior_five_mean | 300.500 | 2024 | 664 | 0.114 | 0.382 |
| ridge | 150.500 | 2023 | 663 | 0.172 | 0.524 |
| ridge | 150.500 | 2024 | 664 | 0.151 | 0.472 |
| ridge | 200.500 | 2023 | 663 | 0.216 | 0.619 |
| ridge | 200.500 | 2024 | 664 | 0.211 | 0.609 |
| ridge | 250.500 | 2023 | 663 | 0.196 | 0.573 |
| ridge | 250.500 | 2024 | 664 | 0.192 | 0.562 |
| ridge | 300.500 | 2023 | 663 | 0.131 | 0.419 |
| ridge | 300.500 | 2024 | 664 | 0.107 | 0.367 |
| season_to_date_mean | 150.500 | 2023 | 663 | 0.174 | 0.538 |
| season_to_date_mean | 150.500 | 2024 | 664 | 0.162 | 0.501 |
| season_to_date_mean | 200.500 | 2023 | 663 | 0.215 | 0.625 |
| season_to_date_mean | 200.500 | 2024 | 664 | 0.226 | 0.657 |
| season_to_date_mean | 250.500 | 2023 | 663 | 0.198 | 0.584 |
| season_to_date_mean | 250.500 | 2024 | 664 | 0.202 | 0.590 |
| season_to_date_mean | 300.500 | 2023 | 663 | 0.131 | 0.422 |
| season_to_date_mean | 300.500 | 2024 | 664 | 0.114 | 0.388 |
| xgb_context | 150.500 | 2023 | 663 | 0.163 | 0.506 |
| xgb_context | 150.500 | 2024 | 664 | 0.145 | 0.463 |
| xgb_context | 200.500 | 2023 | 663 | 0.212 | 0.609 |
| xgb_context | 200.500 | 2024 | 664 | 0.210 | 0.614 |
| xgb_context | 250.500 | 2023 | 663 | 0.191 | 0.558 |
| xgb_context | 250.500 | 2024 | 664 | 0.190 | 0.560 |
| xgb_context | 300.500 | 2023 | 663 | 0.127 | 0.406 |
| xgb_context | 300.500 | 2024 | 664 | 0.107 | 0.368 |
| xgb_qb | 150.500 | 2023 | 663 | 0.166 | 0.512 |
| xgb_qb | 150.500 | 2024 | 664 | 0.149 | 0.471 |
| xgb_qb | 200.500 | 2023 | 663 | 0.211 | 0.608 |
| xgb_qb | 200.500 | 2024 | 664 | 0.211 | 0.613 |
| xgb_qb | 250.500 | 2023 | 663 | 0.195 | 0.567 |
| xgb_qb | 250.500 | 2024 | 664 | 0.192 | 0.563 |
| xgb_qb | 300.500 | 2023 | 663 | 0.129 | 0.409 |
| xgb_qb | 300.500 | 2024 | 664 | 0.106 | 0.367 |
| xgb_schedule | 150.500 | 2023 | 663 | 0.163 | 0.507 |
| xgb_schedule | 150.500 | 2024 | 664 | 0.146 | 0.464 |
| xgb_schedule | 200.500 | 2023 | 663 | 0.209 | 0.604 |
| xgb_schedule | 200.500 | 2024 | 664 | 0.211 | 0.613 |
| xgb_schedule | 250.500 | 2023 | 663 | 0.194 | 0.564 |
| xgb_schedule | 250.500 | 2024 | 664 | 0.191 | 0.561 |
| xgb_schedule | 300.500 | 2023 | 663 | 0.127 | 0.407 |
| xgb_schedule | 300.500 | 2024 | 664 | 0.106 | 0.368 |

## Integer push diagnostics

These rows check 150, 200, 250, and 300 yards. Push mass is sparse and may be zero even when a push occurs. The last column exposes those failures; integer push probabilities are experimental. No conditional or individual-player guarantee is made.

| model | line | n | mean_predicted_push_probability | observed_push_rate | push_brier | pushes_given_zero_probability |
| --- | --- | --- | --- | --- | --- | --- |
| prior_five_mean | 150.000 | 1327 | 0.003 | 0.002 | 0.002 | 0 |
| prior_five_mean | 200.000 | 1327 | 0.004 | 0.005 | 0.005 | 6 |
| prior_five_mean | 250.000 | 1327 | 0.003 | 0.001 | 0.001 | 1 |
| prior_five_mean | 300.000 | 1327 | 0.003 | 0.001 | 0.001 | 0 |
| ridge | 150.000 | 1327 | 0.003 | 0.002 | 0.002 | 1 |
| ridge | 200.000 | 1327 | 0.004 | 0.005 | 0.005 | 4 |
| ridge | 250.000 | 1327 | 0.003 | 0.001 | 0.001 | 1 |
| ridge | 300.000 | 1327 | 0.002 | 0.001 | 0.001 | 1 |
| season_to_date_mean | 150.000 | 1327 | 0.003 | 0.002 | 0.002 | 0 |
| season_to_date_mean | 200.000 | 1327 | 0.004 | 0.005 | 0.005 | 2 |
| season_to_date_mean | 250.000 | 1327 | 0.004 | 0.001 | 0.001 | 0 |
| season_to_date_mean | 300.000 | 1327 | 0.003 | 0.001 | 0.001 | 0 |
| xgb_context | 150.000 | 1327 | 0.003 | 0.002 | 0.002 | 1 |
| xgb_context | 200.000 | 1327 | 0.004 | 0.005 | 0.005 | 2 |
| xgb_context | 250.000 | 1327 | 0.003 | 0.001 | 0.001 | 1 |
| xgb_context | 300.000 | 1327 | 0.002 | 0.001 | 0.001 | 0 |
| xgb_qb | 150.000 | 1327 | 0.003 | 0.002 | 0.002 | 0 |
| xgb_qb | 200.000 | 1327 | 0.004 | 0.005 | 0.005 | 3 |
| xgb_qb | 250.000 | 1327 | 0.003 | 0.001 | 0.001 | 0 |
| xgb_qb | 300.000 | 1327 | 0.002 | 0.001 | 0.001 | 0 |
| xgb_schedule | 150.000 | 1327 | 0.003 | 0.002 | 0.002 | 2 |
| xgb_schedule | 200.000 | 1327 | 0.004 | 0.005 | 0.005 | 1 |
| xgb_schedule | 250.000 | 1327 | 0.003 | 0.001 | 0.001 | 1 |
| xgb_schedule | 300.000 | 1327 | 0.002 | 0.001 | 0.001 | 1 |

Push Brier is mean squared error for the push event; rare events can score well with nearly zero predictions. Assess counts alongside the score. Model inputs, point errors, original interval bounds, and held-out season usage are unchanged.
