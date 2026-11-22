"""Pure functions that turn an agent's forecast into a payload Metaculus accepts.

Server rules mirrored here come from Metaculus `questions/serializers/common.py`:
- binary: probability_yes in [0.001, 0.999]
- multiple choice: every current option present, each in [0.001, 0.999], sum 1
- continuous: exactly inbound_outcome_count + 1 values, every step at least
  0.01 / inbound_outcome_count and at most 0.2 * 200 / inbound_outcome_count,
  open lower bound needs cdf[0] >= 0.001 (closed: == 0), open upper bound needs
  cdf[-1] <= 0.999 (closed: == 1)
"""

from __future__ import annotations

import math

BINARY_MIN = 0.03
BINARY_MAX = 0.97
MC_FLOOR = 0.02
DEFAULT_INBOUND_OUTCOME_COUNT = 200
MIN_PERCENTILES = 5


class ForecastFormatError(ValueError):
    pass


def clip_binary(probability: float) -> float:
    if not 0.0 <= probability <= 1.0 or math.isnan(probability):
        raise ForecastFormatError(f"probability must be in [0, 1], got {probability}")
    return min(BINARY_MAX, max(BINARY_MIN, probability))


def normalize_multiple_choice(
    probabilities: dict[str, float], options: list[str]
) -> dict[str, float]:
    missing = [o for o in options if o not in probabilities]
    unknown = [o for o in probabilities if o not in options]
    if missing or unknown:
        raise ForecastFormatError(
            f"options must match the question exactly; missing={missing} unknown={unknown}"
        )
    if any(p < 0 or math.isnan(p) for p in probabilities.values()):
        raise ForecastFormatError("probabilities must be non-negative numbers")
    total = sum(probabilities[o] for o in options)
    if total <= 0:
        raise ForecastFormatError("probabilities must not all be zero")

    shares = {o: probabilities[o] / total for o in options}
    floor = min(MC_FLOOR, 1.0 / len(options))
    floored = {o: max(floor, p) for o, p in shares.items()}
    excess = sum(floored.values()) - 1.0
    adjustable = sum(p - floor for p in floored.values())
    if adjustable > 0:
        floored = {
            o: p - excess * (p - floor) / adjustable for o, p in floored.items()
        }
    rounded = {o: round(p, 6) for o, p in floored.items()}
    drift = 1.0 - sum(rounded.values())
    largest = max(rounded, key=rounded.get)
    rounded[largest] = round(rounded[largest] + drift, 6)
    validate_multiple_choice(rounded, options)
    return rounded


def validate_multiple_choice(probabilities: dict[str, float], options: list[str]) -> None:
    for option in options:
        value = probabilities.get(option)
        if value is None or not 0.001 <= value <= 0.999:
            raise ForecastFormatError(f"option {option!r} must be in [0.001, 0.999], got {value}")
    if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-6):
        raise ForecastFormatError(f"probabilities must sum to 1, got {sum(probabilities.values())}")


def check_percentiles(percentiles: dict[float, float]) -> list[tuple[float, float]]:
    if len(percentiles) < MIN_PERCENTILES:
        raise ForecastFormatError(
            f"give at least {MIN_PERCENTILES} percentiles, got {len(percentiles)}"
        )
    points = sorted(percentiles.items())
    levels = [p for p, _ in points]
    values = [v for _, v in points]
    if levels[0] <= 0 or levels[-1] >= 1:
        raise ForecastFormatError("percentile levels must be strictly between 0 and 1")
    if levels[0] > 0.1 or levels[-1] < 0.9:
        raise ForecastFormatError(
            "include a low tail (level <= 0.1) and a high tail (level >= 0.9)"
        )
    if any(b <= a for a, b in zip(values, values[1:])):
        raise ForecastFormatError("values must strictly increase with the percentile level")
    return points


def validate_cdf(
    cdf: list[float],
    inbound_outcome_count: int | None,
    open_lower_bound: bool,
    open_upper_bound: bool,
) -> None:
    count = inbound_outcome_count or DEFAULT_INBOUND_OUTCOME_COUNT
    rounded = [round(x, 10) for x in cdf]
    errors: list[str] = []
    if len(rounded) != count + 1:
        errors.append(f"cdf must have {count + 1} values, got {len(rounded)}")
    steps = [round(b - a, 9) for a, b in zip(rounded, rounded[1:])]
    min_step = round(0.01 / count, 9)
    max_step = 0.2 * DEFAULT_INBOUND_OUTCOME_COUNT / count
    if steps and min(steps) < min_step:
        errors.append(f"every step must be >= {min_step}, smallest is {min(steps)}")
    if steps and max(steps) > max_step:
        errors.append(f"every step must be <= {max_step}, largest is {max(steps)}")
    if open_lower_bound and not rounded[0] >= 0.001:
        errors.append("open lower bound needs cdf[0] >= 0.001")
    if not open_lower_bound and rounded[0] != 0.0:
        errors.append("closed lower bound needs cdf[0] == 0")
    if open_upper_bound and not rounded[-1] <= 0.999:
        errors.append("open upper bound needs cdf[-1] <= 0.999")
    if not open_upper_bound and rounded[-1] != 1.0:
        errors.append("closed upper bound needs cdf[-1] == 1")
    if errors:
        raise ForecastFormatError("; ".join(errors))
