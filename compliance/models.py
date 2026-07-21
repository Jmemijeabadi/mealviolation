from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class ResultCode(StrEnum):
    COMPLIANT = "COMPLIANT"
    FIRST_MEAL_MISSING = "FIRST_MEAL_MISSING"
    FIRST_MEAL_LATE = "FIRST_MEAL_LATE"
    FIRST_MEAL_SHORT = "FIRST_MEAL_SHORT"
    FIRST_MEAL_WAIVER_UNVERIFIED = "FIRST_MEAL_WAIVER_UNVERIFIED"
    SECOND_MEAL_MISSING = "SECOND_MEAL_MISSING"
    SECOND_MEAL_LATE = "SECOND_MEAL_LATE"
    SECOND_MEAL_SHORT = "SECOND_MEAL_SHORT"
    SECOND_MEAL_WAIVER_UNVERIFIED = "SECOND_MEAL_WAIVER_UNVERIFIED"
    ON_DUTY_MEAL_AGREEMENT_UNVERIFIED = "ON_DUTY_MEAL_AGREEMENT_UNVERIFIED"
    MEAL_PROBABLE_TIMESTAMP_ONLY = "MEAL_PROBABLE_TIMESTAMP_ONLY"
    PUNCH_ERROR = "PUNCH_ERROR"
    INCOMPLETE_TIMECARD = "INCOMPLETE_TIMECARD"
    ADJUSTED_TIMECARD_REVIEW = "ADJUSTED_TIMECARD_REVIEW"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class CaliforniaMealRules:
    minimum_meal_minutes: float = 30.0
    first_meal_required_after_hours: float = 5.0
    first_meal_waiver_max_hours: float = 6.0
    second_meal_required_after_hours: float = 10.0
    second_meal_waiver_max_hours: float = 12.0
    timestamp_tolerance_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.minimum_meal_minutes <= 0:
            raise ValueError("Minimum meal duration must be positive.")
        if self.first_meal_required_after_hours <= 0:
            raise ValueError("First meal threshold must be positive.")
        if self.first_meal_waiver_max_hours < self.first_meal_required_after_hours:
            raise ValueError("First meal waiver limit is invalid.")
        if self.second_meal_required_after_hours <= self.first_meal_required_after_hours:
            raise ValueError("Second meal threshold must be after first meal threshold.")
        if self.second_meal_waiver_max_hours < self.second_meal_required_after_hours:
            raise ValueError("Second meal waiver limit is invalid.")


@dataclass(frozen=True)
class WaiverRecord:
    employee_key: str
    first_meal_waiver: bool = False
    second_meal_waiver: bool = False
    on_duty_meal_agreement: bool = False
    effective_date: date | None = None
    expiration_date: date | None = None

    def active_on(self, business_date: date) -> bool:
        if self.effective_date and business_date < self.effective_date:
            return False
        if self.expiration_date and business_date > self.expiration_date:
            return False
        return True


@dataclass
class MealCandidate:
    start: datetime
    end: datetime
    duration_minutes: float
    worked_hours_before: float
    evidence: str
    confirmed: bool
    paid: bool = False
    source_timecard_id: str | None = None


@dataclass
class WorkdayAnalysis:
    location_ref: str
    location_name: str
    business_date: date
    employee_key: str
    employee_name: str
    payroll_id: str
    roles: str
    first_clock_in: datetime | None
    last_clock_out: datetime | None
    worked_hours: float
    pay_rate: float | None
    oracle_premium_hours: float
    oracle_premium_pay: float
    meals: list[MealCandidate] = field(default_factory=list)
    result_codes: list[ResultCode] = field(default_factory=list)
    automatic_violations: list[ResultCode] = field(default_factory=list)
    reviews: list[ResultCode] = field(default_factory=list)
    punch_errors: list[str] = field(default_factory=list)
    adjustment_count: int = 0
    details: list[str] = field(default_factory=list)
    source_timecard_ids: list[str] = field(default_factory=list)

    @property
    def has_confirmed_meal_violation(self) -> bool:
        return bool(self.automatic_violations)

    @property
    def premium_workday(self) -> bool:
        return self.has_confirmed_meal_violation

    def to_row(self) -> dict[str, Any]:
        return {
            "Location Ref": self.location_ref,
            "Location": self.location_name,
            "Business Date": self.business_date,
            "Employee Key": self.employee_key,
            "Employee": self.employee_name,
            "Payroll ID": self.payroll_id,
            "Role(s)": self.roles,
            "First Clock In": self.first_clock_in,
            "Last Clock Out": self.last_clock_out,
            "Worked Hours": round(self.worked_hours, 2),
            "Meal Count": len(self.meals),
            "Confirmed Meals": sum(1 for meal in self.meals if meal.confirmed),
            "Probable Meals": sum(1 for meal in self.meals if not meal.confirmed),
            "Automatic Violations": ", ".join(code.value for code in self.automatic_violations),
            "Reviews": ", ".join(code.value for code in self.reviews),
            "Punch Errors": len(self.punch_errors),
            "Adjustment Count": self.adjustment_count,
            "Potential Premium Workday": self.premium_workday,
            "Estimated Meal Premium": round(self.pay_rate or 0.0, 2) if self.premium_workday else 0.0,
            "Oracle Premium Hours": round(self.oracle_premium_hours, 2),
            "Oracle Premium Pay": round(self.oracle_premium_pay, 2),
            "Result": ", ".join(code.value for code in self.result_codes),
            "Details": " | ".join(self.details),
            "Timecard IDs": ", ".join(self.source_timecard_ids),
        }
