
# Hospital Operations — OpenCode Instructions

## Project
Implement a backend-only Operational Bed Allocation
System using Frappe Health (Marley), ERPNext and HRMS.

## Architecture
- One Frappe backend, Python and MariaDB.
- Custom app: hospital_operations.
- Reuse existing Marley, ERPNext and HRMS DocTypes.
- OpenMRS is for architecture research only.
- No FastAPI, Java services or additional databases.
- No custom frontend in this phase.
- Strategic bed optimisation is out of scope.

## Required specification
Read docs/bed-allocation-design.md before
planning or implementing any functionality.

Follow its clinical safety, schema, transaction,
API, testing and implementation requirements.

## Remote Frappe
Site: https://frappe.abhidigitech.com

Read API credentials only from environment variables.
Never log, commit or expose secrets.

## Mandatory workflow
1. Inspect the existing repository.
2. Perform read-only remote schema discovery.
3. Verify installed Frappe, Marley, ERPNext and HRMS.
4. Produce schema analysis and implementation plan.
5. Wait for approval before schema changes.
6. Implement in small, testable phases.
7. Run relevant tests after each phase.

## Safety
- Never modify production data without approval.
- Never execute database migrations automatically.
- Never duplicate existing patient/admission models.
- Never bypass mandatory clinical constraints.
- Prevent double bed allocation with database locking.
- Never disable TLS verification.

## Development standards
- Use Frappe ORM and supported framework APIs.
- Follow installed Frappe version conventions.
- Add automated tests for every critical workflow.
- Document all design decisions.
- Report test results and known limitations.
