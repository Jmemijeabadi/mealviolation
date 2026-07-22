from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any


class ResultCode(StrEnum):
    COMPLIANT_BY_PUNCH = "COMPLIANT_BY_PUNCH"
    COMPLIANT = "COMPLIANT_BY_PUNCH"  # backward-compatible alias
    EXCLUDED_EXEMPT = "EXCLUDED_EXEMPT"
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
    ADJUSTMENT_CHANGED_RESULT = "ADJUSTMENT_CHANGED_RESULT"
    EMPLOYEE_CLASSIFICATION_UNVERIFIED = "EMPLOYEE_CLASSIFICATION_UNVERIFIED"
    WORKDAY_CONFIGURATION_UNVERIFIED = "WORKDAY_CONFIGURATION_UNVERIFIED"
    BUSINESS_DATE_MISMATCH = "BUSINESS_DATE_MISMATCH"
    MULTI_LOCATION_WORKDAY_REVIEW = "MULTI_LOCATION_WORKDAY_REVIEW"
    REGULAR_RATE_UNVERIFIED = "REGULAR_RATE_UNVERIFIED"
    SOURCE_COVERAGE_INCOMPLETE = "SOURCE_COVERAGE_INCOMPLETE"
    LOCATION_SCOPE_INCOMPLETE = "LOCATION_SCOPE_INCOMPLETE"
    EMPLOYEE_NAME_UNRESOLVED = "EMPLOYEE_NAME_UNRESOLVED"
    UNKNOWN_ORACLE_CODE = "UNKNOWN_ORACLE_CODE"
    DATA_INTEGRITY_BLOCKED = "DATA_INTEGRITY_BLOCKED"
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
class EmployeePolicyRecord:
    employee_key: str
    classification: str = "UNKNOWN"  # NON_EXEMPT, EXEMPT, UNKNOWN
    first_meal_waiver: bool = False
    second_meal_waiver: bool = False
    on_duty_meal_agreement: bool = False
    effective_date: date | None = None
    expiration_date: date | None = None
    document_reference: str = ""
    verified_by: str = ""
    notes: str = ""

    def active_on(self, workday_date: date) -> bool:
        if self.effective_date and workday_date < self.effective_date:
            return False
        if self.expiration_date and workday_date > self.expiration_date:
            return False
        return True

    @property
    def normalized_classification(self) -> str:
        value = self.classification.strip().upper().replace("-", "_").replace(" ", "_")
        aliases = {
            "NONEXEMPT": "NON_EXEMPT",
            "NON_EXEMPT": "NON_EXEMPT",
            "NO_EXENTO": "NON_EXEMPT",
            "HOURLY": "NON_EXEMPT",
            "EXEMPT": "EXEMPT",
            "EXENTO": "EXEMPT",
        }
        return aliases.get(value, "UNKNOWN")

    @property
    def classification_verified(self) -> bool:
        return self.normalized_classification in {"NON_EXEMPT", "EXEMPT"} and bool(self.verified_by.strip())

    @property
    def first_meal_waiver_verified(self) -> bool:
        return self.first_meal_waiver and bool(self.verified_by.strip()) and bool(self.document_reference.strip())

    @property
    def second_meal_waiver_verified(self) -> bool:
        return self.second_meal_waiver and bool(self.verified_by.strip()) and bool(self.document_reference.strip())

    @property
    def on_duty_meal_agreement_verified(self) -> bool:
        return self.on_duty_meal_agreement and bool(self.verified_by.strip()) and bool(self.document_reference.strip())


# Backwards-compatible alias used by existing callers/tests.
WaiverRecord = EmployeePolicyRecord


@dataclass(frozen=True)
class WorkdayConfigRecord:
    location_ref: str
    workday_start: time = time(0, 0)
    timezone: str = "America/Los_Angeles"
    effective_date: date | None = None
    expiration_date: date | None = None
    verified_by: str = ""
    source: str = ""

    def active_on(self, calendar_date: date) -> bool:
        if self.effective_date and calendar_date < self.effective_date:
            return False
        if self.expiration_date and calendar_date > self.expiration_date:
            return False
        return True

    @property
    def is_verified(self) -> bool:
        return bool(self.verified_by.strip()) and bool(self.source.strip())


@dataclass(frozen=True)
class RegularRateRecord:
    employee_key: str
    regular_rate: float
    effective_date: date | None = None
    expiration_date: date | None = None
    source: str = ""
    verified_by: str = ""

    def active_on(self, workday_date: date) -> bool:
        if self.effective_date and workday_date < self.effective_date:
            return False
        if self.expiration_date and workday_date > self.expiration_date:
            return False
        return True

    @property
    def is_verified(self) -> bool:
        return bool(self.verified_by.strip()) and bool(self.source.strip())


