"""Dependency-free statistics for scoped frontier-performance comparisons.

These helpers are diagnostic building blocks. They do not grant an evidence
level or a frontier claim; campaign binding, strong baselines, sealed data,
independent evaluation, and authority replay remain separate requirements.
"""

from __future__ import annotations

import math
import random
from statistics import NormalDist
from typing import Any, Mapping, Sequence


MAX_DIAGNOSTIC_CASES = 10_000
MAX_EXACT_SIGN_TRIALS = MAX_DIAGNOSTIC_CASES
MIN_BOOTSTRAP_ITERATIONS = 100
MAX_BOOTSTRAP_ITERATIONS = 10_000
MAX_BOOTSTRAP_DRAWS = 5_000_000
MAX_PARETO_CANDIDATES = 1_000


def _strict_count(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer.")
    if value > MAX_DIAGNOSTIC_CASES:
        raise ValueError(
            f"{label} exceeds the {MAX_DIAGNOSTIC_CASES}-case diagnostic limit."
        )
    return value


def _probability(value: Any, *, label: str, open_lower: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite probability.")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite probability.") from exc
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{label} must be a finite probability.")
    if open_lower and normalized == 0.0:
        raise ValueError(f"{label} must be greater than zero.")
    return normalized


def _finite_number(
    value: Any,
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number.")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number.") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{label} must be a finite number.")
    if minimum is not None and normalized < minimum:
        raise ValueError(f"{label} must be at least {minimum}.")
    if maximum is not None and normalized > maximum:
        raise ValueError(f"{label} must be at most {maximum}.")
    return normalized


def _finite_sequence(
    values: Sequence[float],
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{label} must be a sequence of finite numbers.")
    value_count = len(values)
    if value_count == 0:
        raise ValueError(f"{label} must not be empty.")
    if value_count > MAX_DIAGNOSTIC_CASES:
        raise ValueError(
            f"{label} exceeds the {MAX_DIAGNOSTIC_CASES}-value diagnostic limit."
        )
    try:
        bounded_values = tuple(values[index] for index in range(value_count))
    except (IndexError, KeyError, TypeError) as exc:
        raise ValueError(f"{label} has an inconsistent sequence length.") from exc
    return tuple(
        _finite_number(
            value,
            label=f"{label}[{index}]",
            minimum=minimum,
            maximum=maximum,
        )
        for index, value in enumerate(bounded_values)
    )


def _linear_quantile_from_sorted(values: Sequence[float], probability: float) -> float:
    if len(values) == 1:
        return values[0]
    position = probability * (len(values) - 1)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return values[lower_index]
    weight = position - lower_index
    return values[lower_index] * (1.0 - weight) + values[upper_index] * weight


def linear_quantile(values: Sequence[float], probability: float) -> float:
    """Return the deterministic Type-7 linear sample quantile."""

    normalized = sorted(_finite_sequence(values, label="values"))
    probability = _probability(probability, label="probability")
    return _linear_quantile_from_sorted(normalized, probability)


def exact_one_sided_sign_p_value(*, wins: int, losses: int) -> float:
    """Return P[X >= wins] for X~Binomial(wins+losses, 0.5).

    Ties are deliberately excluded. The result tests the directional null that
    wins are no more likely than losses for one paired comparison.
    """

    wins = _strict_count(wins, label="wins")
    losses = _strict_count(losses, label="losses")
    non_ties = wins + losses
    if non_ties == 0:
        return 1.0
    if non_ties > MAX_EXACT_SIGN_TRIALS:
        raise ValueError(
            "wins plus losses exceeds the exact sign-test diagnostic limit."
        )
    numerator = sum(
        math.comb(non_ties, value) for value in range(wins, non_ties + 1)
    )
    return numerator / (1 << non_ties)


def wilson_interval(
    *,
    successes: int,
    trials: int,
    confidence_level: float = 0.95,
) -> tuple[float, float]:
    """Return a two-sided Wilson score interval for a binomial proportion."""

    successes = _strict_count(successes, label="successes")
    trials = _strict_count(trials, label="trials")
    confidence_level = _probability(
        confidence_level,
        label="confidence_level",
        open_lower=True,
    )
    if confidence_level >= 1.0:
        raise ValueError("confidence_level must be less than one.")
    if successes > trials:
        raise ValueError("successes must not exceed trials.")
    if trials == 0:
        return (0.0, 1.0)
    z = NormalDist().inv_cdf(0.5 + confidence_level / 2.0)
    proportion = successes / trials
    z_squared = z * z
    denominator = 1.0 + z_squared / trials
    center = (proportion + z_squared / (2.0 * trials)) / denominator
    radius = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / trials
            + z_squared / (4.0 * trials * trials)
        )
        / denominator
    )
    return (max(0.0, center - radius), min(1.0, center + radius))


