"""Quick script to inspect dumped data for participant counting."""
import json

# --- Survey ideas ---
with open("data_dump/gv_ideas_survey.json") as f:
    surveys = json.load(f)

e1 = [r.get("custom_field_values.u_email_5vp") for r in surveys if r.get("custom_field_values.u_email_5vp")]
e2 = [r.get("custom_field_values.u_email_rzm") for r in surveys if r.get("custom_field_values.u_email_rzm")]
print("=== Survey Ideas ===")
print(f"  Total rows: {len(surveys)}")
print(f"  u_email_5vp non-null: {len(e1)}")
print(f"  u_email_rzm non-null: {len(e2)}")
print(f"  Sample e1: {e1[:3]}")
print(f"  Sample e2: {e2[:3]}")

null_auth = [r for r in surveys if r.get("author_id") is None]
has_auth = [r for r in surveys if r.get("author_id") is not None]
print(f"  Null author_id: {len(null_auth)}")
print(f"  Has author_id: {len(has_auth)}")

null_with_email = sum(
    1 for r in null_auth
    if r.get("custom_field_values.u_email_5vp") or r.get("custom_field_values.u_email_rzm")
)
null_no_email = len(null_auth) - null_with_email
print(f"  Null author + HAS email in custom fields (email-only): {null_with_email}")
print(f"  Null author + NO email (truly anonymous): {null_no_email}")

# Unique emails from custom fields across all survey ideas
all_survey_emails = set()
for r in surveys:
    for col in ("custom_field_values.u_email_5vp", "custom_field_values.u_email_rzm"):
        val = r.get(col)
        if val and str(val).strip().lower() not in ("", "nan", "none"):
            all_survey_emails.add(str(val).strip().lower())
print(f"  Unique emails from custom fields: {len(all_survey_emails)}")

# --- Ideation ideas ---
with open("data_dump/gv_ideas_ideation.json") as f:
    ideation = json.load(f)

print(f"\n=== Ideation Ideas ===")
print(f"  Total rows: {len(ideation)}")
email_cols_i = [k for k in ideation[0].keys() if "email" in k.lower()] if ideation else []
print(f"  Email columns: {email_cols_i}")
null_auth_i = sum(1 for r in ideation if r.get("author_id") is None)
print(f"  Null author_id: {null_auth_i}")
print(f"  Has author_id: {len(ideation) - null_auth_i}")

# --- GV Users ---
with open("data_dump/gv_users.json") as f:
    users = json.load(f)

user_emails = set()
for u in users:
    e = u.get("email")
    if e and str(e).strip().lower() not in ("", "nan", "none"):
        user_emails.add(str(e).strip().lower())
print(f"\n=== GV Users ===")
print(f"  Total rows: {len(users)}")
print(f"  Unique emails: {len(user_emails)}")

# --- Typeform ---
for fname in ("tf_KdHzkJeL.json", "tf_PmPIQkd8.json", "tf_YcnYy8ah.json"):
    with open(f"data_dump/{fname}") as f:
        tf = json.load(f)
    tf_emails = set()
    tf_anon = 0
    for r in tf:
        email = r.get("email") or r.get("hidden_email")
        if email and str(email).strip().lower() not in ("", "nan", "none"):
            tf_emails.add(str(email).strip().lower())
        else:
            tf_anon += 1
    print(f"\n=== {fname} ===")
    print(f"  Total rows: {len(tf)}")
    print(f"  Unique emails: {len(tf_emails)}")
    print(f"  Anonymous (no email): {tf_anon}")

# --- Cross-source overlap ---
print("\n=== Cross-Source Analysis ===")
# Survey custom field emails that ARE in gv_users (confirmed)
overlap_survey_users = all_survey_emails & user_emails
email_only_survey = all_survey_emails - user_emails
print(f"  Survey emails also in gv_users (confirmed): {len(overlap_survey_users)}")
print(f"  Survey emails NOT in gv_users (email-only): {len(email_only_survey)}")

# Collect all typeform emails
all_tf_emails = set()
for fname in ("tf_KdHzkJeL.json", "tf_PmPIQkd8.json", "tf_YcnYy8ah.json"):
    with open(f"data_dump/{fname}") as f:
        tf = json.load(f)
    for r in tf:
        email = r.get("email") or r.get("hidden_email")
        if email and str(email).strip().lower() not in ("", "nan", "none"):
            all_tf_emails.add(str(email).strip().lower())

overlap_tf_users = all_tf_emails & user_emails
email_only_tf = all_tf_emails - user_emails
print(f"  Typeform emails also in gv_users (confirmed): {len(overlap_tf_users)}")
print(f"  Typeform emails NOT in gv_users (email-only): {len(email_only_tf)}")

# All email-only (from ideas + typeform, not in gv_users)
all_action_emails = all_survey_emails | all_tf_emails
all_email_only = all_action_emails - user_emails
print(f"\n  Total unique action emails (ideas + typeform): {len(all_action_emails)}")
print(f"  Total email-only users (not in gv_users): {len(all_email_only)}")
print(f"  Total confirmed users (gv_users): {len(user_emails)}")
