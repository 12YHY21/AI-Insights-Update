import base64
import hashlib
import hmac
import unittest

from src.feishu import FeishuSender


class FeishuTests(unittest.TestCase):
    def test_signature_matches_documented_algorithm(self):
        sender = FeishuSender("https://example.com", "secret")
        timestamp = "1599360473"
        expected = base64.b64encode(
            hmac.new(f"{timestamp}\nsecret".encode(), digestmod=hashlib.sha256).digest()
        ).decode()
        self.assertEqual(sender._signature(timestamp), expected)

    def test_rejects_non_https_webhook(self):
        with self.assertRaises(ValueError):
            FeishuSender("http://example.com")


if __name__ == "__main__":
    unittest.main()

