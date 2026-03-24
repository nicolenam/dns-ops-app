# AWS and SendGrid Integration Outline

This document started as the implementation outline for replacing mock data in `integrations.py` with live AWS and SendGrid API calls while keeping the Streamlit UI in `app.py` mostly unchanged.

Current note:

- Live SendGrid and AWS ACM support now exist in `integrations.py`.
- Mock mode is still available for demos and local UI work.
- This document remains useful as a design/reference note, but some of the "not implemented yet" sections are historical.

## What the app already does

`app.py` calls two integration functions when the user clicks `Generate records`:

- `get_sendgrid_records(domain)`
- `get_aws_certificate_record(dealer_domain, secure_hostname, certificate_scope)`

Today, both functions return placeholder values when `DNS_OPS_USE_MOCK_MODE=true` and `integrations.py` is running in mock mode.

The UI already has fields for:

- SendGrid mail CNAME
- SendGrid DKIM 1 CNAME
- SendGrid DKIM 2 CNAME
- DMARC TXT value
- AWS certificate validation CNAME
- Requested certificate names

That means the main work is in the integration layer, not the Streamlit layout.

## Target behavior

When the integration is complete, the app should behave like this:

- `SendGrid + certificate prep`
  - call SendGrid to create or reuse domain authentication
  - call AWS ACM to create or reuse a certificate request
  - display all DNS records needed for both systems
- `SendGrid only`
  - call SendGrid only
  - display only the SendGrid DNS records
- `Certificate only`
  - call AWS ACM only
  - display only the ACM validation DNS records
- `Subdomain go-live`
  - no SendGrid or ACM call required
  - continue using the app's internal record-generation logic
- `Main domain go-live`
  - no SendGrid or ACM call required
  - continue using the app's internal record-generation logic
- `WordPress update`
  - no SendGrid or ACM call required
  - continue using the app's existing internal flow

So the external integration work only applies to these three workflows:

- `SendGrid + certificate prep`
- `SendGrid only`
- `Certificate only`

## End-to-end flow when the user clicks `Generate records`

This is the clearest way to think about the integration:

1. The user fills in the Streamlit form in `app.py`.
2. `app.py` validates the required fields.
3. Based on the selected workflow, `app.py` calls one or both integration functions in `integrations.py`.
4. `integrations.py` talks to SendGrid and/or AWS.
5. `integrations.py` returns normalized record data in the shapes the UI expects.
6. `app.py` stores those values in `st.session_state`.
7. The preview panel, checklist, and email template render from session state.
8. If Route 53 automation is later enabled, record creation can happen after step 4 and before step 5.

## Data contract between `app.py` and `integrations.py`

The integration layer should hide provider-specific response details from the UI. `app.py` should receive normalized dictionaries and should not need to know SendGrid or ACM response formats directly.

### SendGrid return contract

`get_sendgrid_records(domain)` should return:

```python
{
    "sendgrid_domain_id": int | str,
    "sendgrid_valid": bool,
    "sendgrid_validation_results": dict | None,
    "sendgrid_cname_name": str,
    "sendgrid_cname_target": str,
    "dkim1_name": str,
    "dkim1_target": str,
    "dkim2_name": str,
    "dkim2_target": str,
    "dmarc_value": str,
}
```

### AWS return contract

`get_aws_certificate_record(dealer_domain, secure_hostname, certificate_scope)` should return:

```python
{
    "certificate_arn": str,
    "certificate_status": str | None,
    "requested_names": list[str],
    "cert_validation_records": [
        {
            "domain_name": str,
            "validation_status": str | None,
            "name": str,
            "type": str,
            "value": str,
        }
    ],
    "cert_cname_name": str,
    "cert_cname_target": str,
}
```

Notes:

- `cert_cname_name` and `cert_cname_target` can be the first validation record for backward compatibility.
- `cert_validation_records` is the real source of truth once the UI is updated.
- `dmarc_value` remains app-generated unless you later decide to manage DMARC separately.