def paired_superiority_diagnostic(
    *,
    wins: int,
    ties: int,
    losses: int,
    minimum_net_improvement: float = 0.10,
    maximum_p_value: float = 0.05,
    confidence_level: float = 0.95,
) -> dict[str, Any]:
    """Compute one comparison's point estimate and uncertainty diagnostics."""

    wins = _strict_count(wins, label="wins")
    ties = _strict_count(ties, label="ties")
    losses = _strict_count(losses, label="losses")
    if isinstance(minimum_net_improvement, bool) or not isinstance(
        minimum_net_improvement,
        (int, float),
    ):
        raise ValueError("minimum_net_improvement must be a finite number.")
    try:
        minimum_net_improvement = float(minimum_net_improvement)
    except (OverflowError, ValueError) as exc:
        raise ValueError(
            "minimum_net_improvement must be a finite number."
        ) from exc
    if (
        not math.isfinite(minimum_net_improvement)
        or not -1.0 <= minimum_net_improvement <= 1.0
    ):
        raise ValueError("minimum_net_improvement must be between -1 and 1.")
    maximum_p_value = _probability(
        maximum_p_value,
        label="maximum_p_value",
        open_lower=True,
    )
    confidence_level = _probability(
        confidence_level,
        label="confidence_level",
        open_lower=True,
    )
    if maximum_p_value >= 1.0:
        raise ValueError("maximum_p_value must be less than one.")
    if confidence_level >= 1.0:
        raise ValueError("confidence_level must be less than one.")
    case_count = wins + ties + losses
    if case_count == 0:
        raise ValueError("At least one paired outcome is required.")
    if case_count > MAX_DIAGNOSTIC_CASES:
        raise ValueError(
            "paired outcome count exceeds the "
            f"{MAX_DIAGNOSTIC_CASES}-case diagnostic limit."
        )
    non_tie_count = wins + losses
    net_improvement = (wins - losses) / case_count
    adjusted_win_rate = (wins + 0.5 * ties) / case_count
    sign_p_value = exact_one_sided_sign_p_value(wins=wins, losses=losses)
    interval = wilson_interval(
        successes=wins,
        trials=non_tie_count,
        confidence_level=confidence_level,
    )
    point_margin_passed = net_improvement >= minimum_net_improvement
    significance_passed = (
        non_tie_count > 0
        and wins > losses
        and sign_p_value <= maximum_p_value
    )
    return {
        "case_count": case_count,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "non_tie_count": non_tie_count,
        "net_improvement": net_improvement,
        "adjusted_win_rate": adjusted_win_rate,
        "non_tie_win_rate": wins / non_tie_count if non_tie_count else None,
        "one_sided_sign_test_p_value": sign_p_value,
        "non_tie_wilson_interval": {
            "confidence_level": confidence_level,
            "lower": interval[0],
            "upper": interval[1],
        },
        "minimum_net_improvement": minimum_net_improvement,
        "maximum_p_value": maximum_p_value,
        "point_margin_passed": point_margin_passed,
        "statistical_superiority_passed": significance_passed,
        "diagnostic_gate_passed": point_margin_passed and significance_passed,
        "limitations": [
            "The sign test excludes ties and does not measure effect magnitude.",
            "The Wilson interval covers the non-tie win proportion only.",
            "Multiple domains require a separate multiplicity correction.",
            "This diagnostic does not prove sealed data, baseline strength, "
            "or authority.",
        ],
    }