@dataclass
class MealCandidate:
    start: datetime
    end: datetime
    duration_minutes: float
    worked_hours_before: float
    evidence: str
    confirmed_by_punch: bool
    paid: bool = False
    source_timecard_id: str | None = None
    locations: str = ""

    @property
    def confirmed(self) -> bool:
        """Compatibility property: means confirmed by punch evidence only."""
        return self.confirmed_by_punch


@dataclass
class WorkdayAnalysis:
    location_ref: str
    location_name: str
    legal_workday_date: date
    business_dates: str
    employee_key: str
    employee_name: str
    payroll_id: str
    employee_classification: str
    policy_source: str
    roles: str
    first_clock_in: datetime | None
    last_clock_out: datetime | None
    worked_hours: float
    base_pay_rate: float | None
    premium_rate: float | None
    premium_rate_basis: str
    oracle_premium_hours: float
    oracle_premium_pay: float
    meals: list[MealCandidate] = field(default_factory=list)
    result_codes: list[ResultCode] = field(default_factory=list)
    presumed_violations: list[ResultCode] = field(default_factory=list)
    candidate_violations: list[ResultCode] = field(default_factory=list)
    blocking_reasons: list[ResultCode] = field(default_factory=list)
    reviews: list[ResultCode] = field(default_factory=list)
    punch_errors: list[str] = field(default_factory=list)
    adjustment_count: int = 0
    details: list[str] = field(default_factory=list)
    source_timecard_ids: list[str] = field(default_factory=list)
    data_blocked: bool = False

    @property
    def automatic_violations(self) -> list[ResultCode]:
        """Backward-compatible name. These are presumed violations, not final legal findings."""
        return self.presumed_violations

    @property
    def has_presumed_meal_violation(self) -> bool:
        return bool(self.presumed_violations)

    @property
    def has_candidate_meal_violation(self) -> bool:
        """A punch-pattern finding, including findings still pending administrative validation."""
        return bool(self.candidate_violations)

    @property
    def premium_workday(self) -> bool:
        return self.has_presumed_meal_violation

    def to_row(self) -> dict[str, Any]:
        premium_amount = self.premium_rate if self.premium_workday and self.premium_rate else 0.0
        return {
            "Location Ref": self.location_ref,
            "Location": self.location_name,
            "Legal Workday Date": self.legal_workday_date,
            "Business Date": self.legal_workday_date,  # UI/backward compatibility
            "Oracle Business Dates": self.business_dates,
            "Employee Key": self.employee_key,
            "Employee": self.employee_name,
            "Payroll ID": self.payroll_id,
            "Employee Classification": self.employee_classification,
            "Policy Source": self.policy_source,
            "Role(s)": self.roles,
            "First Clock In": self.first_clock_in,
            "Last Clock Out": self.last_clock_out,
            "Worked Hours": round(self.worked_hours, 2),
            "Meal Count": len(self.meals),
            "Confirmed Meals": sum(1 for meal in self.meals if meal.confirmed_by_punch),
            "Probable Meals": sum(1 for meal in self.meals if not meal.confirmed_by_punch and not meal.paid),
            "Candidate Violations": ", ".join(code.value for code in self.candidate_violations),
            "Candidate Violation Count": len(self.candidate_violations),
            "Pending Validation Violations": ", ".join(
                code.value for code in self.candidate_violations if code not in self.presumed_violations
            ),
            "Blocking Reasons": ", ".join(code.value for code in self.blocking_reasons),
            "Presumed Violations": ", ".join(code.value for code in self.presumed_violations),
            "Automatic Violations": ", ".join(code.value for code in self.presumed_violations),
            "Reviews": ", ".join(code.value for code in self.reviews),
            "Punch Errors": len(self.punch_errors),
            "Adjustment Count": self.adjustment_count,
            "Potential Premium Workday": self.premium_workday,
            "Premium Estimate": round(premium_amount, 2),
            "Estimated Meal Premium": round(premium_amount, 2),
            "Premium Rate Basis": self.premium_rate_basis,
            "Base Pay Rate": round(self.base_pay_rate or 0.0, 2),
            "Oracle Premium Hours": round(self.oracle_premium_hours, 2),
            "Oracle Premium Pay": round(self.oracle_premium_pay, 2),
            "Result": ", ".join(code.value for code in self.result_codes),
            "Details": " | ".join(self.details),
            "Timecard IDs": ", ".join(self.source_timecard_ids),
            "Data Blocked": self.data_blocked,
        }
