"""Reply routing precedence and cooldown are independent of QQ events."""
import unittest
from unittest.mock import Mock, patch
from virtualme.chat import policy
from virtualme.models import ReplyMode

class ReplyPolicyTests(unittest.TestCase):
    def decide(self, text, **overrides):
        values = dict(to_me=False, has_image=False, now=100, last_reply=0, random_value=0.1)
        values.update(overrides)
        return policy.decide_reply(text, **values)

    def test_mentions_and_images_take_precedence_without_random_draw(self):
        random = Mock(side_effect=AssertionError('Unexpected random draw'))
        decision, cooldown = self.decide('帕朵语音', to_me=True, has_image=True, random_value=random)
        self.assertEqual(decision.mode, ReplyMode.TEXT_AND_OPTIONAL_VOICE)
        self.assertFalse(cooldown)
        decision, _ = self.decide('帕朵语音', has_image=True, random_value=random)
        self.assertEqual(decision.mode, ReplyMode.IMAGE)

    def test_keywords_bypass_random_cooldown_and_voice_precedes_text(self):
        with patch.object(policy, 'VOICE_KEYWORDS', ['语音']), patch.object(policy, 'TXT_KEYWORDS', ['文字']):
            decision, cooldown = self.decide('帕朵文字语音', now=100, last_reply=100)
        self.assertEqual(decision.mode, ReplyMode.VOICE)
        self.assertFalse(cooldown)

    def test_untriggered_and_cooling_messages_do_not_draw_random(self):
        random = Mock(side_effect=AssertionError('Unexpected random draw'))
        self.assertEqual(self.decide('普通消息', random_value=random), (None, False))
        with patch.object(policy, 'TXT_KEYWORDS', []):
            self.assertEqual(self.decide('帕朵', now=100, last_reply=100, random_value=random), (None, False))

    def test_random_thresholds_and_no_reply_still_update_cooldown(self):
        with patch.object(policy, 'TXT_KEYWORDS', []), patch.object(policy, 'VOICE_PROBABILITY', .2), patch.object(policy, 'TEXT_PROBABILITY', .3):
            self.assertEqual(self.decide('帕朵', random_value=.1)[0].mode, ReplyMode.VOICE)
            self.assertEqual(self.decide('帕朵', random_value=.3)[0].mode, ReplyMode.TEXT)
            self.assertEqual(self.decide('帕朵', random_value=.7), (None, True))
