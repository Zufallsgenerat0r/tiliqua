import math
import sys
import unittest
from math import pi, sin

from amaranth import *
from amaranth.sim import *
from parameterized import parameterized

from amaranth_future import fixed
from tiliqua.dsp import ASQ
from tiliqua.raster.scope import FrequencyAxis, Spectrogram
from tiliqua.test import stream


def expected_axis(k, log, sz):
    """Reference model for the FrequencyAxis position ROM."""
    n_vis = sz // 2
    k = min(k, n_vis - 1)
    if log:
        x = math.log2(max(k, 1))/math.log2(n_vis) - 0.5
    else:
        x = k/n_vis - 0.5
    return fixed.Const(x, shape=ASQ, clamp=True).as_float()


class FrequencyAxisTests(unittest.TestCase):

    @parameterized.expand([
        ["linear", 0],
        ["log",    1],
    ])
    def test_axis(self, name, log_scale):

        """
        Feed 2 blocks of known magnitudes through FrequencyAxis and verify
        the emitted axis positions match the reference tables, the pen-lift
        covers the mirrored half (and DC in log mode), and the magnitude
        channel is passed through (centered).
        """

        sz = 16
        n_vis = sz // 2
        n_blocks = 2

        m = Module()
        m.submodules.dut = dut = FrequencyAxis(sz=sz)
        m.d.comb += dut.log_scale.eq(log_scale)

        def mag(k):
            return 0.5 * k / sz

        async def stimulus_i(ctx):
            for _ in range(n_blocks):
                for k in range(sz):
                    await stream.put(ctx, dut.i, {
                        'first': 1 if k == 0 else 0,
                        'sample': fixed.Const(mag(k), shape=ASQ),
                    })

        async def testbench(ctx):
            ctx.set(dut.o.ready, 1)
            outputs = []
            while len(outputs) < n_blocks * sz:
                if ctx.get(dut.o.valid):
                    outputs.append((
                        ctx.get(dut.o.payload[0]).as_float(),
                        ctx.get(dut.o.payload[1]).as_float(),
                        ctx.get(dut.o.payload[2]).as_float(),
                    ))
                await ctx.tick()

            for n, (o_mag, o_axis, o_pen) in enumerate(outputs):
                k = n % sz
                self.assertAlmostEqual(
                    o_mag, mag(k) - 0.25, delta=1/(1 << ASQ.f_bits))
                self.assertAlmostEqual(
                    o_axis, expected_axis(k, log_scale, sz), delta=1e-6)
                pen_expected = (k >= n_vis) or (log_scale and k == 0)
                if pen_expected:
                    self.assertAlmostEqual(o_pen, ASQ.max().as_float())
                else:
                    self.assertEqual(o_pen, 0.0)

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_process(stimulus_i)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open(f"test_frequency_axis_{name}.vcd", "w")):
            sim.run()


class SpectrogramTests(unittest.TestCase):

    @parameterized.expand([
        ["linear", 0],
        ["log",    1],
    ])
    def test_spectrogram_sine(self, name, log_scale):

        """
        Drive a sine centered on a known FFT bin through the whole
        Spectrogram chain (resample -> STFT -> envelope -> log -> axis)
        and verify the peak magnitude is emitted at that bin's expected
        axis position.
        """

        fs = 48000
        sz = 64
        n_vis = sz // 2
        m_down = 2  # Spectrogram-internal decimation at fs <= 48kHz
        target_bin = 8

        m = Module()
        m.submodules.dut = dut = Spectrogram(fs=fs, sz=sz)
        m.d.comb += [
            dut.freq_scale_log.eq(log_scale),
            # No envelope smoothing so the first blocks already carry
            # the full magnitude.
            dut.smooth.eq(0),
        ]

        async def stimulus_i(ctx):
            for n in range(0, sys.maxsize):
                x = 0.9 * sin(2*pi*target_bin*n/(sz*m_down))
                await stream.put(ctx, dut.i, [
                    fixed.Const(x, shape=ASQ), 0, 0, 0])

        async def testbench(ctx):
            ctx.set(dut.o.ready, 1)
            outputs = []
            # Skip the first (warm-up) block, then check 2 full blocks.
            n_skip = sz
            n_check = 2 * sz
            while len(outputs) < n_skip + n_check:
                if ctx.get(dut.o.valid):
                    outputs.append((
                        ctx.get(dut.o.payload[0]).as_float(),
                        ctx.get(dut.o.payload[1]).as_float(),
                        ctx.get(dut.o.payload[2]).as_float(),
                    ))
                await ctx.tick()

            for b in range(1, 1 + n_check // sz):
                block = outputs[b*sz:(b+1)*sz]
                # Peak of the visible (positive-frequency) half, ignoring DC.
                vis = block[1:n_vis]
                peak = max(range(len(vis)), key=lambda i: vis[i][0]) + 1
                self.assertEqual(peak, target_bin)
                self.assertAlmostEqual(
                    vis[peak-1][1], expected_axis(peak, log_scale, sz),
                    delta=1e-6)
                # Pen lifted across the whole mirrored half.
                for (_, _, o_pen) in block[n_vis:]:
                    self.assertAlmostEqual(o_pen, ASQ.max().as_float())

        sim = Simulator(m)
        sim.add_clock(1e-6)
        sim.add_process(stimulus_i)
        sim.add_testbench(testbench)
        with sim.write_vcd(vcd_file=open(f"test_spectrogram_{name}.vcd", "w")):
            sim.run()


if __name__ == "__main__":
    unittest.main()