def holm_bonferroni(
    p_values: Sequence[float],
    *,
    family_alpha: float = 0.05,
) -> tuple[bool, ...]:
    """Return step-down Holm rejection decisions in original input order."""

    if isinstance(p_values, (str, bytes)) or not isinstance(p_values, Sequence):
        raise ValueError("p_values must be a sequence of probabilities.")
    if not p_values:
        raise ValueError("p_values must not be empty.")
    if len(p_values) > MAX_DIAGNOSTIC_CASES:
        raise ValueError("p_values exceeds the diagnostic limit.")
    family_alpha = _probability(
        family_alpha,
        label="family_alpha",
        open_lower=True,
    )
    if family_alpha >= 1.0:
        raise ValueError("family_alpha must be less than one.")
    normalized = [
        _probability(value, label=f"p_values[{index}]")
        for index, value in enumerate(p_values)
    ]
    ordered = sorted(enumerate(normalized), key=lambda item: (item[1], item[0]))
    decisions = [False] * len(normalized)
    for rank, (index, p_value) in enumerate(ordered):
        threshold = family_alpha / (len(ordered) - rank)
        if p_value > threshold:
            break
        decisions[index] = True
    return tuple(decisions)


def quality_distribution_summary(
    domain_case_deltas: Mapping[str, Sequence[float]],
    *,
    bottom_fraction: float = 0.20,
) -> dict[str, Any]:
    """Summarize normalized paired utility deltas across equal-weight domains.

    Each utility score must be normalized to [0, 1] before subtraction, so each
    supplied delta is in [-1, 1]. Bottom-CVaR is the arithmetic mean of the
    lowest ceil(domain_count * bottom_fraction) domain means.
    """

    if not isinstance(domain_case_deltas, Mapping) or not domain_case_deltas:
        raise ValueError("domain_case_deltas must be a non-empty mapping.")
    if len(domain_case_deltas) > MAX_DIAGNOSTIC_CASES:
        raise ValueError("domain count exceeds the diagnostic limit.")
    bottom_fraction = _probability(
        bottom_fraction,
        label="bottom_fraction",
        open_lower=True,
    )
    domain_means: dict[str, float] = {}
    all_deltas: list[float] = []
    if any(
        not isinstance(domain, str) or not domain.strip()
        for domain in domain_case_deltas
    ):
        raise ValueError("domain ids must be non-empty strings.")
    for domain in sorted(domain_case_deltas):
        raw_deltas = domain_case_deltas[domain]
        deltas = _finite_sequence(
            raw_deltas,
            label=f"domain_case_deltas[{domain!r}]",
            minimum=-1.0,
            maximum=1.0,
        )
        all_deltas.extend(deltas)
        if len(all_deltas) > MAX_DIAGNOSTIC_CASES:
            raise ValueError("total case count exceeds the diagnostic limit.")
        domain_means[domain] = sum(deltas) / len(deltas)
    ordered_domains = sorted(domain_means, key=lambda item: (domain_means[item], item))
    bottom_count = max(1, math.ceil(len(ordered_domains) * bottom_fraction))
    bottom_domains = ordered_domains[:bottom_count]
    return {
        "domain_count": len(domain_means),
        "case_count": len(all_deltas),
        "domain_means": domain_means,
        "micro_delta": sum(all_deltas) / len(all_deltas),
        "macro_delta": sum(domain_means.values()) / len(domain_means),
        "worst_domain": ordered_domains[0],
        "worst_domain_delta": domain_means[ordered_domains[0]],
        "bottom_fraction": bottom_fraction,
        "bottom_domain_count": bottom_count,
        "bottom_domains": bottom_domains,
        "bottom_cvar_delta": (
            sum(domain_means[domain] for domain in bottom_domains) / bottom_count
        ),
    }


