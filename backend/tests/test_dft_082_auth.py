import unittest
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import auth

class TestAuthLogic(unittest.TestCase):
    def test_password_hashing(self):
        pw = "my-secret-password"
        hashed = auth.hash_password(pw)
        self.assertNotEqual(pw, hashed)
        self.assertTrue(auth.verify_password(pw, hashed))
        self.assertFalse(auth.verify_password("wrong-password", hashed))

    def test_token_creation_and_verification(self):
        uid = 123
        username = "testuser"
        token = auth.create_token(uid, username)
        
        payload = auth.verify_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["uid"], uid)
        self.assertEqual(payload["sub"], username)
        self.assertGreater(payload["exp"], time.time())

    def test_invalid_token(self):
        self.assertIsNone(auth.verify_token("invalid.token"))
        self.assertIsNone(auth.verify_token("a.b.c"))
        
        # Tamper with token
        token = auth.create_token(1, "user")
        parts = token.split('.')
        tampered = parts[0] + "." + "tamperedsignature"
        self.assertIsNone(auth.verify_token(tampered))

if __name__ == "__main__":
    unittest.main()
