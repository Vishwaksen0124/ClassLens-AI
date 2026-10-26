from __future__ import annotations

from typing import Any

DIMENSION_WEIGHTS = {
    "Teaching Clarity and Structure": 30,
    "Student Interaction and Checks for Understanding": 25,
    "Instructional Delivery and Pacing": 20,
    "Audio and Verbal Communication": 15,
    "Visual and Instructional Material Clarity": 10,
}


def calculate_classlens_score(dimension_scores: dict[str, int | None]) -> dict[str, Any]:
    """Calculate the weighted overall score; null dimensions do not count as failures."""
    if set(dimension_scores) != set(DIMENSION_WEIGHTS):
        raise ValueError("dimension_scores must contain exactly the five rubric dimensions")
    for name, score in dimension_scores.items():
        if score is not None and (type(score) is not int or not 1 <= score <= 5):
            raise ValueError(f"{name} must be an integer from 1 to 5 or null")
    percentages = {
        name: None if score is None else (score - 1) * 25
        for name, score in dimension_scores.items()
    }
    valid_weight = sum(DIMENSION_WEIGHTS[n] for n, score in dimension_scores.items() if score is not None)
    if valid_weight == 0:
        return {"assessment_status": "insufficient_evidence", "overall_score": None, "rating": None,
                "evidence_coverage_percent": 0, "dimension_percentages": percentages}
    score = round(sum(percentages[n] * DIMENSION_WEIGHTS[n] for n in DIMENSION_WEIGHTS if percentages[n] is not None) / valid_weight, 1)
    rating = "Excellent" if score >= 80 else "Good" if score >= 60 else "Average" if score >= 40 else "Poor"
    return {"assessment_status": "completed", "overall_score": score, "rating": rating,
            "evidence_coverage_percent": valid_weight, "dimension_percentages": percentages}