def case_cluster_bootstrap_interval(
    case_replicate_deltas: Mapping[str, Sequence[float]],
    *,
    confidence_level: float = 0.95,
    iterations: int = 2_000,
    seed: int = 0,
) -> dict[str, Any]:
    """Bootstrap a single-domain or preregistered micro equal-case mean.

    Replicate calls for one case are averaged before resampling. They therefore
    never increase the independent case count. Use
    :func:`domain_macro_cluster_bootstrap_interval` for an equal-domain macro
    estimand when domains contain different numbers of cases.
    """

    if (
        not isinstance(case_replicate_deltas, Mapping)
        or not case_replicate_deltas
    ):
        raise ValueError("case_replicate_deltas must be a non-empty mapping.")
    if len(case_replicate_deltas) > MAX_DIAGNOSTIC_CASES:
        raise ValueError("independent case count exceeds the diagnostic limit.")
    confidence_level = _probability(
        confidence_level,
        label="confidence_level",
        open_lower=True,
    )
    if confidence_level >= 1.0:
        raise ValueError("confidence_level must be less than one.")
    iterations = _strict_count(iterations, label="iterations")
    if not MIN_BOOTSTRAP_ITERATIONS <= iterations <= MAX_BOOTSTRAP_ITERATIONS:
        raise ValueError(
            "iterations must be between "
            f"{MIN_BOOTSTRAP_ITERATIONS} and {MAX_BOOTSTRAP_ITERATIONS}."
        )
    if (
        isinstance(seed, bool)
        or not isinstance(seed, int)
        or not 0 <= seed <= (1 << 64) - 1
    ):
        raise ValueError("seed must be an unsigned 64-bit integer.")
    case_means: list[float] = []
    total_observations = 0
    if any(
        not isinstance(case_id, str) or not case_id.strip()
        for case_id in case_replicate_deltas
    ):
        raise ValueError("case ids must be non-empty strings.")
    for case_id in sorted(case_replicate_deltas):
        raw_deltas = case_replicate_deltas[case_id]
        deltas = _finite_sequence(
            raw_deltas,
            label=f"case_replicate_deltas[{case_id!r}]",
            minimum=-1.0,
            maximum=1.0,
        )
        total_observations += len(deltas)
        if total_observations > MAX_DIAGNOSTIC_CASES:
            raise ValueError("total replicate observation count exceeds the limit.")
        case_means.append(sum(deltas) / len(deltas))
    if len(case_means) < 2:
        raise ValueError("At least two independent case clusters are required.")
    rng = random.Random(seed)
    cluster_count = len(case_means)
    if cluster_count * iterations > MAX_BOOTSTRAP_DRAWS:
        raise ValueError(
            "case clusters times iterations exceeds the bootstrap draw limit."
        )
    bootstrap_means = sorted(
        sum(case_means[rng.randrange(cluster_count)] for _ in range(cluster_count))
        / cluster_count
        for _ in range(iterations)
    )
    tail = (1.0 - confidence_level) / 2.0
    return {
        "method": "case_cluster_percentile_bootstrap_v1",
        "confidence_level": confidence_level,
        "iterations": iterations,
        "seed": seed,
        "independent_case_count": cluster_count,
        "replicate_observation_count": total_observations,
        "point_estimate": sum(case_means) / cluster_count,
        "lower": _linear_quantile_from_sorted(bootstrap_means, tail),
        "upper": _linear_quantile_from_sorted(bootstrap_means, 1.0 - tail),
    }


