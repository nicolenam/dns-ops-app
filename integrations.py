import hashlib
import os
import time

import requests

SENDGRID_TIMEOUT_SECONDS = 30
ACM_REUSABLE_CERTIFICATE_STATUSES = (
    "PENDING_VALIDATION",
    "ISSUED",
    "INACTIVE",
)


def use_mock_mode() -> bool:
    raw = os.getenv("DNS_OPS_USE_MOCK_MODE", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def sendgrid_base_url() -> str:
    region = os.getenv("SENDGRID_REGION", "global").strip().lower()
    if region == "global":
        return "https://api.sendgrid.com"
    if region == "eu":
        return "https://api.eu.sendgrid.com"
    raise RuntimeError("SENDGRID_REGION must be 'global' or 'eu'.")


def default_dmarc_value(domain: str) -> str:
    _ = domain
    policy = os.getenv("DMARC_POLICY", "reject").strip().lower() or "reject"
    rua = os.getenv("DMARC_RUA", "example@rep.dmarcanalyzer.com").strip()
    ruf = os.getenv("DMARC_RUF", "example@for.dmarcanalyzer.com").strip()

    parts = ["v=DMARC1", f"p={policy}"]
    if rua:
        parts.append(f"rua=mailto:{rua}")
    if ruf:
        parts.append(f"ruf=mailto:{ruf}")
    parts.append("fo=1")
    return ";".join(parts) + ";"


def _sendgrid_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_require_env('SENDGRID_API_KEY')}",
        "Content-Type": "application/json",
    }


def _sendgrid_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
) -> dict | list:
    response = requests.request(
        method,
        f"{sendgrid_base_url()}{path}",
        headers=_sendgrid_headers(),
        params=params,
        json=json_body,
        timeout=SENDGRID_TIMEOUT_SECONDS,
    )

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = ""
        try:
            body = response.json()
        except ValueError:
            body = None

        if isinstance(body, dict):
            errors = body.get("errors")
            if isinstance(errors, list) and errors:
                first_error = errors[0]
                if isinstance(first_error, dict):
                    detail = first_error.get("message", "")
                else:
                    detail = str(first_error)

        if not detail:
            detail = response.text.strip()

        message = f"SendGrid API request failed ({response.status_code})"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message) from exc

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("SendGrid API returned a non-JSON response.") from exc


def _short_hash(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()[:8]


def _normalize_hostname(value: str) -> str:
    return value.strip().rstrip(".").lower()


def _ordered_unique(values: list[str]) -> list[str]:
    seen = set()
    ordered = []

    for raw_value in values:
        value = _normalize_hostname(raw_value)
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)

    return ordered


def _wildcard_parent_from_hostname(hostname: str) -> str:
    parts = hostname.split(".")
    if len(parts) >= 3:
        return "*." + ".".join(parts[1:])
    return hostname


def _requested_certificate_names(
    dealer_domain: str,
    secure_hostname: str,
    certificate_scope: str,
) -> list[str]:
    dealer_domain = _normalize_hostname(dealer_domain)
    secure_hostname = _normalize_hostname(secure_hostname)

    if certificate_scope == "Exact hostname":
        requested_names = [secure_hostname or dealer_domain]
    elif certificate_scope == "Root + wildcard":
        requested_names = [dealer_domain, f"*.{dealer_domain}" if dealer_domain else ""]
    elif certificate_scope == "Wildcard parent domain":
        requested_names = [_wildcard_parent_from_hostname(secure_hostname or dealer_domain)]
    else:
        requested_names = [secure_hostname or dealer_domain]

    requested_names = _ordered_unique(requested_names)
    if not requested_names:
        raise RuntimeError("Dealer domain is required for AWS certificate integration.")

    return requested_names


def _relative_record_name(hostname: str, domain: str) -> str:
    hostname = _normalize_hostname(hostname)
    domain = _normalize_hostname(domain)

    if not hostname:
        return ""
    if hostname == domain:
        return "@"

    suffix = f".{domain}"
    if domain and hostname.endswith(suffix):
        return hostname[:-len(suffix)]

    return hostname


def _clean_dns_record_name(name: str) -> str:
    return name.strip().rstrip(".")


