# MITM support - generates root CA and domain-specific certs
import os
import ssl
from datetime import datetime, timedelta, timezone
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

CERTS_DIR = os.path.join(os.path.dirname(__file__), 'certs')
CA_KEY_PATH = os.path.join(CERTS_DIR, 'ca.key')
CA_CERT_PATH = os.path.join(CERTS_DIR, 'ca.crt')

if not os.path.exists(CERTS_DIR):
    os.makedirs(CERTS_DIR)

def generate_ca():
    if os.path.exists(CA_KEY_PATH) and os.path.exists(CA_CERT_PATH):
        return

    # Create the root key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    with open(CA_KEY_PATH, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))

    # Create the root cert
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, u"LB"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"SecureWatch Proxy"),
        x509.NameAttribute(NameOID.COMMON_NAME, u"SecureWatch Proxy Root CA"),
    ])
    
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.now(timezone.utc)
    ).not_valid_after(
        datetime.now(timezone.utc) + timedelta(days=3650)
    ).add_extension(
        x509.BasicConstraints(ca=True, path_length=None), critical=True
    ).sign(private_key, hashes.SHA256())

    with open(CA_CERT_PATH, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

def generate_domain_cert(domain):
    domain_key_path = os.path.join(CERTS_DIR, f"{domain}.key")
    domain_cert_path = os.path.join(CERTS_DIR, f"{domain}.crt")

    if os.path.exists(domain_key_path) and os.path.exists(domain_cert_path):
        return domain_cert_path, domain_key_path

    # Load CA
    with open(CA_KEY_PATH, "rb") as f:
        ca_key = serialization.load_pem_private_key(f.read(), password=None)
    with open(CA_CERT_PATH, "rb") as f:
        ca_cert = x509.load_pem_x509_certificate(f.read())

    # Create key for this domain
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    with open(domain_key_path, "wb") as f:
        f.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ))

    # Create cert for this domain
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, u"LB"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"SecureWatch Proxy MITM"),
        x509.NameAttribute(NameOID.COMMON_NAME, str(domain)),
    ])

    certBuilder = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        ca_cert.subject
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.now(timezone.utc)
    ).not_valid_after(
        datetime.now(timezone.utc) + timedelta(days=365)
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName(str(domain))]), critical=False
    ).sign(ca_key, hashes.SHA256())

    with open(domain_cert_path, "wb") as f:
        f.write(certBuilder.public_bytes(serialization.Encoding.PEM))

    return domain_cert_path, domain_key_path

def get_server_context(domain):
    generate_ca()
    cert_path, key_path = generate_domain_cert(domain)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return context

def get_client_context():
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context
