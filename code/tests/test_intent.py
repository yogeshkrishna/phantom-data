"""Consent tests include paraphrases and languages, not participant case labels."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from phantom.intent import classify_intent


class IntentTests(unittest.TestCase):
    def test_explicit_english_requests(self):
        for text in ("Delete my account.", "Could you erase my personal information?",
                     "I want this profile removed permanently.",
                     "Please close my account and process the associated personal-data deletion.",
                     "I am requesting deletion of my account.",
                     "I would like my account to be erased."):
            with self.subTest(text=text):
                self.assertEqual(classify_intent(text).authorization, "REQUESTED")

    def test_multilingual_requests(self):
        for text, language in (("Je souhaite la suppression de mon compte.", "fr"),
                               ("Por favor, eliminen mi cuenta.", "es"),
                               ("Solicito a exclusão da minha conta.", "pt"),
                               ("アカウントの削除をお願いします。", "ja"),
                               ("계정 삭제를 요청합니다.", "ko")):
            with self.subTest(language=language):
                result = classify_intent(text)
                self.assertEqual((result.authorization, result.language), ("REQUESTED", language))

    def test_benign_requests_not_deletion(self):
        for text in ("Please change the email address for my account.",
                     "I want a copy of my personal information.",
                     "Please remove me from marketing lists only.",
                     "My surname is misspelled on the account.",
                     "Could you tell me the deletion status of my account?",
                     "How can I delete my account?", "Please send the deletion policy for my account."):
            with self.subTest(text=text):
                self.assertEqual(classify_intent(text).authorization, "NOT_REQUESTED")

    def test_quoted_erasure_not_consent(self):
        for text in ('The article says "delete my account". I need login help.',
                     "My friend wrote 'erase my profile'; please help me log in."):
            self.assertEqual(classify_intent(text).authorization, "NOT_REQUESTED")

    def test_negation_and_withdrawal(self):
        for text in ("Don't delete my account.", "I am not asking you to erase my personal data.",
                     "I need help logging in. Do not close the account.",
                     "Please cancel my account deletion.",
                     "Je ne demande pas la suppression de mon compte.",
                     "No quiero eliminar mi cuenta.", "Não quero excluir minha conta.",
                     "アカウントを削除しないでください。", "계정을 삭제하지 말아주세요."):
            with self.subTest(text=text):
                self.assertEqual(classify_intent(text).authorization, "NOT_REQUESTED")

    def test_uncertainty_not_consent(self):
        for text in ("", "Account deletion.", "Please close my account.",
                     "Don't delete my account. Please delete my account.",
                     "Bitte entfernen Sie mein Konto."):
            with self.subTest(text=text):
                self.assertEqual(classify_intent(text).authorization, "AMBIGUOUS")

    def test_unrelated_negation_does_not_cancel_real_request(self):
        for text in ("Do not send promotional mail, please delete my account.",
                     "Do not email a confirmation but please delete my account."):
            self.assertEqual(classify_intent(text).authorization, "REQUESTED")

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            classify_intent(None)


if __name__ == "__main__":
    unittest.main()
