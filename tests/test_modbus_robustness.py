        battery_reads = [c for c in self.client.read_holding_registers.await_args_list
                         if c.kwargs["address"] == 30156]
        self.assertEqual(len(battery_reads), 2)

    async def test_bad_phase_power_recovers_after_second_read(self):
        good = list(self.blocks[30183])
        bad = list(good)
        int32_into(bad, 3, -16_711_833)
        calls = 0
        async def read(*, address, count, **kwargs):
            nonlocal calls
            if address == 30183:
                calls += 1
                return modbus_response(bad if calls == 1 else good)
            return modbus_response(list(self.blocks[address]))
        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertEqual(data["grid_power_r"], 0)
        self.assertEqual(calls, 2)

    async def test_period_3_invalid_value_gets_second_read(self):
        first = list(self.blocks[40901])
        first[19] = 1099
        second = list(self.blocks[40901])
        seen = 0
        async def read(*, address, count, **kwargs):
            nonlocal seen
            if address == 40901:
                seen += 1
                return modbus_response(first if seen == 1 else second)
            return modbus_response(list(self.blocks[address]))
        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertEqual(data["period_3"], 0)
        self.assertEqual(seen, 2)

    async def test_sanity_retry_does_not_advance_stateful_counter_twice(self):
        self.blocks[30156][8] = 63744
        await self.coordinator._async_update_data()
        # Even if a register is read twice, the stateful validator runs once per poll.
        with patch.object(self.coordinator._validator, "validate", wraps=self.coordinator._validator.validate) as validate:
            await self.coordinator._async_update_data()
            validate.assert_called_once()

    async def test_isolated_2048_amp_grid_spike_recovers_after_second_read(self):
        self.blocks[30183][1] = 8  # Normal L2/S current = 0.8 A
        await self.coordinator._async_update_data()  # Establish valid baseline.
        corrupt = list(self.blocks[30183])
        corrupt[1] = 2048  # 204.8 A -- syntactically valid but suspicious.
        clean = list(self.blocks[30183])
        attempts = 0

        async def read(*, address, count, **kwargs):
            nonlocal attempts
            if address == 30183:
                attempts += 1
                return modbus_response(corrupt if attempts == 1 else clean)
            return modbus_response(list(self.blocks[address]))

        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertAlmostEqual(data["grid_current_s"], 0.8)
        self.assertEqual(attempts, 2)

    async def test_confirmed_real_high_grid_current_is_allowed(self):
        self.blocks[30183][1] = 8
        await self.coordinator._async_update_data()
        self.blocks[30183][1] = 2000  # 200 A: valid on a sufficiently sized CT.
        self.client.read_holding_registers.reset_mock()
        data = await self.coordinator._async_update_data()
        self.assertEqual(data["grid_current_s"], 200.0)
        phase_reads = [call for call in self.client.read_holding_registers.await_args_list
                       if call.kwargs["address"] == 30183]
        self.assertEqual(len(phase_reads), 2)

    async def test_unverified_grid_current_spike_does_not_leak_to_ha(self):
        self.blocks[30183][1] = 8
        await self.coordinator._async_update_data()
        corrupt = list(self.blocks[30183])
        corrupt[1] = 2048
        attempts = 0

        async def read(*, address, count, **kwargs):
            nonlocal attempts
            if address == 30183:
                attempts += 1
                if attempts == 1:
                    return modbus_response(corrupt)
                return None  # Both transport attempts of verification fail.
            return modbus_response(list(self.blocks[address]))

        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertNotIn("grid_current_s", data)
        self.assertIn("battery_soc", data)
        self.assertEqual(self.coordinator._last_valid_grid_currents["grid_current_s"], 0.8)

    async def test_two_inconsistent_current_spikes_are_not_published(self):
        self.blocks[30183][1] = 8
        await self.coordinator._async_update_data()
        first = list(self.blocks[30183])
        second = list(self.blocks[30183])
        first[1] = 2048
        second[1] = 5000
        attempts = 0

        async def read(*, address, count, **kwargs):
            nonlocal attempts
            if address == 30183:
                attempts += 1
                return modbus_response(first if attempts == 1 else second)
            return modbus_response(list(self.blocks[address]))

        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertNotIn("grid_current_s", data)
        self.assertEqual(attempts, 2)

    async def test_reversed_bms_cell_voltage_recovers_on_second_read(self):
        # Real-world failure: minimum 3.27 V, maximum 0.20 V.
        bad = list(self.blocks[5123])
        bad[13:15] = [200, 3270]
        good = list(self.blocks[5123])
        attempts = 0

        async def read(*, address, count, **kwargs):
            nonlocal attempts
            if address == 5123:
                attempts += 1
                return modbus_response(bad if attempts == 1 else good)
            return modbus_response(list(self.blocks[address]))

        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertAlmostEqual(data["bms_cell_voltage_min"], 3.3)
        self.assertAlmostEqual(data["bms_cell_voltage_max"], 3.35)
        self.assertEqual(attempts, 2)

    async def test_reversed_bms_cell_voltage_rejected_after_two_bad_reads(self):
        self.blocks[5123][13:15] = [200, 3270]
        data = await self.coordinator._async_update_data()
        self.assertNotIn("bms_cell_voltage_min", data)
        self.assertNotIn("bms_cell_voltage_max", data)
        self.assertEqual(data["bms_soc"], 50)
        bms_reads = [call for call in self.client.read_holding_registers.await_args_list
                     if call.kwargs["address"] == 5123]
        self.assertEqual(len(bms_reads), 2)

    async def test_valid_bms_cell_voltage_does_not_trigger_retry(self):
        data = await self.coordinator._async_update_data()
        self.assertAlmostEqual(data["bms_cell_voltage_min"], 3.3)
        self.assertAlmostEqual(data["bms_cell_voltage_max"], 3.35)
        bms_reads = [call for call in self.client.read_holding_registers.await_args_list
                     if call.kwargs["address"] == 5123]
        self.assertEqual(len(bms_reads), 1)

    async def test_reversed_bms_cell_voltage_rejected_if_retry_fails(self):
        bad = list(self.blocks[5123])
        bad[13:15] = [200, 3270]
        attempts = 0

        async def read(*, address, count, **kwargs):
            nonlocal attempts
            if address == 5123:
                attempts += 1
                return modbus_response(bad) if attempts == 1 else None
            return modbus_response(list(self.blocks[address]))

        self.client.read_holding_registers.side_effect = read
        data = await self.coordinator._async_update_data()
        self.assertNotIn("bms_cell_voltage_min", data)
        self.assertNotIn("bms_cell_voltage_max", data)
        self.assertEqual(data["bms_soc"], 50)
        self.assertEqual(attempts, 3)  # Initial read + two transport attempts.

    async def test_signed_16bit_write_verified_from_unsigned_register(self):
        self.client.read_holding_registers.side_effect = None
        self.client.read_holding_registers.return_value = modbus_response([65535])
        ok = await self.coordinator.async_write_register(40901, -1, 2)
        self.assertTrue(ok)
        self.assertEqual(self.client.write_register.await_args.kwargs["value"], 65535)
        self.assertEqual(self.client.write_register.await_count, 1)
        self.assertEqual(self.client.read_holding_registers.await_count, 1)
        self.assertEqual(self.coordinator.refresh_calls, 1)

    async def test_32bit_write_verifies_both_words(self):
        value = -12345
        expected = list(struct.unpack(">HH", struct.pack(">i", value)))
        self.client.read_holding_registers.side_effect = None
        self.client.read_holding_registers.return_value = modbus_response(expected)
        ok = await self.coordinator.async_write_register_32bit(40401, value, 2)
        self.assertTrue(ok)