def domain_macro_cluster_bootstrap_interval(
    domain_case_replicate_deltas: Mapping[
        str,
        Mapping[str, Sequence[float]],
    ],
    *,
    confidence_level: float = 0.95,
    iterations: int = 2_000,
    seed: int = 0,
) -> dict[str, Any]:
    """Bootstrap an equal-domain macro mean within domain case strata.

    Replicate calls are first averaged inside each distinct case. Cases are then
    resampled with replacement inside their original domain, preserving each
    domain's case count, before the resampled domain means are averaged with
    equal domain weight.
    """

    if (
        not isinstance(domain_case_replicate_deltas, Mapping)
        or not domain_case_replicate_deltas
    ):
        raise ValueError(
            "domain_case_replicate_deltas must be a non-empty mapping."
        )
    if len(domain_case_replicate_deltas) > MAX_DIAGNOSTIC_CASES // 2:
        raise ValueError("domain count exceeds the diagnostic limit.")
    if any(
        not isinstance(domain_id, str) or not domain_id.strip()
        for domain_id in domain_case_replicate_deltas
    ):
        raise ValueError("domain ids must be non-empty strings.")
    confidence_level = _probability(
        confidence_level,
        label="confidence_level",
        open_lower=True,
    )
    if confidence_level >= 1.0:
        raise ValueError("confidence_level must be less than one.")
    iterations = _strict_count(iterations, label="iterations")
    if not MIN_BOOTSTRAP_ITERATIONS <= iterations <= MAX_BOOTSTRAP_ITERATIONS:
        raise ValueError(
            "iterations must be between "
            f"{MIN_BOOTSTRAP_ITERATIONS} and {MAX_BOOTSTRAP_ITERATIONS}."
        )
    if (
        isinstance(seed, bool)
        or not isinstance(seed, int)
        or not 0 <= seed <= (1 << 64) - 1
    ):
        raise ValueError("seed must be an unsigned 64-bit integer.")

    domain_case_means: dict[str, tuple[float, ...]] = {}
    seen_case_ids: set[str] = set()
    total_observations = 0
    total_cases = 0
    for domain_id in sorted(domain_case_replicate_deltas):
        raw_cases = domain_case_replicate_deltas[domain_id]
        if not isinstance(raw_cases, Mapping) or not raw_cases:
            raise ValueError(
                f"domain_case_replicate_deltas[{domain_id!r}] must be a "
                "non-empty mapping."
            )
        if len(raw_cases) > MAX_DIAGNOSTIC_CASES - total_cases:
            raise ValueError("total independent case count exceeds the limit.")
        if any(
            not isinstance(case_id, str) or not case_id.strip()
            for case_id in raw_cases
        ):
            raise ValueError("case ids must be non-empty strings.")
        if len(raw_cases) < 2:
            raise ValueError(
                "At least two independent case clusters are required per domain."
            )

        case_means: list[float] = []
        for case_id in sorted(raw_cases):
            if case_id in seen_case_ids:
                raise ValueError("case ids must be globally unique across domains.")
            seen_case_ids.add(case_id)
            deltas = _finite_sequence(
                raw_cases[case_id],
                label=(
                    "domain_case_replicate_deltas"
                    f"[{domain_id!r}][{case_id!r}]"
                ),
                minimum=-1.0,
                maximum=1.0,
            )
            total_observations += len(deltas)
            if total_observations > MAX_DIAGNOSTIC_CASES:
                raise ValueError(
                    "total replicate observation count exceeds the limit."
                )
            case_means.append(sum(deltas) / len(deltas))
        total_cases += len(case_means)
        if total_cases > MAX_DIAGNOSTIC_CASES:
            raise ValueError("total independent case count exceeds the limit.")
        domain_case_means[domain_id] = tuple(case_means)

    if total_cases * iterations > MAX_BOOTSTRAP_DRAWS:
        raise ValueError(
            "case clusters times iterations exceeds the bootstrap draw limit."
        )
    rng = random.Random(seed)
    bootstrap_macro_means: list[float] = []
    for _ in range(iterations):
        sampled_domain_means = []
        for domain_id in sorted(domain_case_means):
            case_means = domain_case_means[domain_id]
            case_count = len(case_means)
            sampled_domain_means.append(
                sum(
                    case_means[rng.randrange(case_count)]
                    for _ in range(case_count)
                )
                / case_count
            )
        bootstrap_macro_means.append(
            sum(sampled_domain_means) / len(sampled_domain_means)
        )
    bootstrap_macro_means.sort()
    domain_means = [
        sum(case_means) / len(case_means)
        for case_means in domain_case_means.values()
    ]
    tail = (1.0 - confidence_level) / 2.0
    return {
        "method": "stratified_domain_case_cluster_percentile_bootstrap_v1",
        "confidence_level": confidence_level,
        "iterations": iterations,
        "seed": seed,
        "domain_count": len(domain_case_means),
        "independent_case_count": total_cases,
        "replicate_observation_count": total_observations,
        "point_estimate": sum(domain_means) / len(domain_means),
        "lower": _linear_quantile_from_sorted(bootstrap_macro_means, tail),
        "upper": _linear_quantile_from_sorted(
            bootstrap_macro_means,
            1.0 - tail,
        ),
    }


