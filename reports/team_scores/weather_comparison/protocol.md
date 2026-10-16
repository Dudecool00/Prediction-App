# Archived weather development comparison

Declared October 15, 2026, before the first fits with the NCAR wind/rate inputs. This
follows earlier EPA, temperature and injury results and the completed weather source audit.
It is adaptive development on previously examined seasons, not a new holdout.

1. Reuse the checksum-verified Tuesday-locked EPA table: 2022 initialization, every
   2023–2024 game, separate horizons, Ridge alpha 20 and training-only preprocessing.
   Train once per Tuesday per horizon. Refresh context changes only at kickoff minus one
   hour; no Thursday/Sunday result may update the same week's EPA or coefficients.
   Require exact reproduction of the original compact-EPA predictions. No 2025 outcomes,
   features or fits enter this comparison.
2. Reconstruct only 2022–2024 weather from the preserved NCAR point responses offline.
   Verify source code/policy, schedule/city inputs, response digests, selected run and
   valid time, forecast process, units, grid and sensor heights. Keep the assumed six-hour-
   ten-minute publication delay, approximate cities and static venue registry explicit.
   Retain every game, including unknown roofs, moved venues and the unavailable 2024 file.
3. Compare compact EPA (8 inputs), EPA plus three venue/coverage flags (11), and those
   inputs plus three weather values (14). Flags identify fixed indoor, known outdoor
   exposure and a complete outdoor weather package. Unknown roofs and excluded moved
   venues form the reference category. A missing outdoor package remains explicitly
   distinguishable from indoor or unknown exposure.
4. For a complete outdoor package, use temperature minus 20°C, 10m wind magnitude in m/s
   and instantaneous precipitation rate in mm/h. For every other status, set all three
   weather terms to neutral zero and retain the same three flags in both context models.
   No observed weather, postgame roof, gust, rain/snow classification, nonlinear transform,
   interaction, clipping, penalty search or injury bundle is introduced.
5. Early-week margin is the primary measure. Weather must improve MAE by at least 0.05
   against both compact EPA and flags-only, with a positive approximate 95% season/week
   paired-bootstrap lower bound (5,000 draws, seed 20261015). At either horizon, neither
   overall margin may regress; score MAE may regress at most 0.05 and total MAE at most
   0.10. A single season's margin may regress at most 0.25. The fallback is compact EPA;
   flags-only cannot become the selected research forecast.
6. Select on 2023 only before assessing every 2024 game. Separately record a simulated
   pre-2025 choice from 2023–2024, preserving the original September 2, 2025 15:00 UTC
   lock and actual recording date. All models retain identical game cohorts. Complete
   outdoor games are a labeled secondary sensitivity, not a filtered primary test.
7. Save one context delta table, one score prediction panel, fold IDs/hashes, complexity,
   aggregate metrics and charts. Reuse source data and environments. Verify readers from
   persisted sources and artifacts without refitting. Do not rewrite earlier frozen
   evidence, promote the active model or clear the original enriched-replay gate.

After this test, continue dated roster/starter coverage before a new enriched 2025 replay.
2025 remains previously examined diagnostic data. The PEM-inspired comparison follows
completion of the original plan.