def _find_existing_sendgrid_domain(domain: str, subdomain: str | None = None) -> dict | None:
    limit = 200
    offset = 0

    while True:
        body = _sendgrid_request(
            "GET",
            "/v3/whitelabel/domains",
            params={"limit": limit, "offset": offset},
        )
        if not isinstance(body, list):
            raise RuntimeError("Unexpected SendGrid response when listing authenticated domains.")

        for item in body:
            if not isinstance(item, dict):
                continue
            if item.get("automatic_security") is False:
                continue
            if item.get("domain", "").strip().lower() != domain:
                continue
            if subdomain is not None and item.get("subdomain", "").strip().lower() != subdomain:
                continue
            return item

        if len(body) < limit:
            return None

        offset += limit


def get_sendgrid_records(domain: str) -> dict:
    domain = _normalize_hostname(domain)
    if use_mock_mode():
        suffix = _short_hash(domain)
        return {
            "sendgrid_domain_id": f"mock-sendgrid-{suffix}",
            "sendgrid_valid": False,
            "sendgrid_validation_results": None,
            "sendgrid_cname_name": f"em{suffix[:4]}",
            "sendgrid_cname_target": "u9079216.wl217.sendgrid.net",
            "dkim1_name": "s1._domainkey",
            "dkim1_target": "s1.domainkey.u9079216.wl217.sendgrid.net",
            "dkim2_name": "s2._domainkey",
            "dkim2_target": "s2.domainkey.u9079216.wl217.sendgrid.net",
            "dmarc_value": default_dmarc_value(domain),
        }

    if not domain:
        raise RuntimeError("Dealer domain is required for SendGrid integration.")

    subdomain = os.getenv("SENDGRID_SUBDOMAIN", "").strip().lower() or None
    region = os.getenv("SENDGRID_REGION", "").strip().lower() or None

    body = _find_existing_sendgrid_domain(domain, subdomain)
    if body is None:
        payload = {
            "domain": domain,
            "automatic_security": True,
            "default": False,
        }
        if subdomain is not None:
            payload["subdomain"] = subdomain
        if region is not None:
            payload["region"] = region

        body = _sendgrid_request(
            "POST",
            "/v3/whitelabel/domains",
            json_body=payload,
        )

    if not isinstance(body, dict):
        raise RuntimeError("Unexpected SendGrid response when creating an authenticated domain.")

    dns = body.get("dns")
    if not isinstance(dns, dict):
        raise RuntimeError("SendGrid did not return DNS records for the authenticated domain.")

    mail_cname = dns.get("mail_cname")
    dkim1 = dns.get("dkim1")
    dkim2 = dns.get("dkim2")
    if not all(isinstance(record, dict) for record in (mail_cname, dkim1, dkim2)):
        raise RuntimeError(
            "SendGrid did not return the expected automated-security CNAME records."
        )

    return {
        "sendgrid_domain_id": body.get("id"),
        "sendgrid_valid": bool(body.get("valid", False)),
        "sendgrid_validation_results": None,
        "sendgrid_cname_name": _relative_record_name(mail_cname.get("host", ""), domain),
        "sendgrid_cname_target": mail_cname.get("data", ""),
        "dkim1_name": _relative_record_name(dkim1.get("host", ""), domain),
        "dkim1_target": dkim1.get("data", ""),
        "dkim2_name": _relative_record_name(dkim2.get("host", ""), domain),
        "dkim2_target": dkim2.get("data", ""),
        "dmarc_value": default_dmarc_value(domain),
    }


def _aws_region() -> str:
    region = os.getenv("AWS_REGION", "").strip() or os.getenv("AWS_DEFAULT_REGION", "").strip()
    if not region:
        raise RuntimeError("Missing required environment variable: AWS_REGION")
    return region


def _aws_acm_poll_timeout_seconds() -> int:
    raw = os.getenv("AWS_ACM_POLL_TIMEOUT_SECONDS", "30").strip() or "30"
    try:
        timeout = int(raw)
    except ValueError as exc:
        raise RuntimeError("AWS_ACM_POLL_TIMEOUT_SECONDS must be an integer.") from exc
    return max(timeout, 0)


def _aws_acm_poll_interval_seconds() -> float:
    raw = os.getenv("AWS_ACM_POLL_INTERVAL_SECONDS", "2").strip() or "2"
    try:
        interval = float(raw)
    except ValueError as exc:
        raise RuntimeError("AWS_ACM_POLL_INTERVAL_SECONDS must be a number.") from exc
    return max(interval, 0.1)


def _acm_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is required for AWS ACM integration. Add boto3 to the deployment environment."
        ) from exc

    return boto3.client("acm", region_name=_aws_region())


