# Copyright (c) 2024 S. Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

from amaranth import *
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out

from amaranth_future import fixed

from . import ASQ


class Trigger(wiring.Component):

    """
    When trigger condition is met, output is set to 1, for 1 stream cycle.

    Currently this only implements rising edge trigger.

    With nonzero `hysteresis`, after firing, the trigger is re-armed only
    once the sample falls below ``threshold - hysteresis``. This suppresses
    repeated firing caused by noise near the threshold crossing. With
    ``hysteresis=0.0``, behaviour matches a plain rising-edge comparison.
    """

    def __init__(self, shape=ASQ, hysteresis=0.0):
        self.shape = shape
        self.hysteresis = hysteresis
        super().__init__({
            "i": In(stream.Signature(data.StructLayout({
                "sample":    shape,
                "threshold": shape,
            }))),
            "o": Out(stream.Signature(unsigned(1))),
        })

    def elaborate(self, platform):
        m = Module()

        armed = Signal()
        arm_lvl = Signal(shape=self.shape)
        m.d.comb += arm_lvl.eq(
            self.i.payload.threshold - fixed.Const(self.hysteresis, shape=self.shape))

        m.d.comb += [
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
        ]

        with m.If(self.i.valid & self.o.ready):
            fire = armed & (self.i.payload.sample >= self.i.payload.threshold)
            m.d.comb += self.o.payload.eq(fire)
            with m.If(fire):
                m.d.sync += armed.eq(0)
            with m.Elif(self.i.payload.sample < arm_lvl):
                m.d.sync += armed.eq(1)

        return m


class Ramp(wiring.Component):

    """
    If trigger strobes a 1, ramps from -1 to 1, staying at 1 until retriggered.
    A retrigger mid-ramp does not restart the ramp until the output has reached 1.
    """

    TIMEBASE_SQ = fixed.SQ(8, 24)

    def __init__(self, shape=ASQ, shift=6):
        self.shape = shape
        self.shift = shift
        super().__init__({
            "i": In(stream.Signature(data.StructLayout({
                "trigger":  unsigned(1),
                "td":       self.TIMEBASE_SQ, # time delta
            }))),
            "o": Out(stream.Signature(shape)),
        })

    def elaborate(self, platform):
        m = Module()

        s = Signal(self.TIMEBASE_SQ)

        m.d.comb += [
            self.o.valid.eq(self.i.valid),
            self.i.ready.eq(self.o.ready),
            self.o.payload.eq(s >> self.shift),
        ]

        with m.If(self.i.valid & self.o.ready):
            with m.If(self.o.payload > fixed.Const(0.985, shape=self.shape)):
                with m.If(self.i.payload.trigger):
                    m.d.sync += s.eq(fixed.Const(-1.0, shape=self.shape, clamp=True) << self.shift)
            with m.Else():
                m.d.sync += s.eq(s + self.i.payload.td)

        return m
