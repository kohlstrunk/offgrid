"""Regression: Dienst object must be created inside asyncio.run's running loop."""
import asyncio
import sys
import unittest
from unittest.mock import patch
import dienst as module


class DienstLoop094(unittest.TestCase):
    def test_dienst_konstruktion_im_aktiven_loop(self):
        gesehen = []

        class ProbeDienst:
            def __init__(self, *args, **kwargs):
                gesehen.append(asyncio.get_running_loop())

            async def start(self):
                self.assert_loop = asyncio.get_running_loop()
                self_ref = gesehen[0]
                assert self_ref is self.assert_loop

        with patch.object(module, 'Dienst', ProbeDienst), patch.object(sys, 'argv', ['dienst.py']):
            module.main()
        self.assertEqual(len(gesehen), 1)


if __name__ == '__main__':
    unittest.main()