## Recommended integration approach

Use:

- `boto3` for AWS Certificate Manager and optional Route 53 automation
- `requests` for SendGrid's REST API
- environment variables or Streamlit secrets for credentials

The long-term target should be a live-only app that talks directly to AWS and SendGrid.

If you want a smoother build phase, you can temporarily keep a mock mode for local UI work or demos, but it is optional and can be removed once the live integrations are stable.

If mock mode is kept during implementation, make it config-driven rather than hard-coded so it can be turned off without editing source files.

Example environment variables:

```bash
AWS_REGION=us-east-1
SENDGRID_API_KEY=your-sendgrid-api-key
SENDGRID_REGION=global
SENDGRID_SUBDOMAIN=em
ROUTE53_AUTO_CREATE=false
ROUTE53_HOSTED_ZONE_ID=
```

Optional temporary dev setting:

```bash
DNS_OPS_USE_MOCK_MODE=false
```

Notes:

- Prefer IAM roles or short-lived AWS credentials over hard-coded access keys.
- In Streamlit, these can also live in `.streamlit/secrets.toml`.

## What is actually required to make this work

It is not just "an AWS key and a SendGrid key."

There are two separate requirements:

1. The live integration code has to be implemented in `integrations.py`.
2. The app needs the right credentials, config, and permissions at runtime.

### Minimum required for a manual-DNS version

This is the version where the app generates the DNS records, but a person still adds them at the DNS provider.

Required:

- SendGrid API key with domain authentication access
- AWS credentials that `boto3` can use
- AWS region
- access to the dealer domain's DNS provider so the returned records can actually be added

That means AWS can be authenticated through any of these:

- access key and secret key
- AWS profile
- AWS SSO
- IAM role

### Additional required config

At minimum, expect to configure:

```bash
AWS_REGION=us-east-1
SENDGRID_API_KEY=your-sendgrid-api-key
SENDGRID_REGION=global
SENDGRID_SUBDOMAIN=em
```

Notes:

- `SENDGRID_REGION` is only needed if you must support the EU SendGrid API base URL.
- `SENDGRID_SUBDOMAIN` is the branded sending subdomain used for domain authentication.

### Required AWS permissions

For the manual-DNS version, AWS needs permission to:

- request ACM certificates
- describe ACM certificates

If Route 53 automation is added later, AWS also needs permission to:

- list hosted zones
- change Route 53 record sets

### Required SendGrid permissions

SendGrid needs permission to:

- create authenticated domains
- retrieve authenticated domain details
- validate authenticated domains

### Important limitation right now

Even if all credentials are present today, the app will not work end-to-end yet because the live SendGrid and ACM code paths are not implemented in `integrations.py`.

So the real requirement is:

- provider credentials
- provider permissions
- DNS access
- completed live integration code

## Implementation plan by phase

### Phase 1: prepare the integration layer

Update `integrations.py` so it can host real provider logic cleanly.

Add:

- config helpers for AWS region, SendGrid base URL, and optional Route 53 settings
- small normalization helpers for DNS names and values
- a DMARC helper for the default TXT value
- provider-specific helper functions instead of putting all request logic inline

The goal of this phase is to make `integrations.py` the only place that knows about:

- SendGrid URLs and headers
- boto3 ACM calls
- Route 53 calls
- response parsing

### Phase 2: implement SendGrid domain authentication

Replace the placeholder `get_sendgrid_records(domain)` logic with real API calls.

This phase should:

1. build the SendGrid request payload
2. create or reuse the authenticated domain
3. parse the returned DNS records
4. return the normalized SendGrid contract
5. leave DMARC generation inside the app's own logic/helper

### Phase 3: implement ACM certificate requests

Replace the placeholder `get_aws_certificate_record(...)` logic with real ACM calls.

This phase should:

1. map the selected certificate scope to requested names
2. request or reuse the ACM certificate
3. poll until validation records are available
4. normalize all returned validation records
5. return the AWS contract back to `app.py`

### Phase 4: expand the UI to support real ACM responses

The current app shape is only fully safe for a single certificate validation record.

This phase should:

1. add `cert_validation_records` to session state
2. update the preview builder to render all validation records
3. update the email template to render all validation records
4. update the checklist if it references certificate validation data

### Phase 5: optional Route 53 automation

Only do this if the customer domain is actually hosted in Route 53.

This phase should:

1. detect the hosted zone
2. build `UPSERT` changes for SendGrid and ACM records
3. submit the change batch
4. store enough information to show the user what was created automatically

### Phase 6: persistence and idempotency

This phase prevents duplicate external resources.

It should cover:

- SendGrid authenticated domain reuse
- ACM certificate reuse or idempotent requests
- optional storage of provider resource IDs by dealer/domain

Without this phase, repeated button clicks can create duplicate provider-side resources.

## SendGrid integration

### Goal

Create or reuse a SendGrid authenticated domain and return the DNS records the UI already expects.

### API flow

1. Build the request using the dealer's root domain.
2. Call SendGrid Domain Authentication.
3. Read the DNS records from the response.
4. Map those records into the existing app fields.
5. Optionally validate the authenticated domain after DNS is published.

### Why `automatic_security=true`

The current UI expects CNAME-based records:

- mail CNAME
- DKIM 1 CNAME
- DKIM 2 CNAME

That matches SendGrid's automated security mode. Twilio SendGrid documents that automated security returns 3 CNAME records, while manual security returns 2 TXT records and 1 MX record. To fit the current UI, the integration should use automated security.

### Request shape

Use:

- `POST /v3/whitelabel/domains`
- base URL `https://api.sendgrid.com` for global accounts
- base URL `https://api.eu.sendgrid.com` for EU regional subusers

Suggested payload:

```json
{
  "domain": "example.com",
  "subdomain": "em",
  "automatic_security": true,
  "default": false,
  "region": "global"
}
```

### Response mapping

Map SendGrid's response to the app like this:

| App field               | SendGrid source                                                      |
| ----------------------- | -------------------------------------------------------------------- |
| `sendgrid_cname_name`   | `dns.mail_cname.host` stripped down to the relative label if desired |
| `sendgrid_cname_target` | `dns.mail_cname.data`                                                |
| `dkim1_name`            | `dns.dkim1.host` stripped down to the relative label if desired      |
| `dkim1_target`          | `dns.dkim1.data`                                                     |
| `dkim2_name`            | `dns.dkim2.host` stripped down to the relative label if desired      |
| `dkim2_target`          | `dns.dkim2.data`                                                     |
| `dmarc_value`           | generated locally by this app, not returned by SendGrid domain auth  |

Important:

- SendGrid domain authentication does not create the DMARC value currently shown in this app.
- The app should keep generating a default DMARC TXT record locally or make DMARC configurable in the UI.

### Validation step

After the DNS records are published, optionally call:

- `POST /v3/whitelabel/domains/{id}/validate`

Store:

- the authenticated domain `id`
- whether SendGrid reports it as `valid`
- any `validation_results` for troubleshooting

### Duplicate prevention

Avoid creating a brand-new SendGrid authenticated domain every time the button is clicked.

Recommended options:

- persist the SendGrid domain `id` by dealer/domain
- or look up an existing authenticated domain before creating a new one

Without this, repeated clicks can create duplicate external resources.

### Example Python shape