def _binomial_cdf(*, failures: int, trials: int, probability: float) -> float:
    if probability <= 0.0:
        return 1.0
    if probability >= 1.0:
        return 1.0 if failures >= trials else 0.0
    log_probability = math.log(probability)
    log_complement = math.log1p(-probability)
    terms = [
        math.lgamma(trials + 1)
        - math.lgamma(value + 1)
        - math.lgamma(trials - value + 1)
        + value * log_probability
        + (trials - value) * log_complement
        for value in range(failures + 1)
    ]
    maximum = max(terms)
    return math.exp(maximum) * sum(math.exp(term - maximum) for term in terms)


def exact_binomial_risk_upper_bound(
    *,
    failures: int,
    trials: int,
    confidence_level: float = 0.95,
) -> float:
    """Return the one-sided exact Clopper-Pearson risk upper bound.

    ``failures`` and ``trials`` must already represent independent, identically
    distributed registered Bernoulli units with one common failure probability.
    Repeated calls from one case cluster must not be supplied as separate trials,
    and heterogeneous strata must not be pooled.
    """

    failures = _strict_count(failures, label="failures")
    trials = _strict_count(trials, label="trials")
    if failures > trials:
        raise ValueError("failures must not exceed trials.")
    if trials == 0:
        raise ValueError("trials must be greater than zero.")
    confidence_level = _probability(
        confidence_level,
        label="confidence_level",
        open_lower=True,
    )
    if confidence_level < 0.5:
        raise ValueError("confidence_level must be at least 0.5.")
    if confidence_level >= 1.0:
        raise ValueError("confidence_level must be less than one.")
    if failures == trials:
        return 1.0
    alpha = 1.0 - confidence_level
    if failures == 0:
        return -math.expm1(math.log(alpha) / trials)
    lower = 0.0
    upper = 1.0
    for _ in range(80):
        midpoint = (lower + upper) / 2.0
        if _binomial_cdf(
            failures=failures,
            trials=trials,
            probability=midpoint,
        ) > alpha:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0


