import os
import unittest
from unittest.mock import patch

import integrations


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages
        self.paginate_calls = []

    def paginate(self, **kwargs):
        self.paginate_calls.append(kwargs)
        return list(self.pages)


class FakeACMClient:
    def __init__(self, *, pages, certificates, request_arn=None):
        self.pages = pages
        self.certificates = certificates
        self.request_arn = request_arn
        self.request_calls = []
        self.paginator = FakePaginator(pages)

    def get_paginator(self, name):
        if name != "list_certificates":
            raise AssertionError(f"Unexpected paginator requested: {name}")
        return self.paginator

    def describe_certificate(self, CertificateArn):
        return {"Certificate": self.certificates[CertificateArn]}

    def request_certificate(self, **kwargs):
        self.request_calls.append(kwargs)
        if not self.request_arn:
            raise AssertionError("request_certificate should not have been called.")
        return {"CertificateArn": self.request_arn}


class AwsCertificateRecordTests(unittest.TestCase):
    def test_mock_mode_returns_multiple_validation_records_for_root_and_wildcard(self):
        with patch.dict(os.environ, {"DNS_OPS_USE_MOCK_MODE": "true"}, clear=False):
            result = integrations.get_aws_certificate_record(
                dealer_domain="Example.com",
                secure_hostname="",
                certificate_scope="Root + wildcard",
            )

        self.assertEqual(result["requested_names"], ["example.com", "*.example.com"])
        self.assertEqual(result["certificate_status"], "PENDING_VALIDATION")
        self.assertEqual(len(result["cert_validation_records"]), 2)
        self.assertEqual(result["cert_cname_name"], result["cert_validation_records"][0]["name"])

    def test_live_mode_reuses_existing_certificate(self):
        certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/existing"
        certificate = {
            "CertificateArn": certificate_arn,
            "DomainName": "example.com",
            "SubjectAlternativeNames": ["example.com", "*.example.com"],
            "Status": "PENDING_VALIDATION",
            "DomainValidationOptions": [
                {
                    "DomainName": "example.com",
                    "ValidationStatus": "PENDING_VALIDATION",
                    "ResourceRecord": {
                        "Name": "_root.example.com.",
                        "Type": "CNAME",
                        "Value": "_root.acm-validations.aws.",
                    },
                },
                {
                    "DomainName": "*.example.com",
                    "ValidationStatus": "PENDING_VALIDATION",
                    "ResourceRecord": {
                        "Name": "_wild.example.com.",
                        "Type": "CNAME",
                        "Value": "_wild.acm-validations.aws.",
                    },
                },
            ],
        }
        fake_client = FakeACMClient(
            pages=[
                {
                    "CertificateSummaryList": [
                        {"DomainName": "example.com", "CertificateArn": certificate_arn},
                    ]
                }
            ],
            certificates={certificate_arn: certificate},
        )

        with patch.dict(
            os.environ,
            {"DNS_OPS_USE_MOCK_MODE": "false", "AWS_REGION": "us-east-1"},
            clear=False,
        ):
            with patch("integrations._acm_client", return_value=fake_client):
                result = integrations.get_aws_certificate_record(
                    dealer_domain="example.com",
                    secure_hostname="",
                    certificate_scope="Root + wildcard",
                )

        self.assertEqual(fake_client.request_calls, [])
        self.assertEqual(result["certificate_arn"], certificate_arn)
        self.assertEqual(result["certificate_status"], "PENDING_VALIDATION")
        self.assertEqual(
            result["requested_names"],
            ["example.com", "*.example.com"],
        )
        self.assertEqual(
            result["cert_validation_records"],
            [
                {
                    "domain_name": "example.com",
                    "validation_status": "PENDING_VALIDATION",
                    "name": "_root.example.com",
                    "type": "CNAME",
                    "value": "_root.acm-validations.aws.",
                },
                {
                    "domain_name": "*.example.com",
                    "validation_status": "PENDING_VALIDATION",
                    "name": "_wild.example.com",
                    "type": "CNAME",
                    "value": "_wild.acm-validations.aws.",
                },
            ],
        )

    def test_live_mode_requests_new_certificate_when_missing(self):
        certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/new"
        certificate = {
            "CertificateArn": certificate_arn,
            "DomainName": "shop.example.com",
            "SubjectAlternativeNames": ["shop.example.com"],
            "Status": "PENDING_VALIDATION",
            "DomainValidationOptions": [
                {
                    "DomainName": "shop.example.com",
                    "ValidationStatus": "PENDING_VALIDATION",
                    "ResourceRecord": {
                        "Name": "_shop.shop.example.com.",
                        "Type": "CNAME",
                        "Value": "_shop.acm-validations.aws.",
                    },
                }
            ],
        }
        fake_client = FakeACMClient(
            pages=[{"CertificateSummaryList": []}],
            certificates={certificate_arn: certificate},
            request_arn=certificate_arn,
        )

        with patch.dict(
            os.environ,
            {"DNS_OPS_USE_MOCK_MODE": "false", "AWS_REGION": "us-east-1"},
            clear=False,
        ):
            with patch("integrations._acm_client", return_value=fake_client):
                result = integrations.get_aws_certificate_record(
                    dealer_domain="example.com",
                    secure_hostname="shop.example.com",
                    certificate_scope="Exact hostname",
                )

        self.assertEqual(result["certificate_arn"], certificate_arn)
        self.assertEqual(len(fake_client.request_calls), 1)
        self.assertEqual(fake_client.request_calls[0]["DomainName"], "shop.example.com")
        self.assertEqual(fake_client.request_calls[0]["ValidationMethod"], "DNS")
        self.assertNotIn("SubjectAlternativeNames", fake_client.request_calls[0])


if __name__ == "__main__":
    unittest.main()
