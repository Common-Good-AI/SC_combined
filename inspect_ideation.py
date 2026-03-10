"""Check ideation ideas for email-only users."""
import json

with open("data_dump/gv_ideas_ideation.json") as f:
    ideation = json.load(f)

null_auth = [r for r in ideation if r.get("author_id") is None]
print(f"Ideation null author_id: {len(null_auth)}")

null_with_email = sum(
    1 for r in null_auth
    if r.get("custom_field_values.u_email_5vp")
    and str(r.get("custom_field_values.u_email_5vp")).strip().lower() not in ("", "nan", "none")
)
print(f"  Null author + HAS email: {null_with_email}")
print(f"  Null author + NO email (truly anon): {len(null_auth) - null_with_email}")

# Unique ideation emails from custom fields
ideation_emails = set()
for r in ideation:
    val = r.get("custom_field_values.u_email_5vp")
    if val and str(val).strip().lower() not in ("", "nan", "none"):
        ideation_emails.add(str(val).strip().lower())
print(f"  Unique emails from custom fields: {len(ideation_emails)}")

# Check gv_users overlap
with open("data_dump/gv_users.json") as f:
    users = json.load(f)
user_emails = set()
for u in users:
    e = u.get("email")
    if e and str(e).strip().lower() not in ("", "nan", "none"):
        user_emails.add(str(e).strip().lower())

overlap = ideation_emails & user_emails
email_only = ideation_emails - user_emails
print(f"  Ideation emails in gv_users: {len(overlap)}")
print(f"  Ideation emails NOT in gv_users: {len(email_only)}")

# Also check: do the u_email_rzm fields have any real emails?
rzm_vals = set()
for r in ideation:
    val = r.get("custom_field_values.u_email_rzm")
    if val and str(val).strip().lower() not in ("", "nan", "none"):
        rzm_vals.add(str(val).strip().lower())
print(f"\n  u_email_rzm real values (ideation): {len(rzm_vals)}")
if rzm_vals:
    print(f"  Sample: {list(rzm_vals)[:5]}")

# Check survey u_email_rzm too
with open("data_dump/gv_ideas_survey.json") as f:
    surveys = json.load(f)
rzm_survey = set()
for r in surveys:
    val = r.get("custom_field_values.u_email_rzm")
    if val and str(val).strip().lower() not in ("", "nan", "none"):
        rzm_survey.add(str(val).strip().lower())
print(f"  u_email_rzm real values (survey): {len(rzm_survey)}")
if rzm_survey:
    print(f"  Sample: {list(rzm_survey)[:5]}")