def aggregate_case_safety_failures(
    case_failure_observations: Mapping[str, Sequence[bool]],
) -> dict[str, Any]:
    """Collapse repeated safety observations to one any-failure unit per case.

    Call this separately for every preregistered category and IID/common-risk
    stratum. The returned case counts can then be passed to
    :func:`exact_binomial_risk_upper_bound`.
    """

    if (
        not isinstance(case_failure_observations, Mapping)
        or not case_failure_observations
    ):
        raise ValueError("case_failure_observations must be a non-empty mapping.")
    if len(case_failure_observations) > MAX_DIAGNOSTIC_CASES:
        raise ValueError("independent case count exceeds the diagnostic limit.")
    if any(
        not isinstance(case_id, str) or not case_id.strip()
        for case_id in case_failure_observations
    ):
        raise ValueError("case ids must be non-empty strings.")

    case_failures: dict[str, bool] = {}
    total_observations = 0
    for case_id in sorted(case_failure_observations):
        observations = case_failure_observations[case_id]
        if isinstance(observations, (str, bytes)) or not isinstance(
            observations,
            Sequence,
        ):
            raise ValueError(
                f"case_failure_observations[{case_id!r}] must be a non-empty "
                "sequence of booleans."
            )
        observation_count = len(observations)
        if observation_count == 0:
            raise ValueError(
                f"case_failure_observations[{case_id!r}] must be a non-empty "
                "sequence of booleans."
            )
        if observation_count > MAX_DIAGNOSTIC_CASES - total_observations:
            raise ValueError("total safety observation count exceeds the limit.")
        try:
            bounded_observations = tuple(
                observations[index] for index in range(observation_count)
            )
        except (IndexError, KeyError, TypeError) as exc:
            raise ValueError(
                f"case_failure_observations[{case_id!r}] has an inconsistent "
                "sequence length."
            ) from exc
        if any(not isinstance(value, bool) for value in bounded_observations):
            raise ValueError(
                f"case_failure_observations[{case_id!r}] must contain only "
                "booleans."
            )
        total_observations += observation_count
        case_failures[case_id] = any(bounded_observations)

    failed_case_ids = tuple(
        case_id for case_id in sorted(case_failures) if case_failures[case_id]
    )
    return {
        "aggregation": "any_failure_per_independent_case_v1",
        "independent_case_count": len(case_failures),
        "failed_case_count": len(failed_case_ids),
        "replicate_observation_count": total_observations,
        "failed_case_ids": failed_case_ids,
    }


def cost_efficiency_summary(
    call_costs: Sequence[float],
    *,
    successful_terminal_cases: int,
    terminal_case_count: int,
) -> dict[str, Any]:
    """Summarize fully charged call cost against terminal case outcomes."""

    costs = _finite_sequence(call_costs, label="call_costs", minimum=0.0)
    successful_terminal_cases = _strict_count(
        successful_terminal_cases,
        label="successful_terminal_cases",
    )
    terminal_case_count = _strict_count(
        terminal_case_count,
        label="terminal_case_count",
    )
    if terminal_case_count == 0:
        raise ValueError("terminal_case_count must be greater than zero.")
    if successful_terminal_cases > terminal_case_count:
        raise ValueError(
            "successful_terminal_cases must not exceed terminal_case_count."
        )
    total_cost = sum(costs)
    if not math.isfinite(total_cost):
        raise ValueError("total charged cost must be finite.")
    return {
        "charged_call_count": len(costs),
        "terminal_case_count": terminal_case_count,
        "successful_terminal_cases": successful_terminal_cases,
        "success_rate": successful_terminal_cases / terminal_case_count,
        "total_cost": total_cost,
        "cost_per_terminal_case": total_cost / terminal_case_count,
        "expected_cost_per_success": (
            total_cost / successful_terminal_cases
            if successful_terminal_cases > 0
            else None
        ),
        "success_cost_evaluable": successful_terminal_cases > 0,
    }


