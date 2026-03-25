import streamlit as st
from integrations import (
    use_mock_mode,
    get_sendgrid_records,
    get_aws_certificate_record,
    get_subdomain_target,
    get_main_domain_records,
)

st.set_page_config(page_title="DNS Ops Helper", layout="wide")
st.title("DNS Ops Helper")
st.caption("Use mock mode for demos or connect real SendGrid and AWS credentials for live records.")
if use_mock_mode():
    st.info("Mock mode is ON. This app is generating placeholder records only.")
else:
    st.warning("Live mode is selected. Generate records will use real provider integrations where available.")
    
st.markdown(
    """
    <style>
    [data-testid="stHeaderActionElements"] {
        display: none;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

DEFAULTS = {
    "workflow": "SendGrid + certificate prep",
    "dealer_name": "",
    "dealer_domain": "",
    "go_live_timing": "",
    "load_balancer": "new",
    "subdomain_label": "portal",
    "secure_hostname": "",
    "certificate_scope": "Exact hostname",
    "sendgrid_cname_name": "",
    "sendgrid_cname_target": "",
    "dkim1_name": "",
    "dkim1_target": "",
    "dkim2_name": "",
    "dkim2_target": "",
    "dmarc_value": "",
    "cert_cname_name": "",
    "cert_cname_target": "",
    "cert_requested_names": [],
    "cert_validation_records": [],
    "cert_status": "",
    "email_draft": "",
    "checklist": "",
    "last_action": "None",
}

for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value

WORKFLOWS = [
    "SendGrid + certificate prep",
    "SendGrid only",
    "Certificate only",
    "Subdomain go-live",
    "Main domain go-live",
    "WordPress update",
]

CERTIFICATE_SCOPES = [
    "Exact hostname",
    "Root + wildcard",
]

st.selectbox(
    "Workflow *",
    WORKFLOWS,
    key="workflow",
    accept_new_options=False,
)

workflow = st.session_state.workflow



with st.expander("What do the certificate scope options mean?"):
    st.markdown(
        """
- **Exact hostname**: only the hostname you enter  
  Example: `shop.bolyardlumber.com`

- **Root + wildcard**: the root domain plus one-level wildcard  
  Example: `bolyardlumber.com` and `*.bolyardlumber.com`
"""
    )


def validate_required_fields() -> list[str]:
    errors = []

    if not st.session_state.dealer_name.strip():
        errors.append("Dealer name is required.")

    if not st.session_state.dealer_domain.strip():
        errors.append("Dealer domain is required.")

    if workflow == "Subdomain go-live" and not st.session_state.subdomain_label.strip():
        errors.append("Subdomain label is required for Subdomain go-live.")

    if workflow in {"SendGrid + certificate prep", "Certificate only"}:
        if (
            st.session_state.certificate_scope == "Exact hostname"
            and not st.session_state.secure_hostname.strip()
        ):
            errors.append(
                "Hostname to secure is required when certificate scope is Exact hostname."
            )

    return errors


def get_certificate_subject_text() -> str:
    dealer_domain = st.session_state.dealer_domain.strip().lower()
    secure_hostname = st.session_state.secure_hostname.strip().lower()
    certificate_scope = st.session_state.certificate_scope

    if certificate_scope == "Exact hostname":
        return secure_hostname or "[hostname]"

    if certificate_scope == "Root + wildcard":
        return (
            f"{dealer_domain} and *.{dealer_domain}"
            if dealer_domain
            else "[root and wildcard domain]"
        )

    return secure_hostname or dealer_domain or "[hostname]"


def get_certificate_validation_records() -> list[dict[str, str]]:
    records = []

    for record in st.session_state.cert_validation_records:
        if not isinstance(record, dict):
            continue

        record_type = str(record.get("type", "")).strip().upper() or "CNAME"
        name = str(record.get("name", "")).strip()
        value = str(record.get("value", "")).strip()
        if not (name and value):
            continue

        records.append(
            {
                "type": record_type,
                "name": name,
                "value": value,
            }
        )

    if records:
        return records

    cert_cname_name = st.session_state.cert_cname_name.strip()
    cert_cname_target = st.session_state.cert_cname_target.strip()
    if cert_cname_name and cert_cname_target:
        return [
            {
                "type": "CNAME",
                "name": cert_cname_name,
                "value": cert_cname_target,
            }
        ]

    return []


def _short_certificate_record_name(name: str, domain_name: str) -> str:
    display_name = name.strip().rstrip(".")
    validation_domain = domain_name.strip().lower().rstrip(".")
    if validation_domain.startswith("*."):
        validation_domain = validation_domain[2:]

    suffix = f".{validation_domain}"
    if validation_domain and display_name.lower().endswith(suffix):
        return display_name[:-len(suffix)]

    return display_name


def format_dns_blocks(records: list[dict[str, str]]) -> str:
    return "\n\n".join(
        f"""{record['type']}
Name: {record['name']}
Value: {record['value']}"""
        for record in records
    )


def build_certificate_email_section(heading: str) -> str:
    records = []
    for record in st.session_state.cert_validation_records:
        if not isinstance(record, dict):
            continue

        record_type = str(record.get("type", "")).strip().upper() or "CNAME"
        name = str(record.get("name", "")).strip()
        value = str(record.get("value", "")).strip()
        if not (name and value):
            continue

        records.append(
            {
                "type": record_type,
                "name": _short_certificate_record_name(
                    name,
                    str(record.get("domain_name", "")).strip(),
                ),
                "value": value,
            }
        )

    if not records:
        fallback_domain = st.session_state.cert_requested_names[0] if st.session_state.cert_requested_names else ""
        for record in get_certificate_validation_records():
            records.append(
                {
                    "type": record["type"],
                    "name": _short_certificate_record_name(record["name"], fallback_domain),
                    "value": record["value"],
                }
            )

    cert_status = st.session_state.cert_status.strip()

    lines = [heading]

    lines.append("")
    if records:
        lines.append(format_dns_blocks(records))
    elif cert_status == "ISSUED":
        lines.append(
            "No additional DNS validation records are needed. AWS ACM currently shows this certificate as ISSUED."
        )
    else:
        lines.append(
            """CNAME
Name: [enter certificate cname name]
Value: [enter certificate cname target]"""
        )

    return "\n".join(lines)


def build_email() -> str:
    dealer_name = st.session_state.dealer_name.strip() or "there"
    domain = st.session_state.dealer_domain.strip().lower() or "[domain]"
    go_live = st.session_state.go_live_timing.strip()
    subdomain = st.session_state.subdomain_label.strip().lower() or "portal"
    lb = st.session_state.load_balancer

    sendgrid_cname_name = st.session_state.sendgrid_cname_name.strip()
    sendgrid_cname_target = st.session_state.sendgrid_cname_target.strip()
    dkim1_name = st.session_state.dkim1_name.strip()
    dkim1_target = st.session_state.dkim1_target.strip()
    dkim2_name = st.session_state.dkim2_name.strip()
    dkim2_target = st.session_state.dkim2_target.strip()
    dmarc_value = st.session_state.dmarc_value.strip()
    subdomain_target = get_subdomain_target(domain, lb)
    main_records = get_main_domain_records(domain, lb)
    cert_subject_text = get_certificate_subject_text()

    sendgrid_section = f"""1) DNS records to authenticate sending emails from @{domain}
