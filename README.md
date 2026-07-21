# Oracle MICROS Meal Compliance Dashboard

Streamlit application for reviewing California meal-period compliance directly from Oracle MICROS Simphony Business Intelligence API timecards.

## What this version changes

- Replaces the required Excel upload with Oracle BI API calls.
- Uses Oracle `busDt` as the business date.
- Reads `getTimeCardDetails` with `includeAdjustments=true`.
- Resolves employee names from `getEmployeeDimensions`.
- Resolves roles from `getJobCodeDimensions`.
- Resolves location name and timezone from `getLocationDimensions`.
- Separates automatic violations, waiver reviews, probable meals, paid breaks, adjustments, open timecards, and punch errors.
- Evaluates both the first and second meal.
- Consolidates potential meal premium exposure by employee and business date.

## Security before deployment

Rotate any password, token, or credential that has been shared in email, chat, screenshots, tickets, or source code. Do not place production credentials in this repository.

Create `.streamlit/secrets.toml` from the example:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Fill it with the Business Intelligence API account information sent by Oracle. The application does not use the legacy `/ws/mylabor` token.

## Local installation

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Oracle authentication

The client implements Oracle's OpenID Connect Authorization Code Flow with PKCE:

1. Generate `code_verifier` and SHA-256 `code_challenge`.
2. Call `/oidc-provider/v1/oauth2/authorize`.
3. Sign in through `/oidc-provider/v1/oauth2/signin` with the API account.
4. Exchange the authorization code through `/oidc-provider/v1/oauth2/token`.
5. Send `Authorization: Bearer <id_token>` to BI endpoints.
6. Refresh the token in memory when needed.

Tokens are held in the Streamlit process session and are not written to disk.

## Oracle endpoints used

- `POST /bi/v1/{orgIdentifier}/getLocationDimensions`
- `POST /bi/v1/{orgIdentifier}/getEmployeeDimensions`
- `POST /bi/v1/{orgIdentifier}/getJobCodeDimensions`
- `POST /bi/v1/{orgIdentifier}/getTimeCardDetails`

The API account must have `Employee Time Card Details and Pay Rates` data access.

## Meal classifications

### Automatic violations

- `FIRST_MEAL_MISSING`
- `FIRST_MEAL_LATE`
- `FIRST_MEAL_SHORT`
- `SECOND_MEAL_MISSING`
- `SECOND_MEAL_LATE`
- `SECOND_MEAL_SHORT`

### Human review

- `FIRST_MEAL_WAIVER_UNVERIFIED`
- `SECOND_MEAL_WAIVER_UNVERIFIED`
- `ON_DUTY_MEAL_AGREEMENT_UNVERIFIED`
- `MEAL_PROBABLE_TIMESTAMP_ONLY`
- `ADJUSTED_TIMECARD_REVIEW`
- `INCOMPLETE_TIMECARD`
- `PUNCH_ERROR`
- `INCONCLUSIVE`

A timestamp gap without a confirmed unpaid-break indicator is not automatically treated as a compliant duty-free meal. A paid break is not automatically treated as a valid duty-free meal.

## Waiver register

The optional CSV uses this schema:

```csv
employee_key,first_meal_waiver,second_meal_waiver,on_duty_meal_agreement,effective_date,expiration_date
12345,false,false,false,2026-01-01,
```

`employee_key` should match the payroll ID returned by Oracle. If no payroll ID is available, the normalizer uses `EMP::<empNum>`.

The existence of an on-duty agreement does not prove that the nature-of-work requirement is satisfied. Those cases remain review items.

## Validation mode

The app includes a JSON mode so the Oracle responses can be tested without production credentials. Sample payloads are under `demo/`.

## Connectivity probe

A command-line probe is included for the first controlled Oracle test. It reads credentials only from environment variables and can save location, employee, job-code, and timecard responses for comparison.

```bash
export ORACLE_AUTH_SERVER="..."
export ORACLE_APPLICATION_SERVER="..."
export ORACLE_ORG_IDENTIFIER="..."
export ORACLE_CLIENT_ID="..."
export ORACLE_BI_USERNAME="..."
export ORACLE_BI_PASSWORD="..."
python oracle_probe.py --loc-ref 8 --business-date 2026-07-01
```

## Tests

```bash
pytest -q
```

The suite covers exact five-hour shifts, five-to-six-hour waivers, missing and late first meals, missing and late second meals, probable timestamp-only meals, open timecards, and Oracle normalization.

## Important limitations

- The application is an operational audit tool, not a final legal determination.
- A timecard can establish timestamps and Simphony statuses but cannot by itself prove that an employee was relieved of all duty.
- Estimated premium uses the highest timecard pay rate in the workday. The legally applicable regular rate may require a more complex payroll calculation.
- Oracle `premPay` may include premiums unrelated to meal periods depending on enterprise configuration.
- Materially incomplete or overlapping timecards suppress automatic conclusions.
- Before production use, compare at least several payroll periods against manually reviewed timecards and signed waiver records.
