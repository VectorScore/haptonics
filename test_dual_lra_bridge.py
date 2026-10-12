"""Small protocol tests for the two-motor bridge."""

import unittest

from dual_lra_bridge import command_pair


class DualLraBridgeTests(unittest.TestCase):
    def test_command_pair_routes_0832_to_h_and_1030z_to_l(self):
        self.assertEqual(command_pair(127, 33), b"H127\nL33\n")

    def test_command_pair_stops_both_motors(self):
        self.assertEqual(command_pair(0, 0), b"H0\nL0\n")


if __name__ == "__main__":
    unittest.main()