def robustness_pair_summary(
    clean_perturbed_pairs: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    """Summarize registered clean/perturbed utility pairs."""

    if not isinstance(clean_perturbed_pairs, Mapping) or not clean_perturbed_pairs:
        raise ValueError("clean_perturbed_pairs must be a non-empty mapping.")
    if len(clean_perturbed_pairs) > MAX_DIAGNOSTIC_CASES:
        raise ValueError("robustness pair count exceeds the diagnostic limit.")
    if any(
        not isinstance(pair_id, str) or not pair_id.strip()
        for pair_id in clean_perturbed_pairs
    ):
        raise ValueError("robustness pair ids must be non-empty strings.")
    expected_fields = {"clean_utility", "perturbed_utility"}
    pair_metrics: dict[str, dict[str, float | None]] = {}
    for pair_id in sorted(clean_perturbed_pairs):
        pair = clean_perturbed_pairs[pair_id]
        if not isinstance(pair, Mapping) or set(pair) != expected_fields:
            raise ValueError(
                f"{pair_id}: robustness pair must contain exactly "
                "clean_utility and perturbed_utility."
            )
        clean = _finite_number(
            pair["clean_utility"],
            label=f"{pair_id}.clean_utility",
            minimum=0.0,
            maximum=1.0,
        )
        perturbed = _finite_number(
            pair["perturbed_utility"],
            label=f"{pair_id}.perturbed_utility",
            minimum=0.0,
            maximum=1.0,
        )
        retention = perturbed / clean if clean > 0.0 else None
        if retention is not None and not math.isfinite(retention):
            raise ValueError(f"{pair_id}.retention must be finite.")
        pair_metrics[pair_id] = {
            "clean_utility": clean,
            "perturbed_utility": perturbed,
            "degradation": clean - perturbed,
            "retention": retention,
        }
    worst_pair = max(
        pair_metrics,
        key=lambda item: (pair_metrics[item]["degradation"], item),
    )
    retention_values = [
        metrics["retention"]
        for metrics in pair_metrics.values()
        if metrics["retention"] is not None
    ]
    return {
        "pair_count": len(pair_metrics),
        "pair_metrics": pair_metrics,
        "mean_degradation": (
            sum(metrics["degradation"] for metrics in pair_metrics.values())
            / len(pair_metrics)
        ),
        "worst_pair": worst_pair,
        "worst_degradation": pair_metrics[worst_pair]["degradation"],
        "retention_evaluable_count": len(retention_values),
        "minimum_retention": min(retention_values) if retention_values else None,
    }


def latency_percentiles(elapsed_ms: Sequence[float]) -> dict[str, float | int]:
    """Return Type-7 p50/p95/p99 over all terminal call latencies."""

    values = sorted(
        _finite_sequence(elapsed_ms, label="elapsed_ms", minimum=0.0)
    )
    return {
        "count": len(values),
        "p50_ms": _linear_quantile_from_sorted(values, 0.50),
        "p95_ms": _linear_quantile_from_sorted(values, 0.95),
        "p99_ms": _linear_quantile_from_sorted(values, 0.99),
    }


def pareto_nondominated_ids(
    candidates: Mapping[str, Mapping[str, float]],
) -> tuple[str, ...]:
    """Return candidates not dominated on quality, cost, and p95 latency."""

    if not isinstance(candidates, Mapping) or not candidates:
        raise ValueError("candidates must be a non-empty mapping.")
    if len(candidates) > MAX_PARETO_CANDIDATES:
        raise ValueError(
            f"candidate count exceeds the {MAX_PARETO_CANDIDATES}-item "
            "Pareto diagnostic limit."
        )
    normalized: dict[str, tuple[float, float, float]] = {}
    expected_fields = {"quality", "cost", "p95_latency_ms"}
    if any(
        not isinstance(candidate_id, str) or not candidate_id.strip()
        for candidate_id in candidates
    ):
        raise ValueError("candidate ids must be non-empty strings.")
    for candidate_id in sorted(candidates):
        metrics = candidates[candidate_id]
        if not isinstance(metrics, Mapping) or set(metrics) != expected_fields:
            raise ValueError(
                f"{candidate_id}: metrics must contain exactly "
                "quality, cost, and p95_latency_ms."
            )
        normalized[candidate_id] = (
            _finite_number(
                metrics["quality"],
                label=f"{candidate_id}.quality",
                minimum=0.0,
                maximum=1.0,
            ),
            _finite_number(
                metrics["cost"],
                label=f"{candidate_id}.cost",
                minimum=0.0,
            ),
            _finite_number(
                metrics["p95_latency_ms"],
                label=f"{candidate_id}.p95_latency_ms",
                minimum=0.0,
            ),
        )

    def dominates(
        left: tuple[float, float, float],
        right: tuple[float, float, float],
    ) -> bool:
        return (
            left[0] >= right[0]
            and left[1] <= right[1]
            and left[2] <= right[2]
            and left != right
        )

    return tuple(
        candidate_id
        for candidate_id in sorted(normalized)
        if not any(
            other_id != candidate_id
            and dominates(other_metrics, normalized[candidate_id])
            for other_id, other_metrics in normalized.items()
        )
    )