```python
def get_sendgrid_records(domain: str) -> dict:
    if use_mock_mode():
        ...

    payload = {
        "domain": domain,
        "subdomain": os.getenv("SENDGRID_SUBDOMAIN", "em"),
        "automatic_security": True,
        "default": False,
        "region": os.getenv("SENDGRID_REGION", "global"),
    }

    response = requests.post(
        f"{sendgrid_base_url()}/v3/whitelabel/domains",
        headers={
            "Authorization": f"Bearer {os.environ['SENDGRID_API_KEY']}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    dns = body["dns"]

    return {
        "sendgrid_domain_id": body["id"],
        "sendgrid_valid": body.get("valid", False),
        "sendgrid_cname_name": dns["mail_cname"]["host"],
        "sendgrid_cname_target": dns["mail_cname"]["data"],
        "dkim1_name": dns["dkim1"]["host"],
        "dkim1_target": dns["dkim1"]["data"],
        "dkim2_name": dns["dkim2"]["host"],
        "dkim2_target": dns["dkim2"]["data"],
        "dmarc_value": default_dmarc_value(domain),
    }
```

## AWS certificate integration

### Goal

Request an ACM certificate, read the DNS validation CNAME record(s), and return them to the app.

### Certificate scope mapping

The current UI exposes two certificate scopes:

- `Exact hostname`
- `Root + wildcard`

Map them like this:

- `Exact hostname`
  - `DomainName = secure_hostname`
  - no SANs
- `Root + wildcard`
  - `DomainName = dealer_domain`
  - `SubjectAlternativeNames = [f"*.{dealer_domain}"]`

### API flow

1. Build the requested names from the selected certificate scope.
2. Call `acm.request_certificate(...)` with `ValidationMethod="DNS"`.
3. Read the `CertificateArn` from the response.
4. Poll `acm.describe_certificate(...)` until `DomainValidationOptions[].ResourceRecord` is present.
5. Return the validation CNAME record(s) plus the requested names.

### What ACM returns

ACM exposes certificate validation details through `DomainValidationOptions`. Each item may include:

- `DomainName`
- `ValidationStatus`
- `ResourceRecord.Name`
- `ResourceRecord.Type`
- `ResourceRecord.Value`

### Important app-model gap

The current app only stores one certificate validation record:

- `cert_cname_name`
- `cert_cname_target`

That is fine for the simplest case, but ACM returns a list of validation options. For `Root + wildcard`, you should not assume the real integration will always fit into one CNAME pair.

Recommended improvement:

- add `cert_validation_records: list[dict]` to session state
- update the preview/email/checklist builders to render all returned validation records

If you want a minimal first pass, you can support live ACM only for `Exact hostname` first and leave `Root + wildcard` on a follow-up task.

### Example Python shape

```python
def get_aws_certificate_record(
    dealer_domain: str,
    secure_hostname: str,
    certificate_scope: str,
) -> dict:
    if use_mock_mode():
        ...

    acm = boto3.client("acm", region_name=os.environ["AWS_REGION"])

    if certificate_scope == "Exact hostname":
        domain_name = secure_hostname.strip().lower()
        sans = []
        requested_names = [domain_name]
    else:
        root = dealer_domain.strip().lower()
        domain_name = root
        sans = [f"*.{root}"]
        requested_names = [root, f"*.{root}"]

    response = acm.request_certificate(
        DomainName=domain_name,
        SubjectAlternativeNames=sans,
        ValidationMethod="DNS",
        IdempotencyToken=stable_token_for(requested_names),
    )

    certificate_arn = response["CertificateArn"]
    certificate = wait_for_validation_options(acm, certificate_arn)
    options = certificate["Certificate"]["DomainValidationOptions"]

    first_record = options[0]["ResourceRecord"]

    return {
        "certificate_arn": certificate_arn,
        "requested_names": requested_names,
        "cert_validation_records": [
            {
                "domain_name": item["DomainName"],
                "validation_status": item.get("ValidationStatus"),
                "name": item["ResourceRecord"]["Name"],
                "type": item["ResourceRecord"]["Type"],
                "value": item["ResourceRecord"]["Value"],
            }
            for item in options
            if "ResourceRecord" in item
        ],
        "cert_cname_name": first_record["Name"],
        "cert_cname_target": first_record["Value"],
    }
```

