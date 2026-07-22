import unittest
from collections.abc import Mapping, Sequence

from prompt_performance_engine.frontier_statistics import (
    aggregate_case_safety_failures,
    case_cluster_bootstrap_interval,
    cost_efficiency_summary,
    domain_macro_cluster_bootstrap_interval,
    exact_one_sided_sign_p_value,
    exact_binomial_risk_upper_bound,
    holm_bonferroni,
    latency_percentiles,
    linear_quantile,
    paired_superiority_diagnostic,
    pareto_nondominated_ids,
    quality_distribution_summary,
    robustness_pair_summary,
    wilson_interval,
)


class _OversizedMapping(Mapping):
    """Advertise an invalid size and fail if validation tries to enumerate it."""

    def __init__(self, size):
        self._size = size

    def __getitem__(self, key):
        raise AssertionError("oversized mappings must fail before item access")

    def __iter__(self):
        raise AssertionError("oversized mappings must fail before iteration")

    def __len__(self):
        return self._size


class _TruthyEmptySequence(Sequence):
    def __bool__(self):
        return True

    def __getitem__(self, index):
        raise IndexError(index)

    def __len__(self):
        return 0


class FrontierStatisticsTests(unittest.TestCase):
    def test_historical_result_is_not_statistically_superior(self):
        result = paired_superiority_diagnostic(wins=30, ties=9, losses=21)

        self.assertAlmostEqual(result["net_improvement"], 0.15)
        self.assertAlmostEqual(
            result["one_sided_sign_test_p_value"],
            0.1312187678555219,
        )
        self.assertLess(result["non_tie_wilson_interval"]["lower"], 0.5)
        self.assertGreater(result["non_tie_wilson_interval"]["upper"], 0.5)
        self.assertTrue(result["point_margin_passed"])
        self.assertFalse(result["statistical_superiority_passed"])
        self.assertFalse(result["diagnostic_gate_passed"])

    def test_clear_paired_advantage_passes_one_comparison(self):
        result = paired_superiority_diagnostic(wins=80, ties=10, losses=10)

        self.assertTrue(result["point_margin_passed"])
        self.assertTrue(result["statistical_superiority_passed"])
        self.assertTrue(result["diagnostic_gate_passed"])

    def test_all_ties_are_not_superiority(self):
        result = paired_superiority_diagnostic(wins=0, ties=60, losses=0)

        self.assertEqual(result["one_sided_sign_test_p_value"], 1.0)
        self.assertEqual(result["non_tie_wilson_interval"]["lower"], 0.0)
        self.assertEqual(result["non_tie_wilson_interval"]["upper"], 1.0)
        self.assertFalse(result["diagnostic_gate_passed"])

    def test_sign_test_and_wilson_known_small_sample(self):
        self.assertEqual(
            exact_one_sided_sign_p_value(wins=5, losses=0),
            0.03125,
        )
        lower, upper = wilson_interval(successes=5, trials=5)
        self.assertAlmostEqual(lower, 0.5655175352168251)
        self.assertEqual(upper, 1.0)

    def test_wilson_rejects_impossible_and_unbounded_counts(self):
        with self.assertRaisesRegex(ValueError, "successes must not exceed trials"):
            wilson_interval(successes=1, trials=0)
        with self.assertRaises(ValueError):
            wilson_interval(successes=2, trials=1)
        with self.assertRaisesRegex(ValueError, "diagnostic limit"):
            wilson_interval(successes=1, trials=10**5000)
        self.assertEqual(
            wilson_interval(successes=0, trials=0),
            (0.0, 1.0),
        )

    def test_paired_diagnostic_has_bounded_counts_and_alpha(self):
        with self.assertRaisesRegex(ValueError, "diagnostic limit"):
            paired_superiority_diagnostic(
                wins=0,
                ties=10**5000,
                losses=0,
            )
        with self.assertRaisesRegex(ValueError, "maximum_p_value"):
            paired_superiority_diagnostic(
                wins=1,
                ties=0,
                losses=0,
                maximum_p_value=1.0,
            )
        self.assertFalse(
            paired_superiority_diagnostic(
                wins=1,
                ties=0,
                losses=0,
            )["diagnostic_gate_passed"]
        )
        self.assertEqual(
            paired_superiority_diagnostic(
                wins=0,
                ties=10_000,
                losses=0,
            )["case_count"],
            10_000,
        )

    def test_quality_summary_uses_equal_domain_macro_and_bottom_cvar(self):
        result = quality_distribution_summary(
            {
                "domain-a": [0.4, 0.2],
                "domain-b": [-0.2],
                "domain-c": [0.1, 0.1, 0.1],
            },
            bottom_fraction=0.5,
        )

        self.assertAlmostEqual(result["micro_delta"], 0.7 / 6)
        self.assertAlmostEqual(result["macro_delta"], (0.3 - 0.2 + 0.1) / 3)
        self.assertEqual(result["worst_domain"], "domain-b")
        self.assertEqual(result["bottom_domains"], ["domain-b", "domain-c"])
        self.assertAlmostEqual(result["bottom_cvar_delta"], -0.05)

    def test_case_cluster_bootstrap_is_deterministic_and_counts_cases(self):
        observations = {
            "case-a": [0.2, 0.4, 0.3],
            "case-b": [-0.1, 0.1],
            "case-c": [0.5],
        }
        first = case_cluster_bootstrap_interval(
            observations,
            iterations=500,
            seed=42,
        )
        second = case_cluster_bootstrap_interval(
            dict(reversed(list(observations.items()))),
            iterations=500,
            seed=42,
        )

        self.assertEqual(first, second)
        self.assertEqual(first["independent_case_count"], 3)
        self.assertEqual(first["replicate_observation_count"], 6)
        self.assertLessEqual(first["lower"], first["point_estimate"])
        self.assertGreaterEqual(first["upper"], first["point_estimate"])

    def test_domain_macro_bootstrap_preserves_equal_domain_weight(self):
        observations = {
            "domain-a": {
                "a-1": [1.0],
                "a-2": [1.0],
            },
            "domain-b": {
                f"b-{index}": [-1.0]
                for index in range(18)
            },
        }

        macro = domain_macro_cluster_bootstrap_interval(
            observations,
            iterations=200,
            seed=42,
        )
        micro = case_cluster_bootstrap_interval(
            {
                case_id: deltas
                for cases in observations.values()
                for case_id, deltas in cases.items()
            },
            iterations=200,
            seed=42,
        )

        self.assertEqual(macro["point_estimate"], 0.0)
        self.assertEqual(macro["lower"], 0.0)
        self.assertEqual(macro["upper"], 0.0)
        self.assertAlmostEqual(micro["point_estimate"], -0.8)

    def test_domain_macro_bootstrap_is_order_stable_and_counts_clusters(self):
        observations = {
            "domain-a": {
                "a-1": [0.2, 0.4, 0.3],
                "a-2": [-0.1],
            },
            "domain-b": {
                "b-1": [0.5, 0.4],
                "b-2": [0.1],
            },
        }
        reversed_observations = {
            domain_id: dict(reversed(list(cases.items())))
            for domain_id, cases in reversed(list(observations.items()))
        }

        first = domain_macro_cluster_bootstrap_interval(
            observations,
            iterations=500,
            seed=73,
        )
        second = domain_macro_cluster_bootstrap_interval(
            reversed_observations,
            iterations=500,
            seed=73,
        )

        self.assertEqual(first, second)
        self.assertEqual(first["domain_count"], 2)
        self.assertEqual(first["independent_case_count"], 4)
        self.assertEqual(first["replicate_observation_count"], 7)

    def test_domain_macro_bootstrap_fails_closed_on_invalid_clusters(self):
        with self.assertRaisesRegex(ValueError, "two independent case clusters"):
            domain_macro_cluster_bootstrap_interval(
                {"domain-a": {"only-case": [0.1]}},
            )
        with self.assertRaisesRegex(ValueError, "globally unique"):
            domain_macro_cluster_bootstrap_interval(
                {
                    "domain-a": {"shared": [0.1], "a-2": [0.2]},
                    "domain-b": {"shared": [0.3], "b-2": [0.4]},
                },
            )
        with self.assertRaisesRegex(ValueError, "bootstrap draw limit"):
            domain_macro_cluster_bootstrap_interval(
                {
                    "domain-a": {
                        f"a-{index}": [0.1]
                        for index in range(500)
                    },
                    "domain-b": {
                        f"b-{index}": [0.1]
                        for index in range(500)
                    },
                },
                iterations=5_001,
            )

    def test_bootstrap_size_limits_fail_before_sorting_oversized_mappings(self):
        with self.assertRaisesRegex(ValueError, "independent case count"):
            case_cluster_bootstrap_interval(_OversizedMapping(10_001))
        with self.assertRaisesRegex(ValueError, "domain count"):
            domain_macro_cluster_bootstrap_interval(_OversizedMapping(5_001))
        with self.assertRaisesRegex(ValueError, "total independent case count"):
            domain_macro_cluster_bootstrap_interval(
                {"domain-a": _OversizedMapping(10_001)}
            )

    def test_exact_risk_upper_bound_handles_zero_and_missing_trials(self):
        self.assertAlmostEqual(
            exact_binomial_risk_upper_bound(failures=0, trials=60),
            1.0 - 0.05 ** (1.0 / 60),
        )
        with self.assertRaisesRegex(ValueError, "trials must be greater than zero"):
            exact_binomial_risk_upper_bound(failures=0, trials=0)
        with self.assertRaisesRegex(ValueError, "failures must not exceed trials"):
            exact_binomial_risk_upper_bound(failures=1, trials=0)
        with self.assertRaisesRegex(ValueError, "at least 0.5"):
            exact_binomial_risk_upper_bound(
                failures=0,
                trials=10,
                confidence_level=1e-20,
            )
        self.assertAlmostEqual(
            exact_binomial_risk_upper_bound(failures=1, trials=10),
            0.39416330243650477,
        )

    def test_safety_replicates_collapse_to_one_any_failure_unit_per_case(self):
        base = aggregate_case_safety_failures(
            {
                "case-a": [False],
                "case-b": [True],
            }
        )
        repeated = aggregate_case_safety_failures(
            {
                "case-a": [False, False, False],
                "case-b": [False, True, False, True],
            }
        )

        self.assertEqual(base["independent_case_count"], 2)
        self.assertEqual(repeated["independent_case_count"], 2)
        self.assertEqual(base["failed_case_count"], 1)
        self.assertEqual(repeated["failed_case_count"], 1)
        self.assertEqual(repeated["replicate_observation_count"], 7)
        self.assertEqual(repeated["failed_case_ids"], ("case-b",))

    def test_safety_case_aggregation_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "non-empty mapping"):
            aggregate_case_safety_failures({})
        with self.assertRaisesRegex(ValueError, "only booleans"):
            aggregate_case_safety_failures({"case-a": [0]})
        with self.assertRaisesRegex(ValueError, "non-empty sequence"):
            aggregate_case_safety_failures({"case-a": []})
        with self.assertRaisesRegex(ValueError, "observation count"):
            aggregate_case_safety_failures(
                {
                    "case-a": [False] * 10_000,
                    "case-b": [False],
                }
            )
        with self.assertRaisesRegex(ValueError, "independent case count"):
            aggregate_case_safety_failures(_OversizedMapping(10_001))
        with self.assertRaisesRegex(ValueError, "non-empty sequence"):
            aggregate_case_safety_failures(
                {"case-a": _TruthyEmptySequence()}
            )
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            latency_percentiles(_TruthyEmptySequence())

    def test_robustness_pairs_report_retention_and_worst_degradation(self):
        result = robustness_pair_summary(
            {
                "format-noise": {
                    "clean_utility": 0.8,
                    "perturbed_utility": 0.6,
                },
                "unicode": {
                    "clean_utility": 0.5,
                    "perturbed_utility": 0.45,
                },
            }
        )

        self.assertEqual(result["worst_pair"], "format-noise")
        self.assertAlmostEqual(result["worst_degradation"], 0.2)
        self.assertAlmostEqual(result["minimum_retention"], 0.75)

    def test_cost_summary_charges_all_calls_and_fails_closed_without_success(self):
        result = cost_efficiency_summary(
            [0.10, 0.20, 0.30],
            successful_terminal_cases=2,
            terminal_case_count=3,
        )
        self.assertEqual(result["charged_call_count"], 3)
        self.assertAlmostEqual(result["total_cost"], 0.60)
        self.assertAlmostEqual(result["expected_cost_per_success"], 0.30)

        no_success = cost_efficiency_summary(
            [0.25],
            successful_terminal_cases=0,
            terminal_case_count=1,
        )
        self.assertIsNone(no_success["expected_cost_per_success"])
        self.assertFalse(no_success["success_cost_evaluable"])

    def test_latency_quantiles_use_type_seven_and_keep_terminal_calls(self):
        self.assertEqual(linear_quantile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)
        result = latency_percentiles([1.0, 2.0, 3.0, 4.0])
        self.assertEqual(result["count"], 4)
        self.assertAlmostEqual(result["p95_ms"], 3.85)
        self.assertAlmostEqual(result["p99_ms"], 3.97)

    def test_pareto_frontier_rejects_strictly_dominated_candidate(self):
        frontier = pareto_nondominated_ids(
            {
                "balanced": {
                    "quality": 0.90,
                    "cost": 2.0,
                    "p95_latency_ms": 100.0,
                },
                "dominated": {
                    "quality": 0.80,
                    "cost": 3.0,
                    "p95_latency_ms": 110.0,
                },
                "quality": {
                    "quality": 0.95,
                    "cost": 4.0,
                    "p95_latency_ms": 90.0,
                },
            }
        )

        self.assertEqual(frontier, ("balanced", "quality"))

    def test_frontier_metric_helpers_fail_closed(self):
        with self.assertRaises(ValueError):
            quality_distribution_summary({"domain": [1.1]})
        with self.assertRaises(ValueError):
            case_cluster_bootstrap_interval({"only-case": [0.1]})
        with self.assertRaises(ValueError):
            latency_percentiles([1.0, float("nan")])
        with self.assertRaises(ValueError):
            pareto_nondominated_ids(
                {"candidate": {"quality": 0.9, "cost": 1.0}}
            )
        with self.assertRaises(ValueError):
            robustness_pair_summary(
                {"pair": {"clean_utility": 0.5}}
            )
        with self.assertRaisesRegex(ValueError, "retention must be finite"):
            robustness_pair_summary(
                {
                    "pair": {
                        "clean_utility": 5e-324,
                        "perturbed_utility": 1.0,
                    }
                }
            )
        with self.assertRaisesRegex(ValueError, "total charged cost"):
            cost_efficiency_summary(
                [1e308, 1e308],
                successful_terminal_cases=1,
                terminal_case_count=1,
            )
        with self.assertRaises(ValueError):
            quality_distribution_summary({1: [0.1], "domain": [0.2]})
        with self.assertRaisesRegex(ValueError, "bootstrap draw limit"):
            case_cluster_bootstrap_interval(
                {f"case-{index}": [0.1] for index in range(1_001)},
                iterations=5_000,
            )
        with self.assertRaisesRegex(ValueError, "Pareto diagnostic limit"):
            pareto_nondominated_ids(
                {
                    f"candidate-{index}": {
                        "quality": 0.5,
                        "cost": 1.0,
                        "p95_latency_ms": 1.0,
                    }
                    for index in range(1_001)
                }
            )

    def test_holm_bonferroni_is_step_down_and_preserves_order(self):
        self.assertEqual(
            holm_bonferroni([0.06, 0.001, 0.02]),
            (False, True, True),
        )

    def test_statistics_reject_boolean_and_malformed_inputs(self):
        for kwargs in (
            {"wins": True, "ties": 0, "losses": 0},
            {"wins": 0, "ties": 0, "losses": 0},
            {"wins": 1, "ties": 0, "losses": 0, "maximum_p_value": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                paired_superiority_diagnostic(**kwargs)
        with self.assertRaises(ValueError):
            holm_bonferroni([])
        with self.assertRaises(ValueError):
            holm_bonferroni([0.01, float("nan")])
        with self.assertRaisesRegex(ValueError, "diagnostic limit"):
            holm_bonferroni([0.01] * 10_001)
        with self.assertRaises(ValueError):
            exact_one_sided_sign_p_value(wins=10_001, losses=0)
        with self.assertRaises(ValueError):
            paired_superiority_diagnostic(
                wins=1,
                ties=0,
                losses=0,
                minimum_net_improvement=10**5000,
            )
