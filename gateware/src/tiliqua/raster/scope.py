# Copyright (c) 2024 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0
"""
Multi-channel oscilloscope and vectorscope SoC peripherals.
"""

import math

from amaranth import *
from amaranth.lib import data, memory, stream, wiring
from amaranth.lib.wiring import In, Out
from amaranth.utils import exact_log2
from amaranth_soc import csr
from amaranth_future import fixed

from .. import dsp
from ..dsp import ASQ
from . import PSQ, PSQ_BASE_FBITS, psq_from_volts
from .plot import PlotRequest
from .stroke import Stroke


class VectorPeripheral(wiring.Component):

    class Flags(csr.Register, access="w"):
        enable: csr.Field(csr.action.W, unsigned(1))

    class HueReg(csr.Register, access="w"):
        hue: csr.Field(csr.action.W, unsigned(8))

    class IntensityReg(csr.Register, access="w"):
        intensity: csr.Field(csr.action.W, unsigned(8))

    class ScaleReg(csr.Register, access="w"):
        scale: csr.Field(csr.action.W, unsigned(8))

    class Position(csr.Register, access="w"):
        value: csr.Field(csr.action.W, unsigned(16))

    class PixelsPerVolt(csr.Register, access="r"):
        pixels_per_volt: csr.Field(csr.action.R, unsigned(16))

    def __init__(self):

        self.stroke = Stroke()

        regs = csr.Builder(addr_width=6, data_width=8)

        self._flags     = regs.add("flags",     self.Flags(),        offset=0x0)
        self._hue       = regs.add("hue",       self.HueReg(),       offset=0x4)
        self._intensity = regs.add("intensity", self.IntensityReg(), offset=0x8)
        self._xoffset   = regs.add("xoffset",   self.Position(),     offset=0xC)
        self._yoffset   = regs.add("yoffset",   self.Position(),     offset=0x10)
        self._xscale    = regs.add("xscale",    self.ScaleReg(),     offset=0x14)
        self._yscale    = regs.add("yscale",    self.ScaleReg(),     offset=0x18)
        self._pscale    = regs.add("pscale",    self.ScaleReg(),     offset=0x1C)
        self._cscale    = regs.add("cscale",    self.ScaleReg(),     offset=0x20)
        self._pixels_per_volt = regs.add("pixels_per_volt", self.PixelsPerVolt(), offset=0x24)

        self._bridge = csr.Bridge(regs.as_memory_map())

        super().__init__({
            "i": In(stream.Signature(data.ArrayLayout(PSQ, 4))),
            # CSR bus
            "bus": In(csr.Signature(addr_width=regs.addr_width, data_width=regs.data_width)),
            # Plot request output to shared backend
            "o": Out(stream.Signature(PlotRequest)),
            "soc_en": Out(unsigned(1), init=1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        m.submodules += self.stroke

        wiring.connect(m, wiring.flipped(self.i), self.stroke.i)
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        m.d.comb += self._pixels_per_volt.f.pixels_per_volt.r_data.eq(
            psq_from_volts(1).reshape(PSQ_BASE_FBITS))

        with m.If(self._hue.f.hue.w_stb):
            m.d.sync += self.stroke.hue.eq(self._hue.f.hue.w_data)

        with m.If(self._intensity.f.intensity.w_stb):
            m.d.sync += self.stroke.intensity.eq(self._intensity.f.intensity.w_data)

        with m.If(self._xscale.f.scale.w_stb):
            m.d.sync += self.stroke.scale_x.eq(self._xscale.f.scale.w_data)

        with m.If(self._yscale.f.scale.w_stb):
            m.d.sync += self.stroke.scale_y.eq(self._yscale.f.scale.w_data)

        with m.If(self._xoffset.f.value.w_stb):
            m.d.sync += self.stroke.x_offset.eq(self._xoffset.f.value.w_data)

        with m.If(self._yoffset.f.value.w_stb):
            m.d.sync += self.stroke.y_offset.eq(self._yoffset.f.value.w_data)

        with m.If(self._pscale.f.scale.w_stb):
            m.d.sync += self.stroke.scale_p.eq(self._pscale.f.scale.w_data)

        with m.If(self._cscale.f.scale.w_stb):
            m.d.sync += self.stroke.scale_c.eq(self._cscale.f.scale.w_data)

        with m.If(self._flags.f.enable.w_stb):
            m.d.sync += self.soc_en.eq(self._flags.f.enable.w_data)

        with m.If(self.soc_en):
            wiring.connect(m, self.stroke.o, wiring.flipped(self.o))

        return m

class ScopePeripheral(wiring.Component):

    class Flags(csr.Register, access="w"):
        enable: csr.Field(csr.action.W, unsigned(1))
        trigger_always: csr.Field(csr.action.W, unsigned(1))

    class Hue(csr.Register, access="w"):
        hue: csr.Field(csr.action.W, unsigned(8))

    class Intensity(csr.Register, access="w"):
        intensity: csr.Field(csr.action.W, unsigned(8))

    class Timebase(csr.Register, access="w"):
        timebase: csr.Field(csr.action.W, unsigned(32))

    class XScale(csr.Register, access="w"):
        xscale: csr.Field(csr.action.W, unsigned(8))

    class YScale(csr.Register, access="w"):
        yscale: csr.Field(csr.action.W, unsigned(8))

    class TriggerLevel(csr.Register, access="w"):
        trigger_level: csr.Field(csr.action.W, unsigned(16))

    class XPosition(csr.Register, access="w"):
        xpos: csr.Field(csr.action.W, unsigned(16))

    class YPosition(csr.Register, access="w"):
        ypos: csr.Field(csr.action.W, unsigned(16))

    class PixelsPerVolt(csr.Register, access="r"):
        pixels_per_volt: csr.Field(csr.action.R, unsigned(16))

    class Fs(csr.Register, access="r"):
        fs: csr.Field(csr.action.R, unsigned(32))

    class PeriodSamples(csr.Register, access="r"):
        period_samples: csr.Field(csr.action.R, unsigned(32))

    class TriggerCount(csr.Register, access="r"):
        trigger_count: csr.Field(csr.action.R, unsigned(32))

    def __init__(self, n_channels=4, fs=48000):

        self.fs = fs
        self.n_channels = n_channels
        self.strokes = [Stroke()
                        for _ in range(self.n_channels)]

        regs = csr.Builder(addr_width=6, data_width=8)
        self._flags          = regs.add("flags",          self.Flags(),         offset=0x0)
        self._hue            = regs.add("hue",            self.Hue(),           offset=0x4)
        self._intensity      = regs.add("intensity",      self.Intensity(),     offset=0x8)
        self._timebase       = regs.add("timebase",       self.Timebase(),      offset=0xC)
        self._xscale         = regs.add("xscale",         self.XScale(),        offset=0x10)
        self._yscale         = regs.add("yscale",         self.YScale(),        offset=0x14)
        self._trigger_lvl    = regs.add("trigger_lvl",    self.TriggerLevel(),  offset=0x18)
        self._xpos           = regs.add("xpos",           self.XPosition(),     offset=0x1C)
        self._ypos           = [regs.add(f"ypos{i}",      self.YPosition(),
                                offset=(0x20+i*4)) for i in range(self.n_channels)]
        self._pixels_per_volt = regs.add("pixels_per_volt", self.PixelsPerVolt(), offset=0x30)
        self._fs              = regs.add("fs",              self.Fs(),             offset=0x34)
        self._period_samples  = regs.add("period_samples",  self.PeriodSamples(),  offset=0x38)
        self._trigger_count   = regs.add("trigger_count",   self.TriggerCount(),   offset=0x3C)

        self._bridge = csr.Bridge(regs.as_memory_map())
        super().__init__({
            "i": In(stream.Signature(data.ArrayLayout(PSQ, self.n_channels))),
            # CSR bus
            "bus": In(csr.Signature(addr_width=regs.addr_width, data_width=regs.data_width)),
            # Pixel request outputs, one for each channel
            "o": Out(stream.Signature(PlotRequest)).array(self.n_channels),
            "soc_en": Out(unsigned(1), init=1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()

        trigger_lvl = Signal(shape=PSQ)
        trigger_always = Signal()

        self.isplit4 = dsp.Split(self.n_channels, shape=PSQ)

        wiring.connect(m, wiring.flipped(self.i), self.isplit4.i)

        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        m.d.comb += self._pixels_per_volt.f.pixels_per_volt.r_data.eq(
            psq_from_volts(1).reshape(PSQ_BASE_FBITS))
        m.d.comb += self._fs.f.fs.r_data.eq(self.fs)

        m.submodules += self.strokes

        for n, s in enumerate(self.strokes):
            wiring.connect(m, s.o, wiring.flipped(self.o[n]))

        # Scope and trigger
        # Ch0 is routed through trigger, the rest are not.
        m.submodules.isplit4 = self.isplit4

        # 2 copies of input channel 0
        m.submodules.irep2 = irep2 = dsp.Split(2, replicate=True, source=self.isplit4.o[0], shape=PSQ)

        # Send one copy to trigger => ramp => X
        # ~50mV hysteresis: without it, noise near the threshold crossing
        # double-fires the trigger, corrupting the period measurement.
        m.submodules.trig = trig = dsp.Trigger(shape=PSQ, hysteresis=0.05/8.192)
        m.submodules.ramp = ramp = dsp.Ramp(shape=PSQ)
        timebase = Signal(shape=dsp.Ramp.TIMEBASE_SQ)
        # Audio => Trigger
        dsp.connect_remap(m, irep2.o[0], trig.i, lambda o, i: [
            i.payload.sample.eq(o.payload),
            i.payload.threshold.eq(trigger_lvl),
        ])
        # Trigger => Ramp
        dsp.connect_remap(m, trig.o, ramp.i, lambda o, i: [
            i.payload.trigger.eq(o.payload | trigger_always),
            i.payload.td.eq(timebase),
        ])

        # Period measurement for auto-timebase. Tap the raw trigger output:
        # we want the true input period regardless of the Ramp's debounce
        # (which ignores retriggers mid-sweep until output > 0.985).
        sample_ctr     = Signal(32)
        period_samples = Signal(32)
        trigger_count  = Signal(32)
        with m.If(trig.o.valid & trig.o.ready):
            with m.If(trig.o.payload):
                m.d.sync += [
                    period_samples.eq(sample_ctr),
                    sample_ctr.eq(1),
                    trigger_count.eq(trigger_count + 1),
                ]
            with m.Else():
                m.d.sync += sample_ctr.eq(sample_ctr + 1)
        m.d.comb += [
            self._period_samples.f.period_samples.r_data.eq(period_samples),
            self._trigger_count.f.trigger_count.r_data.eq(trigger_count),
        ]

        # Split ramp into 4 streams, one for each channel
        m.submodules.rampsplit4 = rampsplit4 = dsp.Split(self.n_channels, replicate=True, source=ramp.o, shape=PSQ)

        # Rasterize ch0: Ramp => X, Audio => Y
        m.submodules.ch0_merge4 = ch0_merge4 = dsp.Merge(4, shape=PSQ)
        # HACK for stable trigger despite periodic cache misses
        # TODO: modify ramp generation instead?
        dsp.connect_peek(m, ch0_merge4.o, self.strokes[0].i, always_ready=True)
        ch0_merge4.wire_valid(m, [2, 3])
        wiring.connect(m, rampsplit4.o[0], ch0_merge4.i[0])
        wiring.connect(m, irep2.o[1], ch0_merge4.i[1])

        # Rasterize ch1-ch3: Ramp => X, Audio => Y
        for ch in range(1, self.n_channels):
            ch_merge4 = dsp.Merge(4, shape=PSQ)
            dsp.connect_peek(m, ch_merge4.o, self.strokes[ch].i, always_ready=True)
            m.submodules += ch_merge4
            ch_merge4.wire_valid(m, [2, 3])
            wiring.connect(m, rampsplit4.o[ch], ch_merge4.i[0])
            wiring.connect(m, self.isplit4.o[ch], ch_merge4.i[1])


        # Wishbone tweakables

        with m.If(self._flags.f.trigger_always.w_stb):
            m.d.sync += trigger_always.eq(self._flags.f.trigger_always.w_data)

        with m.If(self._hue.f.hue.w_stb):
            for ch, s in enumerate(self.strokes):
                m.d.sync += s.hue.eq(self._hue.f.hue.w_data + ch*3)

        with m.If(self._intensity.f.intensity.w_stb):
            for s in self.strokes:
                m.d.sync += s.intensity.eq(self._intensity.f.intensity.w_data)

        with m.If(self._timebase.f.timebase.w_stb):
            m.d.sync += timebase.as_value().eq(self._timebase.f.timebase.w_data)

        with m.If(self._xscale.f.xscale.w_stb):
            for s in self.strokes:
                m.d.sync += s.scale_x.eq(self._xscale.f.xscale.w_data)

        with m.If(self._yscale.f.yscale.w_stb):
            for s in self.strokes:
                m.d.sync += s.scale_y.eq(self._yscale.f.yscale.w_data)

        with m.If(self._trigger_lvl.f.trigger_level.w_stb):
            m.d.sync += trigger_lvl.as_value().eq(
                self._trigger_lvl.f.trigger_level.w_data.as_signed() >> (PSQ_BASE_FBITS - PSQ.f_bits))

        with m.If(self._xpos.f.xpos.w_stb):
            for s in self.strokes:
                m.d.sync += s.x_offset.eq(self._xpos.f.xpos.w_data)

        for i, ypos_reg in enumerate(self._ypos):
            with m.If(ypos_reg.f.ypos.w_stb):
                m.d.sync += self.strokes[i].y_offset.eq(ypos_reg.f.ypos.w_data)

        with m.If(self._flags.f.enable.w_stb):
            m.d.sync += self.soc_en.eq(self._flags.f.enable.w_data)

        with m.If(~self.soc_en):
            m.d.comb += self.i.ready.eq(0)

        return m


class FrequencyAxis(wiring.Component):

    """
    Format blocks of (log-)magnitude spectra for plotting.

    For each incoming bin, emit the magnitude on channel 0 (offset to
    center), an axis position for that bin on channel 1, and a pen-lift
    on channel 2 (to avoid interpolation artifacts).

    Axis positions come from a ROM holding a linear and a logarithmic
    table, selected at runtime with ``log_scale``. Only the first
    ``sz//2`` bins (positive frequencies) are drawn; the mirrored half
    is emitted with the pen lifted, parked at the last visible position.
    On the log axis, bin 0 (DC) is parked at bin 1's position with the
    pen lifted.
    """

    def __init__(self, sz):
        self.sz = sz
        super().__init__({
            # Blocks of real (log-)magnitude bins
            "i": In(stream.Signature(dsp.block.Block(ASQ))),
            # Out on channels 0 (magnitude), 1 (axis), 2 (pen-lift)
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, 4))),
            "log_scale": In(unsigned(1)),
        })

    def elaborate(self, platform):
        m = Module()

        sz = self.sz
        n_vis = sz // 2
        n_oct = math.log2(n_vis)

        def axis_position(k, log):
            if k >= n_vis:
                # Mirrored half: parked at the last visible position.
                k = n_vis - 1
            if log:
                x = math.log2(max(k, 1))/n_oct - 0.5
            else:
                x = k/n_vis - 0.5
            return fixed.Const(x, shape=ASQ, clamp=True)

        m.submodules.rom = rom = memory.Memory(
            shape=ASQ, depth=2*sz,
            init=[axis_position(k, False) for k in range(sz)] +
                 [axis_position(k, True)  for k in range(sz)])
        rom_rd = rom.read_port()

        idx = Signal(range(sz))
        l_mag = Signal(ASQ)
        m.d.comb += [
            rom_rd.en.eq(1),
            rom_rd.addr.eq(Cat(idx, self.log_scale)),
        ]

        pen_lift = Signal()
        m.d.comb += pen_lift.eq(
            (idx >= n_vis) | (self.log_scale & (idx == 0)))

        m.d.comb += [
            # Magnitude on ch0 (offset to center)
            self.o.payload[0].eq(l_mag - fixed.Const(0.25)),
            # Axis position on ch1
            self.o.payload[1].eq(rom_rd.data),
        ]
        with m.If(pen_lift):
            m.d.comb += self.o.payload[2].eq(ASQ.max())

        with m.FSM():
            with m.State("IDLE"):
                m.d.comb += self.i.ready.eq(1)
                with m.If(self.i.valid):
                    m.d.sync += l_mag.eq(self.i.payload.sample)
                    with m.If(self.i.payload.first):
                        m.d.sync += idx.eq(0)
                    with m.Else():
                        m.d.sync += idx.eq(idx+1)
                    m.next = "READ"
            with m.State("READ"):
                # 1 cycle for the sync ROM read of the new `idx`.
                m.next = "OUTPUT"
            with m.State("OUTPUT"):
                m.d.comb += self.o.valid.eq(1)
                with m.If(self.o.ready):
                    m.next = "IDLE"

        return m


class Spectrogram(wiring.Component):

    """
    Simple spectrogram drawing logic.

    Take input channel 0, run an FFT/STFT on it, take the logarithm and
    emit the log-magnitude on 0, frequency axis position on 1, and a
    pen-lift on 2 (see :class:`FrequencyAxis`).

    Designed to connect to Stroke - that is, use a vectorscope as
    a spectrum analyzer visualization.
    """

    def __init__(self, fs, sz=512):
        self.fs = fs
        self.sz = sz
        super().__init__({
            # In on channel 0
            "i": In(stream.Signature(data.ArrayLayout(ASQ, 4))),
            # Out on channels 0 (magnitude), 1 (axis), 2 (pen-lift)
            "o": Out(stream.Signature(data.ArrayLayout(ASQ, 4))),
            # Select logarithmic frequency axis (0 = linear)
            "freq_scale_log": In(unsigned(1)),
            # Envelope smoothing level, indexes a table of one-pole
            # smoothing constants (0 = no smoothing).
            "smooth": In(unsigned(4), init=4),
        })

    def elaborate(self, platform):
        m = Module()

        m.submodules.split4 = split4 = dsp.Split(4)
        wiring.connect(m, wiring.flipped(self.i), split4.i)

        fftsz = self.sz

        # Resample input down, so visible area is a fraction of the nyquist (e.g. 192khz/8 = 24kHz visual bandwidth)
        # order_mult is bumped so the anti-alias transition band is narrow enough
        # that content above the decimated nyquist doesn't visibly fold back into
        # the displayed spectrum (the default's transition band is wider than the
        # whole visible range).
        m.submodules.resample = resample = dsp.Resample(
            fs_in=self.fs, n_up=1, m_down=8 if self.fs > 48000 else 2, order_mult=32)
        m.submodules.analyzer = analyzer = dsp.fft.STFTAnalyzer(shape=ASQ, sz=fftsz)
        m.submodules.envelope = envelope = dsp.spectral.SpectralEnvelope(shape=ASQ, sz=fftsz)
        def log_lut(x):
            # map 0 - 1 (linear) to 0 - 1 (log representing -X dBr to 0dBr)
            # where -X (smallest value) represents 1 LSB of the fixed.SQ.
            max_v = 1 << ASQ.f_bits
            r = max(0, math.log2(max(1, x*max_v))/math.log2(max_v))
            return r
        m.submodules.log = log = dsp.block.WrapCore(dsp.WaveShaper(
                lut_function=log_lut, lut_size=512, continuous=False))
        m.submodules.faxis = faxis = FrequencyAxis(sz=fftsz)

        # `smooth` level to one-pole smoothing constant; level->beta mapping
        # lives here so SoC register values are independent of the ASQ format.
        with m.Switch(self.smooth):
            for n in range(16):
                with m.Case(n):
                    m.d.comb += envelope.beta.eq(
                        fixed.Const(1.0 - 2.0**(-n/2.0), shape=ASQ, clamp=True))
        m.d.comb += faxis.log_scale.eq(self.freq_scale_log)

        wiring.connect(m, split4.o[0], resample.i)
        wiring.connect(m, resample.o, analyzer.i)
        wiring.connect(m, analyzer.o, envelope.i)
        wiring.connect(m, envelope.o, log.i)
        wiring.connect(m, log.o, faxis.i)
        wiring.connect(m, faxis.o, wiring.flipped(self.o))

        # Unused channels
        split4.wire_ready(m, [1, 2, 3])

        return m


class SpectrumPeripheral(wiring.Component):

    """
    :class:`Spectrogram` with CSR registers, for spectrum analysis in
    SoC designs.

    Emits axis position on channel 0 (x) and magnitude on channel 1 (y),
    so the frequency axis is horizontal when plotted by a :class:`Stroke`
    (whose tweakables - scale, offset, intensity, hue - are expected to
    be provided by the peripheral this stream is routed to, normally a
    :class:`VectorPeripheral`).
    """

    class Flags(csr.Register, access="w"):
        enable:   csr.Field(csr.action.W, unsigned(1))
        freq_log: csr.Field(csr.action.W, unsigned(1))

    class SmoothReg(csr.Register, access="w"):
        smooth: csr.Field(csr.action.W, unsigned(4))

    class NBins(csr.Register, access="r"):
        n_bins: csr.Field(csr.action.R, unsigned(16))

    class FsBins(csr.Register, access="r"):
        fs_bins: csr.Field(csr.action.R, unsigned(32))

    def __init__(self, fs, sz=512):
        self.fs = fs
        self.sz = sz
        self.spectrogram = Spectrogram(fs=fs, sz=sz)

        regs = csr.Builder(addr_width=5, data_width=8)

        self._flags   = regs.add("flags",   self.Flags(),     offset=0x0)
        self._smooth  = regs.add("smooth",  self.SmoothReg(), offset=0x4)
        self._n_bins  = regs.add("n_bins",  self.NBins(),     offset=0x8)
        self._fs_bins = regs.add("fs_bins", self.FsBins(),    offset=0xC)

        self._bridge = csr.Bridge(regs.as_memory_map())

        super().__init__({
            # In on channel 0
            "i": In(stream.Signature(data.ArrayLayout(PSQ, 4))),
            # CSR bus
            "bus": In(csr.Signature(addr_width=regs.addr_width, data_width=regs.data_width)),
            # Out on channels 0 (axis), 1 (magnitude), 2 (pen-lift)
            "o": Out(stream.Signature(data.ArrayLayout(PSQ, 4))),
            "soc_en": Out(unsigned(1), init=0),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        m.submodules.spectrogram = spectrogram = self.spectrogram

        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        # Decimated rate seen by the analyzer (matches Spectrogram internals).
        m_down = 8 if self.fs > 48000 else 2
        m.d.comb += [
            self._n_bins.f.n_bins.r_data.eq(self.sz),
            self._fs_bins.f.fs_bins.r_data.eq(int(self.fs // m_down)),
        ]

        with m.If(self._flags.f.enable.w_stb):
            m.d.sync += self.soc_en.eq(self._flags.f.enable.w_data)

        with m.If(self._flags.f.freq_log.w_stb):
            m.d.sync += spectrogram.freq_scale_log.eq(self._flags.f.freq_log.w_data)

        with m.If(self._smooth.f.smooth.w_stb):
            m.d.sync += spectrogram.smooth.eq(self._smooth.f.smooth.w_data)

        dsp.connect_remap(m, self.i, spectrogram.i, lambda o, i: [
            i.payload[n].eq(o.payload[n]) for n in range(4)])
        # Swap channels 0/1 so the frequency axis lands on x.
        dsp.connect_remap(m, spectrogram.o, self.o, lambda o, i: [
            i.payload[0].eq(o.payload[1]),
            i.payload[1].eq(o.payload[0]),
            i.payload[2].eq(o.payload[2]),
            i.payload[3].eq(o.payload[3]),
        ])

        return m
