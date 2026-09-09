"""Unit tests for Iris DataSanitizer."""

from iris.core.sanitizer import DataSanitizer


def test_blacklist_names():
    sample_locals = {
        "user_id": 42,
        "username": "alice",
        "password": "super_secret_password_123",
        "api_key_header": "sk-live-abcdef123456",
        "auth_token": "bearer xyz789",
        "secret_salt": "pepper",
    }
    sanitized = DataSanitizer.sanitize_locals(sample_locals)

    assert sanitized["user_id"] == 42
    assert sanitized["username"] == "alice"
    assert "[REDACTED_SECRET" in sanitized["password"]
    assert "[REDACTED_SECRET" in sanitized["api_key_header"]
    assert "[REDACTED_SECRET" in sanitized["auth_token"]
    assert "[REDACTED_SECRET" in sanitized["secret_salt"]


def test_regex_secrets():
    jwt_val = "ey" + "A" * 15 + "." + "B" * 15 + "." + "C" * 15
    aws_val = "AKIAIOSFODNN7EXAMPLE"
    priv_key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA..."

    assert "[REDACTED_JWT" in DataSanitizer.sanitize_string(jwt_val)
    assert "[REDACTED_AWS_KEY" in DataSanitizer.sanitize_string(aws_val)
    assert "[REDACTED_PRIVATE_KEY" in DataSanitizer.sanitize_string(priv_key)


def test_size_bounds():
    long_str = "x" * 500
    sanitized_str = DataSanitizer.sanitize_string(long_str)
    assert len(sanitized_str) < 300
    assert "... [truncated 308 chars] ..." in sanitized_str

    long_list = list(range(100))
    sanitized_list = DataSanitizer.sanitize_value(long_list)
    assert len(sanitized_list) == 21
    assert "... [truncated 80 items]" in sanitized_list[-1]

    large_dict = {f"k{i}": i for i in range(50)}
    sanitized_dict = DataSanitizer.sanitize_value(large_dict)
    assert len(sanitized_dict) == 21
    assert "__iris_truncated__" in sanitized_dict


if __name__ == "__main__":
    test_blacklist_names()
    test_regex_secrets()
    test_size_bounds()
    print("All DataSanitizer unit tests passed successfully!")
