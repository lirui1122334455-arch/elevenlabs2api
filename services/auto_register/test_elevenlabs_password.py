import unittest

from elevenlabs_assisted.passwords import generate_password, is_valid_password


class PasswordTest(unittest.TestCase):
    def test_generated_password_meets_observed_rules(self) -> None:
        for _ in range(50):
            password = generate_password()
            self.assertTrue(is_valid_password(password))
            self.assertEqual(len(password), 20)

    def test_rejects_missing_character_classes(self) -> None:
        self.assertFalse(is_valid_password("abcdefgh!"))
        self.assertFalse(is_valid_password("12345678!"))
        self.assertFalse(is_valid_password("Abcdefgh9"))


if __name__ == "__main__":
    unittest.main()
