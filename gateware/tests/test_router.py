# Copyright (c) 2026 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

import unittest

from amaranth import *
from amaranth.lib import wiring
from amaranth.sim import *
from amaranth_soc import csr
from amaranth_soc.csr import wishbone

from amaranth_future import fixed
from tiliqua import test as test_util
from tiliqua.dsp import ASQ

from top.usb_audio_soc.top import Router, RouterPeripheral

N_CHANNELS    = 4
STARVE_FRAMES = 8

PHYS = [0.125, 0.25, -0.25, 0.5]
USB  = [-0.5, 0.375, -0.125, 0.75]


class RouterTests(unittest.TestCase):

    def _dut(self):
        return Router(n_channels=N_CHANNELS, starve_frames=STARVE_FRAMES)

    def test_routing_and_starvation(self):

        dut = self._dut()

        async def testbench(ctx):
            # Physical inputs always streaming (I2S clocks are FPGA-driven),
            # sink always ready.
            for k in range(N_CHANNELS):
                ctx.set(dut.i_phys.payload[k], fixed.Const(PHYS[k], shape=ASQ))
                ctx.set(dut.i_usb.payload[k], fixed.Const(USB[k], shape=ASQ))
            ctx.set(dut.i_phys.valid, 1)
            ctx.set(dut.i_usb.valid, 1)
            ctx.set(dut.o.ready, 1)

            # dest0 <- phys in0, dest1 <- usb ch1, dest2 <- off,
            # dest5 (usb capture ch1) <- phys in2, dest6 <- usb ch3 (loopback)
            ctx.set(dut.sel[0], 1 + 0)
            ctx.set(dut.sel[1], 1 + N_CHANNELS + 1)
            ctx.set(dut.sel[2], 0)
            ctx.set(dut.sel[5], 1 + 2)
            ctx.set(dut.sel[6], 1 + N_CHANNELS + 3)

            # Let the hold registers latch a USB frame.
            await ctx.tick().repeat(4)

            self.assertEqual(ctx.get(dut.o.valid), 1)
            self.assertAlmostEqual(ctx.get(dut.o.payload[0]).as_float(), PHYS[0])
            self.assertAlmostEqual(ctx.get(dut.o.payload[1]).as_float(), USB[1])
            self.assertAlmostEqual(ctx.get(dut.o.payload[2]).as_float(), 0.0)
            self.assertAlmostEqual(ctx.get(dut.o.payload[5]).as_float(), PHYS[2])
            self.assertAlmostEqual(ctx.get(dut.o.payload[6]).as_float(), USB[3])

            # Host stops streaming: physical routing keeps flowing, USB
            # sources mute after STARVE_FRAMES routed frames.
            ctx.set(dut.i_usb.valid, 0)
            await ctx.tick().repeat(2)
            # ...still holding the last USB samples before the timeout...
            self.assertAlmostEqual(ctx.get(dut.o.payload[1]).as_float(), USB[1])
            await ctx.tick().repeat(STARVE_FRAMES + 2)
            self.assertEqual(ctx.get(dut.o.valid), 1)
            self.assertAlmostEqual(ctx.get(dut.o.payload[0]).as_float(), PHYS[0])
            self.assertAlmostEqual(ctx.get(dut.o.payload[1]).as_float(), 0.0)
            self.assertAlmostEqual(ctx.get(dut.o.payload[6]).as_float(), 0.0)

            # Host resumes: USB sources come back.
            ctx.set(dut.i_usb.valid, 1)
            await ctx.tick().repeat(2)
            self.assertAlmostEqual(ctx.get(dut.o.payload[1]).as_float(), USB[1])
            self.assertAlmostEqual(ctx.get(dut.o.payload[6]).as_float(), USB[3])

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        sim.run()

    def test_usb_drain_paced_by_frames(self):

        # The USB stream must be consumed at most once per routed frame,
        # otherwise the USB DAC FIFO level (and with it the isochronous
        # feedback endpoint's rate estimate) is destroyed.

        dut = self._dut()

        async def testbench(ctx):
            for k in range(N_CHANNELS):
                ctx.set(dut.i_phys.payload[k], fixed.Const(PHYS[k], shape=ASQ))
                ctx.set(dut.i_usb.payload[k], fixed.Const(USB[k], shape=ASQ))
            ctx.set(dut.i_usb.valid, 1)

            # Physical frames tick once every 8 cycles (like fs strobes);
            # USB data is always available.
            frames = 0
            usb_consumed = 0
            ctx.set(dut.o.ready, 1)
            for _ in range(20):
                ctx.set(dut.i_phys.valid, 1)
                await ctx.tick()
                frames       += ctx.get(dut.o.valid & dut.o.ready)
                usb_consumed += ctx.get(dut.i_usb.valid & dut.i_usb.ready)
                ctx.set(dut.i_phys.valid, 0)
                for _ in range(7):
                    await ctx.tick()
                    frames       += ctx.get(dut.o.valid & dut.o.ready)
                    usb_consumed += ctx.get(dut.i_usb.valid & dut.i_usb.ready)

            self.assertEqual(frames, 20)
            self.assertEqual(usb_consumed, 20)

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        sim.run()


class RouterPeripheralTests(unittest.TestCase):

    def test_indexed_route_writes(self):

        defaults = [1 + N_CHANNELS + d for d in range(N_CHANNELS)] + \
                   [1 + c for c in range(N_CHANNELS)]

        m = Module()
        dut = RouterPeripheral(n_dests=2*N_CHANNELS, default_sources=defaults)
        decoder = csr.Decoder(addr_width=28, data_width=8)
        decoder.add(dut.bus, addr=0, name="dut")
        bridge = wishbone.WishboneCSRBridge(decoder.bus, data_width=32)
        m.submodules += [dut, decoder, bridge]

        async def testbench(ctx):

            async def csr_write(ctx, value, register, field=None):
                await test_util.csr.wb_csr_w(
                        ctx, dut.bus, bridge.wb_bus, value, register, field)

            # defaults visible before any write
            for d, default in enumerate(defaults):
                self.assertEqual(ctx.get(dut.sel[d]), default)
            self.assertEqual(ctx.get(dut.usb_connect), 0)

            # indexed write protocol: set index, then source latches it
            await csr_write(ctx, 2, "route_index")
            await csr_write(ctx, 7, "route_source")
            await ctx.tick()
            for d, default in enumerate(defaults):
                self.assertEqual(ctx.get(dut.sel[d]), 7 if d == 2 else default)

            # another destination, first one keeps its value
            await csr_write(ctx, 5, "route_index")
            await csr_write(ctx, 0, "route_source")
            await ctx.tick()
            self.assertEqual(ctx.get(dut.sel[2]), 7)
            self.assertEqual(ctx.get(dut.sel[5]), 0)

            await csr_write(ctx, 1, "flags")
            await ctx.tick()
            self.assertEqual(ctx.get(dut.usb_connect), 1)

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_testbench(testbench)
        sim.run()