CNAME
Name: {sendgrid_cname_name or "[enter cname name]"}
Value: {sendgrid_cname_target or "[enter cname target]"}

CNAME
Name: {dkim1_name or "s1._domainkey"}
Value: {dkim1_target or "[enter dkim 1 target]"}

CNAME
Name: {dkim2_name or "s2._domainkey"}
Value: {dkim2_target or "[enter dkim 2 target]"}

TXT
Name: _dmarc
Value: {dmarc_value or "[enter dmarc value]"}"""

    cert_section = build_certificate_email_section(
        f"2) DNS records to validate our server certificates in prep for site hosting on {cert_subject_text}"
    )

    if workflow == "SendGrid + certificate prep":
        return f"""Hi {dealer_name},

Here are the DNS records needed for {domain}:

{sendgrid_section}

{cert_section}

Once these records have been added, please let us know and we will complete the remaining setup on our side.

Thanks,"""

    if workflow == "SendGrid only":
        return f"""Hi {dealer_name},

Here are the DNS records needed to authenticate sending emails from @{domain}:

CNAME
Name: {sendgrid_cname_name or "[enter cname name]"}
Value: {sendgrid_cname_target or "[enter cname target]"}

CNAME
Name: {dkim1_name or "s1._domainkey"}
Value: {dkim1_target or "[enter dkim 1 target]"}

