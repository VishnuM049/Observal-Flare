from __future__ import annotations


class ExperimentWeightError(ValueError):
    pass


def normalize_weights(image_count: int, weights: list[int] | None) -> list[int]:
    """Validate aligned positive integer weights, defaulting legacy requests to 1:1."""
    if image_count < 1:
        raise ExperimentWeightError("At least one experiment image is required")
    if not weights:
        return [1] * image_count
    if len(weights) != image_count:
        raise ExperimentWeightError("Provide exactly one weight for every experiment image")
    if any(isinstance(weight, bool) or not isinstance(weight, int) or weight < 1 for weight in weights):
        raise ExperimentWeightError("Image weights must be positive integers")
    return list(weights)


def allocate_weighted_pulls(total: int, weights: list[int]) -> list[int]:
    """Allocate an exact total by largest remainder, with stable tie-breaking."""
    if total < 0:
        raise ExperimentWeightError("Pull total cannot be negative")
    normalized = normalize_weights(len(weights), weights)
    weight_sum = sum(normalized)
    allocations = [(total * weight) // weight_sum for weight in normalized]
    remainder = total - sum(allocations)
    order = sorted(
        range(len(normalized)),
        key=lambda index: (-(total * normalized[index] % weight_sum), index),
    )
    for index in order[:remainder]:
        allocations[index] += 1
    return allocations