### Duplicate prevention

Avoid requesting a new ACM certificate on every click.

Recommended protections:

- use `IdempotencyToken`
- persist the `CertificateArn` by request
- optionally search for an existing matching certificate before creating another one

## Optional Route 53 automation

If you control the customer's DNS in Route 53, you can go one step further and write the records automatically instead of only displaying them.

Use:

- `route53.change_resource_record_sets(...)`

Recommended behavior:

1. Detect whether the dealer domain is hosted in Route 53.
2. If yes, `UPSERT` the SendGrid and/or ACM records.
3. If no, keep the current manual workflow and show the records in the UI/email/checklist.

This should be optional, because many dealer domains will likely be hosted outside AWS.

## Suggested code changes in this repo

### `integrations.py`

Replace the mock-only implementation with the live integrations below:

- `get_sendgrid_records(domain)` as the SendGrid entrypoint
- `get_aws_certificate_record(...)` as the ACM entrypoint
- `sendgrid_base_url()` helper
- `default_dmarc_value(domain)` helper
- `create_or_get_sendgrid_domain(...)` helper
- `validate_sendgrid_domain(...)` helper
- `request_or_get_certificate(...)` helper
- `wait_for_validation_options(...)` helper
- `normalize_record_name(...)` helper
- optional Route 53 helper for DNS auto-create
- optional temporary mock-mode helper only if needed during the build

`integrations.py` should become the only module that knows how to talk to external providers.

### `app.py`

Add session fields for live-resource metadata:

- `sendgrid_domain_id`
- `sendgrid_valid`
- `sendgrid_validation_results`
- `certificate_arn`
- `certificate_status`
- `cert_validation_records`

Then update these app areas:

- the `Generate records` button handler to store the new returned fields
- the generated records preview to render multiple certificate validation records
- the email template to render multiple certificate validation records
- the checklist builder if it references certificate validation details
- the mock-mode banner logic only if mock mode is temporarily retained

`app.py` should continue to be the orchestration/UI layer, not the provider-integration layer.

## Security and operational notes

- Never hard-code API keys or AWS secrets in source control.
- Use least-privilege IAM permissions.
- Give the SendGrid API key only the scopes needed for domain authentication.
- Add request timeouts and retry handling for transient API failures.
- Log external API failures, but do not log secrets.
- Treat SendGrid domain creation and ACM certificate requests as external resources that need idempotent handling.

## Recommended rollout order

1. Refactor `integrations.py` into clean provider helpers.
2. Implement live SendGrid domain authentication.
3. Implement live ACM certificate requests for `Exact hostname`.
4. Update `app.py` to support the full AWS return contract, including multiple validation records.
5. Extend ACM support confidently for `Root + wildcard`.
6. Add optional Route 53 automation.
7. Add idempotency and persistence so repeated clicks do not create duplicate resources.
8. Remove mock mode entirely, or keep it only as a temporary dev-only switch while rollout is in progress.

## References

Checked against official docs on March 23, 2026:

- Twilio SendGrid Domain Authentication overview: https://www.twilio.com/docs/sendgrid/api-reference/domain-authentication
- Twilio SendGrid authenticate a domain: https://www.twilio.com/docs/sendgrid/api-reference/domain-authentication/authenticate-a-domain
- Twilio SendGrid validate a domain authentication: https://www.twilio.com/docs/sendgrid/api-reference/domain-authentication/validate-a-domain-authentication
- AWS Boto3 ACM `request_certificate`: https://docs.aws.amazon.com/boto3/latest/reference/services/acm/client/request_certificate.html
- AWS Boto3 ACM `describe_certificate`: https://docs.aws.amazon.com/boto3/latest/reference/services/acm/client/describe_certificate.html
- AWS Boto3 Route 53 `change_resource_record_sets`: https://docs.aws.amazon.com/boto3/latest/reference/services/route53/client/change_resource_record_sets.html