CNAME
Name: {dkim2_name or "s2._domainkey"}
Value: {dkim2_target or "[enter dkim 2 target]"}

TXT
Name: _dmarc
Value: {dmarc_value or "[enter dmarc value]"}

Once these records have been added, please let us know and we will verify the sending setup on our side.

Thanks,"""

    if workflow == "Certificate only":
        cert_only_section = build_certificate_email_section(
            f"Here are the DNS records needed to validate our server certificates in prep for site hosting on {cert_subject_text}:"
        )
        return f"""Hi {dealer_name},

{cert_only_section}

These records can be added right away and do not change where the website is hosted yet.

Once these records have been added, please let us know and we will complete the remaining setup on our side.

Thanks,"""

    if workflow == "Subdomain go-live":
        return f"""Hi {dealer_name},

Here is the DNS record needed to update the site to be hosted right on the subdomain {subdomain}.{domain}:

CNAME
Name: {subdomain}
Value: {subdomain_target}

Once this record has been added, please let us know and we will complete the remaining setup on our side.

Thanks,"""

    if workflow == "Main domain go-live":
        timing_line = f" We are targeting {go_live}." if go_live else ""
        return f"""Hi {dealer_name},

Here are the DNS records needed to update the site to be hosted right on {domain} - taking over the main site.{timing_line}

A
Name: @
Value: {main_records["a1"]}

A
Name: @
Value: {main_records["a2"]}

CNAME
Name: www
Value: {main_records["www"]}

Please only update these when we are aligned on go-live timing.

Once these records have been added, please let us know and we will complete the remaining setup on our side.

Thanks,"""

    if workflow == "WordPress update":
        return f"""Hi {dealer_name},

Here are the DNS records needed for the WordPress site on {domain}:

A
Name: @
Value: 64.225.22.51

A
Name: www
Value: 64.225.22.51

After the DNS update, additional WordPress and SSL settings may still need to be updated.

Once these records have been added, please let us know and we will complete the remaining setup on our side.

