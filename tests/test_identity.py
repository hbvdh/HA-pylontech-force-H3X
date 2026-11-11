"""Serial number check: learn, verify, block a foreign device, accept a replacement."""
import unittest

import test_optional_reads as base
from test_coordinator import coordinator_module

# The identity module as the coordinator under test imported it.
identity = coordinator_module.identity

OWN = "SN12345678"
OTHER = "SN99999999"


class IdentityGuardTests(unittest.TestCase):
    def test_serial_is_learned_only_after_two_identical_readings(self):
        guard = identity.IdentityGuard()
        self.assertEqual(guard.observe(OWN), identity.PENDING)
        self.assertIsNone(guard.expected)
        self.assertFalse(guard.writes_blocked)
        self.assertEqual(guard.observe(OWN), identity.LEARNED)
        self.assertEqual(guard.expected, OWN)

    def test_corrupt_first_reading_is_not_learned(self):
        guard = identity.IdentityGuard()
        guard.observe("SN1234X678")
        self.assertEqual(guard.observe(OWN), identity.PENDING)
        self.assertEqual(guard.observe(OWN), identity.LEARNED)
        self.assertEqual(guard.expected, OWN)

    def test_unusable_serials_change_nothing(self):
        guard = identity.IdentityGuard(OWN)
        for value in ("", None, "abc", "SN 123 456", "x" * 40, 12345678):
            self.assertEqual(guard.observe(value), identity.IGNORED)
        self.assertFalse(guard.writes_blocked)
        self.assertIsNone(guard.mismatch)

    def test_one_deviating_reading_blocks_writes_but_is_no_mismatch(self):
        guard = identity.IdentityGuard(OWN)
        self.assertEqual(guard.observe(OTHER), identity.PENDING)
        self.assertTrue(guard.writes_blocked)
        self.assertIsNone(guard.mismatch)
        self.assertEqual(guard.observe(OWN), identity.OK)
        self.assertFalse(guard.writes_blocked)

    def test_two_deviating_readings_confirm_a_mismatch(self):
        guard = identity.IdentityGuard(OWN)
        guard.observe(OTHER)
        self.assertEqual(guard.observe(OTHER), identity.MISMATCH_NEW)
        self.assertEqual(guard.mismatch, OTHER)
        self.assertEqual(guard.observe(OTHER), identity.MISMATCH)
        self.assertTrue(guard.writes_blocked)

    def test_two_different_deviating_readings_do_not_confirm(self):
        guard = identity.IdentityGuard(OWN)
        guard.observe(OTHER)
        self.assertEqual(guard.observe("SN88888888"), identity.PENDING)
        self.assertIsNone(guard.mismatch)

    def test_own_device_back_clears_the_mismatch(self):
        guard = identity.IdentityGuard(OWN)
        guard.observe(OTHER)
        guard.observe(OTHER)
        self.assertEqual(guard.observe(OWN), identity.CLEARED)
        self.assertIsNone(guard.mismatch)
        self.assertFalse(guard.writes_blocked)

    def test_accept_makes_the_other_device_the_expected_one(self):
        guard = identity.IdentityGuard(OWN)
        guard.observe(OTHER)
        guard.observe(OTHER)
        guard.accept(OTHER)
        self.assertEqual(guard.expected, OTHER)
        self.assertFalse(guard.writes_blocked)
        self.assertEqual(guard.observe(OTHER), identity.OK)


class IdentityCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    """The guard wired into real polls, with the optional-read test fixture."""

    battery_details = False
    setUp = base.OptionalReadTests.setUp
    response = base.OptionalReadTests.response
    poll = base.OptionalReadTests.poll

    def set_serial(self, serial):
        self.blocks[30010][16:24] = base.ascii_regs(serial, 8)

    async def run_for(self, seconds):
        for _ in range(seconds // 5):
            await self.poll()

    async def confirm_mismatch(self):
        """Own device first, then another one at the same address."""
        await self.poll()
        self.set_serial(OTHER)
        self.coordinator._optional_next_read.pop(30010, None)
        await self.poll()                       # first deviating reading
        await self.run_for(coordinator_module.IDENTITY_CONFIRM_S)
        self.assertEqual(self.coordinator.identity_guard.mismatch, OTHER)

    async def test_new_installation_learns_the_serial_within_a_minute(self):
        self.coordinator.identity_guard.expected = None
        await self.poll()
        self.assertIsNone(self.coordinator.identity_guard.expected)
        await self.run_for(coordinator_module.IDENTITY_CONFIRM_S)
        self.assertEqual(self.coordinator.identity_guard.expected, OWN)
        self.assertEqual(self.calls.count(30010), 2)
        # After that: back to the normal hourly rhythm.
        await self.run_for(600)
        self.assertEqual(self.calls.count(30010), 2)

    async def test_known_serial_costs_no_extra_reads(self):
        await self.run_for(600)
        self.assertEqual(self.calls.count(30010), 1)
        self.assertFalse(self.coordinator.identity_guard.writes_blocked)

    async def test_single_corrupt_serial_reading_recovers_by_itself(self):
        await self.poll()
        self.set_serial(OTHER)
        self.coordinator._optional_next_read.pop(30010, None)
        data = await self.poll()
        self.assertTrue(data)
        self.assertTrue(self.coordinator.identity_guard.writes_blocked)
        self.set_serial(OWN)                    # the dongle answers sanely again
        await self.run_for(coordinator_module.IDENTITY_CONFIRM_S)
        self.assertFalse(self.coordinator.identity_guard.writes_blocked)
        self.assertIsNone(self.coordinator.identity_guard.mismatch)
        self.assertTrue(await self.poll())

    async def test_confirmed_other_device_stops_polling_and_writing(self):
        await self.confirm_mismatch()
        before = len(self.calls)
        with self.assertRaises(coordinator_module.IdentityMismatch):
            await self.poll()
        self.assertEqual(len(self.calls), before)   # nothing was read
        self.coordinator.client.write_register = base.AsyncMock()
        self.coordinator.client.write_registers = base.AsyncMock()
        self.assertFalse(await self.coordinator.async_write_register(40907, 4))
        self.assertFalse(
            await self.coordinator.async_write_register_32bit(40401, -3000)
        )
        self.coordinator.client.write_register.assert_not_called()
        self.coordinator.client.write_registers.assert_not_called()
        # The other device's details never reached the device info.
        self.assertEqual(self.coordinator.device_identity.get("serial"), OWN)

    async def test_mismatch_does_not_trigger_the_dongle_backoff(self):
        await self.confirm_mismatch()
        for _ in range(10):
            with self.assertRaises(coordinator_module.IdentityMismatch):
                await self.poll()
        self.assertEqual(self.coordinator._bad_polls, 0)

    async def test_only_the_serial_is_reread_while_mismatched(self):
        await self.confirm_mismatch()
        before = len(self.calls)
        for _ in range(coordinator_module.IDENTITY_MISMATCH_RECHECK_S // 5 + 1):
            with self.assertRaises(coordinator_module.IdentityMismatch):
                await self.poll()
        self.assertEqual(self.calls[before:], [30010])

    async def test_own_device_back_resumes_by_itself(self):
        await self.confirm_mismatch()
        self.set_serial(OWN)
        resumed = None
        for _ in range(coordinator_module.IDENTITY_MISMATCH_RECHECK_S // 5 + 2):
            try:
                resumed = await self.poll()
            except coordinator_module.IdentityMismatch:
                continue
            break
        self.assertTrue(resumed)
        self.assertFalse(self.coordinator.identity_guard.writes_blocked)

    async def test_accepting_the_replacement_resumes_with_the_new_device(self):
        await self.confirm_mismatch()
        self.coordinator.accept_replacement(OTHER)
        self.assertEqual(self.coordinator.identity_guard.expected, OTHER)
        data = await self.poll()
        self.assertTrue(data)
        self.assertEqual(self.coordinator.device_identity.get("serial"), OTHER)
        self.assertFalse(self.coordinator.identity_guard.writes_blocked)

    async def test_serial_is_checked_again_after_the_dongle_was_away(self):
        await self.poll()
        self.assertEqual(self.calls.count(30010), 1)
        self.coordinator._bad_polls = 2            # as after two dead polls
        await self.poll()                          # healthy poll: recovery
        await self.poll()
        self.assertEqual(self.calls.count(30010), 2)


if __name__ == "__main__":
    unittest.main()