def _aws_error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return ""

    error = response.get("Error")
    if not isinstance(error, dict):
        return ""

    return str(error.get("Code", "")).strip()


def _raise_aws_runtime_error(action: str, exc: Exception) -> None:
    response = getattr(exc, "response", None)
    detail = ""

    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict):
            code = str(error.get("Code", "")).strip()
            message = str(error.get("Message", "")).strip()
            if code and message:
                detail = f"{code}: {message}"
            elif code:
                detail = code
            elif message:
                detail = message

    if not detail:
        detail = str(exc).strip() or exc.__class__.__name__

    raise RuntimeError(f"AWS ACM {action} failed: {detail}") from exc


def _describe_acm_certificate(
    acm_client,
    certificate_arn: str,
    *,
    allow_retryable_missing: bool = False,
) -> dict | None:
    try:
        response = acm_client.describe_certificate(CertificateArn=certificate_arn)
    except Exception as exc:
        if allow_retryable_missing and _aws_error_code(exc) in {
            "RequestInProgressException",
            "ResourceNotFoundException",
        }:
            return None
        _raise_aws_runtime_error("describe certificate", exc)

    certificate = response.get("Certificate")
    if not isinstance(certificate, dict):
        raise RuntimeError("AWS ACM returned an unexpected certificate response.")

    return certificate


def _certificate_identity_names(certificate: dict) -> set[str]:
    names = [certificate.get("DomainName", "")]
    sans = certificate.get("SubjectAlternativeNames")
    if isinstance(sans, list):
        names.extend(str(name) for name in sans)
    return set(_ordered_unique(names))


def _certificate_status_rank(certificate: dict) -> tuple[int, float]:
    status = str(certificate.get("Status", "")).upper()
    status_rank = {
        "ISSUED": 0,
        "PENDING_VALIDATION": 1,
        "INACTIVE": 2,
    }.get(status, 99)

    created_at = certificate.get("CreatedAt")
    created_at_ts = created_at.timestamp() if hasattr(created_at, "timestamp") else 0.0
    return (status_rank, -created_at_ts)


def _find_existing_acm_certificate(acm_client, requested_names: list[str]) -> dict | None:
    requested_name_set = set(requested_names)
    matches = []

    try:
        paginator = acm_client.get_paginator("list_certificates")
        pages = paginator.paginate(
            CertificateStatuses=list(ACM_REUSABLE_CERTIFICATE_STATUSES),
        )
    except Exception as exc:
        _raise_aws_runtime_error("list certificates", exc)

    for page in pages:
        summaries = page.get("CertificateSummaryList")
        if not isinstance(summaries, list):
            continue

        for summary in summaries:
            if not isinstance(summary, dict):
                continue

            summary_domain = _normalize_hostname(str(summary.get("DomainName", "")))
            if summary_domain and summary_domain not in requested_name_set:
                continue

            certificate_arn = str(summary.get("CertificateArn", "")).strip()
            if not certificate_arn:
                continue

            certificate = _describe_acm_certificate(acm_client, certificate_arn)
            if certificate is None:
                continue

            if _certificate_identity_names(certificate) == requested_name_set:
                matches.append(certificate)

    if not matches:
        return None

    return sorted(matches, key=_certificate_status_rank)[0]


def _certificate_idempotency_token(requested_names: list[str]) -> str:
    seed = "|".join(requested_names)
    return hashlib.md5(seed.encode()).hexdigest()[:32]


def _request_acm_certificate(acm_client, requested_names: list[str]) -> str:
    payload = {
        "DomainName": requested_names[0],
        "ValidationMethod": "DNS",
        "IdempotencyToken": _certificate_idempotency_token(requested_names),
    }
    if len(requested_names) > 1:
        payload["SubjectAlternativeNames"] = requested_names[1:]

    try:
        response = acm_client.request_certificate(**payload)
    except Exception as exc:
        _raise_aws_runtime_error("request certificate", exc)

    certificate_arn = str(response.get("CertificateArn", "")).strip()
    if not certificate_arn:
        raise RuntimeError("AWS ACM did not return a certificate ARN.")

    return certificate_arn