Thanks,"""

    return "Please choose a workflow."


def build_checklist() -> str:
    items = [
        "- verify customer confirmed the records were added",
        "- verify DNS propagation",
    ]

    if workflow in {"SendGrid + certificate prep", "SendGrid only"}:
        items.extend([
            "- verify SendGrid authentication shows as verified",
            "- verify sender address/domain is correct",
            "- test outbound email sending",
        ])

    if workflow in {"SendGrid + certificate prep", "Certificate only"}:
        items.append("- verify certificate is issued in AWS manually")

    if workflow == "Subdomain go-live":
        items.extend([
            "- update Toolbx Hostname to the new subdomain",
            "- move old toolbxapp hostname to Deprecated Hostnames",
            "- verify subdomain routing",
            "- verify HTTPS",
        ])

    if workflow == "Main domain go-live":
        items.extend([
            "- update Toolbx Hostname to dealer domain",
            "- move old toolbxapp hostname to Deprecated Hostnames",
            "- verify root domain routing",
            "- verify www routing",
            "- verify HTTPS",
        ])

    if workflow == "WordPress update":
        items.extend([
            "- verify Domain Management",
            "- verify SSL",
            "- verify WP Address URL",
        ])

    return "\n".join(items)


def _format_record_table(rows: list[tuple[str, str, str]]) -> str:
    type_width = max(len("Type"), *(len(record_type) for record_type, _, _ in rows))
    name_width = max(len("Name"), *(len(name) for _, name, _ in rows))

    lines = [f"{'Type':<{type_width}}  {'Name':<{name_width}}  Value"]
    for record_type, name, value in rows:
        lines.append(f"{record_type:<{type_width}}  {name:<{name_width}}  {value}")

    return "\n".join(lines)


def build_records_preview() -> str:
    sections = []

    if workflow in {"SendGrid + certificate prep", "SendGrid only"}:
        sendgrid_rows = [
            ("CNAME", st.session_state.sendgrid_cname_name, st.session_state.sendgrid_cname_target),
            ("CNAME", st.session_state.dkim1_name, st.session_state.dkim1_target),
            ("CNAME", st.session_state.dkim2_name, st.session_state.dkim2_target),
            ("TXT", "_dmarc", st.session_state.dmarc_value),
        ]
        sections.append("SendGrid records")
        sections.append(_format_record_table(sendgrid_rows))

    if workflow in {"SendGrid + certificate prep", "Certificate only"}:
        cert_rows = []
        for record in st.session_state.cert_validation_records:
            if not isinstance(record, dict):
                continue

            record_type = str(record.get("type", "")).strip().upper() or "CNAME"
            name = str(record.get("name", "")).strip()
            value = str(record.get("value", "")).strip()
            if not (name and value):
                continue

            cert_rows.append(
                (
                    record_type,
                    _short_certificate_record_name(
                        name,
                        str(record.get("domain_name", "")).strip(),
                    ),
                    value,
                )
            )

        if not cert_rows:
            fallback_domain = st.session_state.cert_requested_names[0] if st.session_state.cert_requested_names else ""
            for record in get_certificate_validation_records():
                cert_rows.append(
                    (
                        record["type"],
                        _short_certificate_record_name(record["name"], fallback_domain),
                        record["value"],
                    )
                )

        sections.append("AWS certificate records")
        if cert_rows:
            sections.append(_format_record_table(cert_rows))
        elif st.session_state.cert_status.strip() == "ISSUED":
            sections.append("No additional DNS validation records are needed.")

    if workflow == "Subdomain go-live":
        subdomain_rows = [
            (
                "CNAME",
                st.session_state.subdomain_label,
                get_subdomain_target(
                    st.session_state.dealer_domain.strip().lower(),
                    st.session_state.load_balancer,
                ),
            ),
        ]
        sections.append("Subdomain go-live records")
        sections.append(_format_record_table(subdomain_rows))

    if workflow == "Main domain go-live":
        records = get_main_domain_records(
            st.session_state.dealer_domain.strip().lower(),
            st.session_state.load_balancer,
        )
        main_domain_rows = [
            ("A", "@", records["a1"]),
            ("A", "@", records["a2"]),
            ("CNAME", "www", records["www"]),
        ]
        sections.append("Main domain go-live records")
        sections.append(_format_record_table(main_domain_rows))

    if workflow == "WordPress update":
        wordpress_rows = [
            ("A", "@", "64.225.22.51"),
            ("A", "www", "64.225.22.51"),
        ]
        sections.append("WordPress update records")
        sections.append(_format_record_table(wordpress_rows))

    return "\n\n".join(sections).strip()


with st.form("dns_form"):
    st.subheader("Basic details", anchor=False)
    col1, col2 = st.columns(2)

    with col1:
        dealer_name = st.text_input("Dealer name *", value=st.session_state.dealer_name)
        dealer_domain = st.text_input("Dealer domain *", value=st.session_state.dealer_domain)

    with col2:
        if workflow == "Main domain go-live":
            go_live_timing = st.text_input("Go-live timing", value=st.session_state.go_live_timing)
        else:
            go_live_timing = st.session_state.go_live_timing

    if workflow in {"Subdomain go-live", "Main domain go-live"}:
        st.subheader("Hosting details", anchor=False)
        col3, col4 = st.columns(2)

        with col3:
            if workflow == "Subdomain go-live":
                subdomain_label = st.text_input("Subdomain label *", value=st.session_state.subdomain_label)
            else:
                load_balancer = st.selectbox(
                    "Load balancer",
                    ["new", "old"],
                    key="load_balancer",
                    accept_new_options=False,
                )

        with col4:
            if workflow == "Subdomain go-live":
                load_balancer = st.selectbox(
                    "Load balancer",
                    ["new", "old"],
                    key="load_balancer",
                    accept_new_options=False,
                )
            else:
                subdomain_label = st.session_state.subdomain_label
    else:
        load_balancer = st.session_state.load_balancer
        subdomain_label = st.session_state.subdomain_label

    if workflow in {"SendGrid + certificate prep", "Certificate only"}:
        st.subheader("AWS certificate details", anchor=False)
        cert1, cert2 = st.columns(2)

        with cert1:
            secure_hostname = st.text_input(
                "Hostname to secure *",
                value=st.session_state.secure_hostname,
                help="Example: shop.bolyardlumber.com",
            )

        with cert2:
            certificate_scope = st.selectbox(
                "Certificate scope",
                CERTIFICATE_SCOPES,
                key="certificate_scope",
                accept_new_options=False,
            )
    else:
        secure_hostname = st.session_state.secure_hostname
        certificate_scope = st.session_state.certificate_scope

    submitted = st.form_submit_button("Save request")

if submitted:
    try:
        st.session_state.dealer_name = dealer_name
        st.session_state.dealer_domain = dealer_domain
        st.session_state.go_live_timing = go_live_timing
        st.session_state.subdomain_label = subdomain_label
        st.session_state.secure_hostname = secure_hostname

        errors = validate_required_fields()

        if errors:
            for error in errors:
                st.error(error)
        else:
            st.session_state.last_action = "Request saved"
            st.success("Request saved successfully.")
    except Exception as e:
        st.error(f"Could not save request: {e}")

st.subheader("Actions", anchor=False)
c1, c2, c3 = st.columns(3)

with c1:
    if st.button("Generate records"):
        try:
            errors = validate_required_fields()

            if errors:
                for error in errors:
                    st.error(error)
            else:
                domain = st.session_state.dealer_domain.strip().lower()
                secure_hostname = st.session_state.secure_hostname.strip().lower()
                certificate_scope = st.session_state.certificate_scope

                with st.spinner("Generating or retrieving DNS records from SendGrid and AWS..."):
                    if workflow in {"SendGrid + certificate prep", "SendGrid only"}:
                        sg = get_sendgrid_records(domain)
                        st.session_state.sendgrid_cname_name = sg["sendgrid_cname_name"]
                        st.session_state.sendgrid_cname_target = sg["sendgrid_cname_target"]
                        st.session_state.dkim1_name = sg["dkim1_name"]
                        st.session_state.dkim1_target = sg["dkim1_target"]
                        st.session_state.dkim2_name = sg["dkim2_name"]
                        st.session_state.dkim2_target = sg["dkim2_target"]
                        st.session_state.dmarc_value = sg["dmarc_value"]

                    if workflow in {"SendGrid + certificate prep", "Certificate only"}:
                        cert = get_aws_certificate_record(
                            dealer_domain=domain,
                            secure_hostname=secure_hostname,
                            certificate_scope=certificate_scope,
                        )
                        st.session_state.cert_cname_name = cert["cert_cname_name"]
                        st.session_state.cert_cname_target = cert["cert_cname_target"]
                        st.session_state.cert_requested_names = cert["requested_names"]
                        st.session_state.cert_validation_records = cert.get("cert_validation_records", [])
                        st.session_state.cert_status = cert.get("certificate_status", "") or ""

                st.session_state.last_action = "Records generated"
                st.success("DNS records generated successfully.")
        except Exception as e:
            st.error(f"Could not generate DNS records: {e}")

with c2:
    if st.button("Generate email template"):
        try:
            errors = validate_required_fields()

            if errors:
                for error in errors:
                    st.error(error)
            else:
                st.session_state.email_draft = build_email()
                st.session_state.last_action = "Email template generated"
                st.success("Email template generated successfully.")
        except Exception as e:
            st.error(f"Could not generate email template: {e}")

with c3:
    if st.button("Generate checklist"):
        try:
            errors = validate_required_fields()

            if errors:
                for error in errors:
                    st.error(error)
            else:
                st.session_state.checklist = build_checklist()
                st.session_state.last_action = "Checklist generated"
                st.success("Checklist generated successfully.")
        except Exception as e:
            st.error(f"Could not generate checklist: {e}")

st.divider()

left, right = st.columns(2)

with left:
    st.subheader("Generated records", anchor=False)
    st.code(build_records_preview(), language=None, wrap_lines=False, height=420)

with right:
    st.subheader("Generated email template", anchor=False)
    st.code(st.session_state.email_draft, language=None, wrap_lines=True, height=420)

st.subheader("Checklist", anchor=False)
st.code(st.session_state.checklist, language=None, wrap_lines=True, height=260)

st.subheader("Last action", anchor=False)
st.info(st.session_state.last_action)
