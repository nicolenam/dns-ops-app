import hashlib
import os

import requests

SENDGRID_TIMEOUT_SECONDS = 30


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


def _wildcard_parent_from_hostname(hostname: str) -> str:
    parts = hostname.split(".")
    if len(parts) >= 3:
        return "*." + ".".join(parts[1:])
    return hostname


def _relative_record_name(hostname: str, domain: str) -> str:
    hostname = hostname.strip().rstrip(".").lower()
    domain = domain.strip().rstrip(".").lower()

    if not hostname:
        return ""
    if hostname == domain:
        return "@"

    suffix = f".{domain}"
    if domain and hostname.endswith(suffix):
        return hostname[:-len(suffix)]

    return hostname


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
    domain = domain.strip().lower()
    if use_mock_mode():
        suffix = _short_hash(domain)
        return {
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


def get_aws_certificate_record(
    dealer_domain: str,
    secure_hostname: str,
    certificate_scope: str,
) -> dict:
    if use_mock_mode():
        dealer_domain = dealer_domain.strip().lower()
        secure_hostname = secure_hostname.strip().lower()

        if certificate_scope == "Exact hostname":
            requested_names = [secure_hostname or dealer_domain]

        elif certificate_scope == "Root + wildcard":
            requested_names = [dealer_domain, f"*.{dealer_domain}"]

        elif certificate_scope == "Wildcard parent domain":
            wildcard_name = _wildcard_parent_from_hostname(secure_hostname or dealer_domain)
            requested_names = [wildcard_name]

        else:
            requested_names = [secure_hostname or dealer_domain]

        seed = "|".join(requested_names)
        suffix = _short_hash(seed)

        return {
            "cert_cname_name": f"_{suffix}acmvalidation",
            "cert_cname_target": f"_{suffix}.jkddzztszm.acm-validations.aws.",
            "requested_names": requested_names,
        }

    raise NotImplementedError("Real AWS integration not added yet.")


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
