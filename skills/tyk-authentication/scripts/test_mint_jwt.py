import base64
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import mint_jwt as m


def b64d(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class Claims(unittest.TestCase):
    def test_json_values_and_ttl(self):
        c = m.build_claims(["sub=partner-a", 'aud=["a","b"]', "n=5"], ttl=-10, now=1000)
        self.assertEqual(c, {"sub": "partner-a", "aud": ["a", "b"], "n": 5, "iat": 1000, "exp": 990})

    def test_bad_claim(self):
        with self.assertRaises(ValueError):
            m.parse_claims(["nope"])


class HS256(unittest.TestCase):
    def test_signature_verifies(self):
        token = m.mint_hs256({"sub": "x"}, "secret", kid="k1")
        h, p, sig = token.split(".")
        self.assertEqual(json.loads(b64d(h)), {"alg": "HS256", "typ": "JWT", "kid": "k1"})
        expected = hmac.new(b"secret", f"{h}.{p}".encode(), hashlib.sha256).digest()
        self.assertEqual(b64d(sig), expected)


SAMPLE = """Public-Key: (16 bit)
Modulus:
    00:c3:a1
Exponent: 65537 (0x10001)
"""


class JWKS(unittest.TestCase):
    def test_from_openssl_text(self):
        jwks = m.jwk_from_openssl_text(SAMPLE, "k1")
        key = jwks["keys"][0]
        self.assertEqual(key["kid"], "k1")
        self.assertEqual(b64d(key["n"]), bytes([0xC3, 0xA1]))
        self.assertEqual(b64d(key["e"]), (65537).to_bytes(3, "big"))


@unittest.skipUnless(shutil.which("openssl"), "openssl command not available")
class RS256(unittest.TestCase):
    def test_sign_and_verify(self):
        with tempfile.TemporaryDirectory() as d:
            priv, pub = os.path.join(d, "k.pem"), os.path.join(d, "k.pub")
            subprocess.run(["openssl", "genrsa", "-out", priv, "2048"], check=True, capture_output=True)
            subprocess.run(["openssl", "rsa", "-in", priv, "-pubout", "-out", pub], check=True, capture_output=True)
            token = m.mint_rs256({"sub": "x"}, priv, kid="k1")
            h, p, sig = token.split(".")
            msg, sigf = os.path.join(d, "msg"), os.path.join(d, "sig")
            with open(msg, "wb") as fh:
                fh.write(f"{h}.{p}".encode())
            with open(sigf, "wb") as fh:
                fh.write(b64d(sig))
            res = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pub, "-signature", sigf, msg],
                                 capture_output=True, text=True)
            self.assertIn("Verified OK", res.stdout)
            jwks = m.jwks_for(pub, "k1")
            self.assertEqual(jwks["keys"][0]["kty"], "RSA")


if __name__ == "__main__":
    unittest.main()