def _extract_acm_validation_records(certificate: dict) -> list[dict[str, str | None]]:
    options = certificate.get("DomainValidationOptions")
    if not isinstance(options, list):
        return []

    records = []
    seen = set()

    for option in options:
        if not isinstance(option, dict):
            continue

        resource_record = option.get("ResourceRecord")
        if not isinstance(resource_record, dict):
            continue

        record_type = str(resource_record.get("Type", "")).strip().upper()
        name = _clean_dns_record_name(str(resource_record.get("Name", "")))
        value = str(resource_record.get("Value", "")).strip()
        if not (record_type and name and value):
            continue

        key = (record_type, name, value)
        if key in seen:
            continue
        seen.add(key)

        records.append(
            {
                "domain_name": _normalize_hostname(str(option.get("DomainName", ""))),
                "validation_status": str(option.get("ValidationStatus", "")).strip() or None,
                "name": name,
                "type": record_type,
                "value": value,
            }
        )

    return records


def _poll_acm_certificate_until_ready(acm_client, certificate_arn: str) -> dict:
    timeout_seconds = _aws_acm_poll_timeout_seconds()
    interval_seconds = _aws_acm_poll_interval_seconds()
    deadline = time.time() + timeout_seconds
    last_certificate = None

    while True:
        certificate = _describe_acm_certificate(
            acm_client,
            certificate_arn,
            allow_retryable_missing=True,
        )

        if certificate is not None:
            last_certificate = certificate
            status = str(certificate.get("Status", "")).upper()
            if status in {"FAILED", "VALIDATION_TIMED_OUT", "REVOKED", "EXPIRED"}:
                raise RuntimeError(f"AWS ACM returned certificate status {status}.")

            validation_records = _extract_acm_validation_records(certificate)
            if validation_records or status == "ISSUED":
                return certificate

        if time.time() >= deadline:
            if last_certificate is not None:
                return last_certificate
            raise RuntimeError(
                "AWS ACM did not return certificate details before the poll timeout."
            )

        time.sleep(interval_seconds)


def get_aws_certificate_record(
    dealer_domain: str,
    secure_hostname: str,
    certificate_scope: str,
) -> dict:
    requested_names = _requested_certificate_names(
        dealer_domain,
        secure_hostname,
        certificate_scope,
    )

    if use_mock_mode():
        validation_records = []
        for requested_name in requested_names:
            suffix = _short_hash(requested_name)
            validation_records.append(
                {
                    "domain_name": requested_name,
                    "validation_status": "PENDING_VALIDATION",
                    "name": f"_{suffix}acmvalidation.{requested_name}",
                    "type": "CNAME",
                    "value": f"_{suffix}.jkddzztszm.acm-validations.aws.",
                }
            )

        first_record = validation_records[0]
        return {
            "certificate_arn": f"arn:aws:acm:mock:000000000000:certificate/{_short_hash('|'.join(requested_names))}",
            "certificate_status": "PENDING_VALIDATION",
            "requested_names": requested_names,
            "cert_validation_records": validation_records,
            "cert_cname_name": first_record["name"],
            "cert_cname_target": first_record["value"],
        }

    acm_client = _acm_client()
    certificate = _find_existing_acm_certificate(acm_client, requested_names)

    if certificate is None:
        certificate_arn = _request_acm_certificate(acm_client, requested_names)
        certificate = _poll_acm_certificate_until_ready(acm_client, certificate_arn)
    else:
        certificate_arn = str(certificate.get("CertificateArn", "")).strip()
        certificate = _poll_acm_certificate_until_ready(acm_client, certificate_arn)

    certificate_arn = str(certificate.get("CertificateArn", "")).strip()
    certificate_status = str(certificate.get("Status", "")).strip() or None
    validation_records = _extract_acm_validation_records(certificate)

    if not validation_records and certificate_status != "ISSUED":
        raise RuntimeError(
            "AWS ACM did not return DNS validation records for the requested certificate."
        )

    first_record = validation_records[0] if validation_records else {"name": "", "value": ""}

    return {
        "certificate_arn": certificate_arn,
        "certificate_status": certificate_status,
        "requested_names": requested_names,
        "cert_validation_records": validation_records,
        "cert_cname_name": first_record["name"],
        "cert_cname_target": first_record["value"],
    }


def get_subdomain_target(domain: str, load_balancer: str) -> str:
    if load_balancer == "old":
        return f"{domain}.toolbxapp.com"
    return "dns.web1.toolbxapp.com"


def get_main_domain_records(domain: str, load_balancer: str) -> dict:
    if load_balancer == "old":
        return {
            "a1": "18.235.90.157",
            "a2": "44.207.126.26",
            "www": domain,
        }

    return {
        "a1": "44.196.245.65",
        "a2": "44.207.244.224",
        "www": domain,
    }
