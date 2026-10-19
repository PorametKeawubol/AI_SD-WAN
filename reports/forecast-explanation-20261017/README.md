# Inspecting the saved packet-link forecaster

This report explains the already frozen `model-search-link-20261017` candidate. It does not retrain or promote it. `scripts/explain_forecaster.py` checks the dataset fingerprint and original holdout episode IDs before producing plots. It verifies that the exact tree contributions sum to each saved XGBoost residual prediction before clipping.

The largest mean absolute RTT residual contribution is `jitter_ms_current` (16.740 ms), followed by the observed RTT trend (5.949 ms) and RTT standard deviation (4.266 ms). Jitter uses its current value, lag 3 and RTT trend. Utilization uses its current value, lag 1 and jitter lag 3. These are descriptive TreeSHAP contributions on the held-out windows, not causal effects. The NetEm workload changes metrics together, so reliance on their correlation needs confirmation in different workloads through flexiEdge.

The model forecasts a change from the current metric using 26 features from 12 samples. It therefore does more than copy the latest value for the three accepted metrics. Loss deliberately remains persistence because its acceptance confidence interval crosses zero. The plots retain the first predeclared holdout episode and WAN1, including intervals where the optimized forecast overshoots.

`forecast-trace.png` includes all four metrics; `forecast-trace-compact.png` shows RTT and utilization for a legible two-page appendix. Both compare the prediction made at time t with the measured target at t+30, using the actual target timestamps. `feature-contributions.png` shows the largest residual contributions. All eligible holdout windows enter the contribution summary. No holdout-based selection or parameter changes were made.

Model SHA256: `2d3c3fc2cd27419da0977af36d6d0943afbf50ed54a5be9f1bb16ba78695b6eb`

Dataset SHA256: `a53352de7f4c166167f7a4b9be1e983555f21c7c655ab9e4475833ee853d7e50`
