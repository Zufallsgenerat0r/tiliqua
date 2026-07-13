# Copyright (c) 2026 Seb Holzapfel <me@sebholzapfel.com>
#
# SPDX-License-Identifier: CERN-OHL-S-2.0

"""
8-channel USB2 audio interface with menu system and runtime routing.

Enumerates as a class-compliant, high-speed USB2 sound card exposing all
channels of the onboard audio interface *plus* an optional ``TLQ-EXPANDER``
plugged into the ``ex0`` (and/or ``ex1``) expansion port:

    .. code-block:: text

        Boards            USB device      Jacks
        ──────            ──────────      ─────
        (builtin)         ch0-3           in0-3 / out0-3
        first expander    ch4-7           expander in0-3 / out0-3
        second expander   ch8-11          (only with both ports populated)

    Channel ranges are positional in board attach order: builtin first, then
    the ``ex0`` expander (if enabled), then ``ex1`` (if enabled). With only
    one expander - on either port - its jacks are channels 4-7.

Every *destination* (each physical output jack and each USB capture channel)
selects its *source* at runtime through the menu system:

    .. code-block:: text

        in0-7 ────────►┌────────┐     ┌──────────────┐     ┌────────┐
                       │        ├────►│Nin/Nout USB  ├────►│Computer│
                       │ ROUTER │◄────│Audio I/F     │◄────│(USB2)  │
        out0-7 ◄───────┤        │     └──────────────┘     └────────┘
                       └────────┘

Each destination picks one of: ``off`` (silence), any physical input
(``in0`` .. ``in7``), or any USB playback channel (``usb0`` .. ``usb7``).
This covers the usual audio-interface tricks:

    - ``outN = usbN``:  ordinary DAW playback (default).
    - ``capN = inN``:   ordinary DAW recording (default).
    - ``outN = inM``:   zero-latency direct monitoring / passthrough.
    - ``capN = usbM``:  loopback recording of computer playback.

All routing selections may be saved to flash (``MISC: save-opts``) and are
restored on power-up, so the module works standalone without a screen once
configured.

The following options are tweakable in the menu:

    .. code-block:: text

        Page    Parameter     Description
        ────    ─────────     ───────────
        HELP    scroll        scroll help text up/down

        OUTS    out0..out3    source for builtin output jacks
        OUTS    out4..out7    source for expander output jacks (if attached;
                              out8..out11 with a second expander)

        CAPTURE cap0..cap7    source for USB capture channels (cap8..cap11
                              with a second expander)

        BEAM    ui-hue        menu hue
        BEAM    palette       color palette
        BEAM    persist       menu phosphor persistence

        MISC    rotation      screen rotation
        MISC    help          show/hide leftmost help page
        MISC    save-opts     save all options to flash
        MISC    wipe-opts     reset all options to defaults

    .. note::

        The recommended build flags for this bitstream are
        ``TILIQUA_ASQ_WIDTH=20 TILIQUA_ASQ_I_BITS=2``: a 20-bit audio path
        whose quantization noise sits below the AK4619 noise floor, with
        audio full-scale (0 dBFS on the USB device) at ±16.384V. Jacks are
        DC-coupled, so CV can be recorded and played back like any audio
        signal - line-level audio will simply peak well below 0 dBFS.
        (``TILIQUA_ASQ_WIDTH=24`` also works but currently fails ``dvi_clk``
        timing on the LFE5U-25F due to routing congestion.)

    .. note::

        By default, this core builds for 48kHz sampling. ``--fs-192khz``
        also works, however 192kHz is limited to 8 channels total (USB2
        high-speed isochronous packet size limit), i.e. at most one
        expander.

"""

import os
import sys

from amaranth import *
from amaranth.lib import data, stream, wiring
from amaranth.lib.wiring import In, Out, connect, flipped
from amaranth_soc import csr

from tiliqua import dsp, usb_audio
from tiliqua.build import sim
from tiliqua.build.cli import top_level_cli
from tiliqua.build.types import BitstreamHelp
from tiliqua.periph import eurorack_pmod, i2c
from tiliqua.tiliqua_soc import TiliquaSoc


class RouterPeripheral(wiring.Component):

    """
    CSR peripheral holding one source-select register per routing destination.

    Destination index convention (N = total channels):
        0 .. N-1     physical outputs (builtin out0-3, then ex0, then ex1)
        N .. 2N-1    USB capture channels 0 .. N-1

    Source encoding (see also fw/src/options.rs):
        0            off (silence)
        1 .. N       physical inputs (builtin in0-3, then ex0, then ex1)
        N+1 .. 2N    USB playback channels 0 .. N-1
    """

    class Flags(csr.Register, access="w"):
        usb_connect: csr.Field(csr.action.W, unsigned(1))

    class RouteIndex(csr.Register, access="w"):
        index: csr.Field(csr.action.W, unsigned(5))

    class RouteSource(csr.Register, access="w"):
        source: csr.Field(csr.action.W, unsigned(5))

    def __init__(self, n_dests, default_sources):
        assert len(default_sources) == n_dests
        # Source encoding (0..2N) and destination index must fit the 5-bit
        # CSR fields below.
        assert n_dests <= 31
        self.n_dests = n_dests
        self.default_sources = default_sources
        regs = csr.Builder(addr_width=5, data_width=8)
        self._flags = regs.add("flags", self.Flags(), offset=0x0)
        # Indexed write protocol: set `route_index`, then writing
        # `route_source` latches the select for that destination.
        self._route_index = regs.add("route_index", self.RouteIndex(), offset=0x4)
        self._route_source = regs.add("route_source", self.RouteSource(), offset=0x8)
        self._bridge = csr.Bridge(regs.as_memory_map())
        super().__init__({
            "usb_connect": Out(1),
            "sel": Out(unsigned(5)).array(n_dests),
            "bus": In(csr.Signature(addr_width=regs.addr_width, data_width=regs.data_width)),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge = self._bridge
        connect(m, flipped(self.bus), self._bridge.bus)

        with m.If(self._flags.f.usb_connect.w_stb):
            m.d.sync += self.usb_connect.eq(self._flags.f.usb_connect.w_data)

        index = Signal(5)
        with m.If(self._route_index.f.index.w_stb):
            m.d.sync += index.eq(self._route_index.f.index.w_data)

        # Sane passthrough routing before the firmware has booted.
        sels = [Signal(5, init=self.default_sources[d]) for d in range(self.n_dests)]
        with m.If(self._route_source.f.source.w_stb):
            with m.Switch(index):
                for d in range(self.n_dests):
                    with m.Case(d):
                        m.d.sync += sels[d].eq(self._route_source.f.source.w_data)
        for d in range(self.n_dests):
            m.d.comb += self.sel[d].eq(sels[d])

        return m


class Router(wiring.Component):

    """
    Per-destination source-select crossbar between physical channels and a
    USB2 audio interface.

    Stream timing is mastered by ``i_phys`` (the merged physical inputs,
    which always tick at fs as the FPGA drives the I2S clocks). ``i_usb``
    is drained into per-channel hold registers at most once per routed
    frame: this keeps the USB DAC FIFO level regulated by the interface's
    isochronous feedback endpoint (an always-ready drain would pin the FIFO
    empty and defeat rate matching), while never blocking the physical
    streams when the host stops streaming. After ``starve_frames`` routed
    frames without fresh USB samples, USB sources are muted to avoid
    holding a stale DC value on the outputs.
    """

    def __init__(self, n_channels, starve_frames):
        # Source encoding 0..2N must fit the 5-bit `sel` inputs.
        assert 2*n_channels <= 31
        self.n_channels = n_channels
        self.starve_frames = starve_frames
        super().__init__({
            "i_phys": In(stream.Signature(data.ArrayLayout(eurorack_pmod.ASQ, n_channels))),
            "i_usb":  In(stream.Signature(data.ArrayLayout(eurorack_pmod.ASQ, n_channels))),
            "sel":    In(unsigned(5)).array(2*n_channels),
            "o":      Out(stream.Signature(data.ArrayLayout(eurorack_pmod.ASQ, 2*n_channels))),
        })

    def elaborate(self, platform):
        m = Module()

        n = self.n_channels

        usb_hold = Signal(data.ArrayLayout(eurorack_pmod.ASQ, n))
        starve   = Signal(range(self.starve_frames + 1))
        starved  = Signal()

        frame = Signal()
        m.d.comb += [
            self.o.valid.eq(self.i_phys.valid),
            self.i_phys.ready.eq(self.o.ready),
            frame.eq(self.o.valid & self.o.ready),
            # Take at most one USB frame per routed frame; don't block if
            # the host isn't streaming.
            self.i_usb.ready.eq(frame),
            starved.eq(starve == self.starve_frames),
        ]

        with m.If(self.i_usb.valid & self.i_usb.ready):
            m.d.sync += [
                usb_hold.eq(self.i_usb.payload),
                starve.eq(0),
            ]
        with m.Elif(frame & ~starved):
            m.d.sync += starve.eq(starve + 1)

        usb_src = Signal(data.ArrayLayout(eurorack_pmod.ASQ, n))
        with m.If(~starved):
            m.d.comb += usb_src.eq(usb_hold)

        for d in range(2*n):
            with m.Switch(self.sel[d]):
                for k in range(n):
                    with m.Case(1 + k):
                        m.d.comb += self.o.payload[d].eq(self.i_phys.payload[k])
                for k in range(n):
                    with m.Case(1 + n + k):
                        m.d.comb += self.o.payload[d].eq(usb_src[k])
                with m.Default():
                    m.d.comb += self.o.payload[d].eq(0)

        return m


class UsbAudioSoc(TiliquaSoc):

    # Used by `tiliqua_soc.py` to create a MODULE_DOCSTRING rust constant used by the 'help' page.
    module_docstring = sys.modules[__name__].__doc__

    # Stored in manifest and used by bootloader for brief summary of each bitstream.
    # (overridden with the real channel count in __init__)
    bitstream_help = BitstreamHelp(
        brief="USB soundcard with runtime routing.",
        io_left=['in0', 'in1', 'in2', 'in3', 'out0', 'out1', 'out2', 'out3'],
        io_right=['navigate menu', 'audio device', 'video out', '', '', '']
    )

    def __init__(self, **kwargs):

        self.expander_ex0 = kwargs.pop("expander_ex0", True)
        self.expander_ex1 = kwargs.pop("expander_ex1", False)
        self.nr_channels = 4 + 4*self.expander_ex0 + 4*self.expander_ex1

        # don't finalize the CSR bridge in TiliquaSoc, we're adding more peripherals.
        super().__init__(finalize_csr_bridge=False, **kwargs)

        if self.clock_settings.audio_clock.is_192khz():
            assert self.nr_channels <= 8, \
                f"192kHz supports at most 8 channels (1024B packet limit), got {self.nr_channels}"

        # Channel ranges are positional in board attach order (builtin,
        # then ex0 if attached, then ex1 if attached).
        n = self.nr_channels
        ex_label = {}
        ch = 4
        for port, attached in [("ex0", self.expander_ex0), ("ex1", self.expander_ex1)]:
            ex_label[port] = f'{port} audio (ch{ch}-{ch+3})' if attached else ''
            ch += 4 if attached else 0
        self.bitstream_help = BitstreamHelp(
            brief=f"USB soundcard, {n}in + {n}out, runtime routing.",
            io_left=['in0', 'in1', 'in2', 'in3', 'out0', 'out1', 'out2', 'out3'],
            io_right=['navigate menu', f'{n}x{n} audio device', 'video out',
                      ex_label["ex0"], ex_label["ex1"], '']
        )

        self.router_periph_base   = 0x00001000
        self.ex0_pmod_periph_base = 0x00001100
        self.i2c_ex0_base         = 0x00001200
        self.ex1_pmod_periph_base = 0x00001300
        self.i2c_ex1_base         = 0x00001400

        if self.expander_ex0:
            self.ex0_pmod = eurorack_pmod.EurorackPmod(
                self.clock_settings.audio_clock)
            self.ex0_pmod_periph = eurorack_pmod.Peripheral(pmod=self.ex0_pmod)
            self.csr_decoder.add(self.ex0_pmod_periph.bus,
                                 addr=self.ex0_pmod_periph_base, name="ex0_pmod_periph")
            self.i2c_ex0 = i2c.Peripheral()
            self.csr_decoder.add(self.i2c_ex0.bus,
                                 addr=self.i2c_ex0_base, name="i2c2")
        if self.expander_ex1:
            self.ex1_pmod = eurorack_pmod.EurorackPmod(
                self.clock_settings.audio_clock)
            self.ex1_pmod_periph = eurorack_pmod.Peripheral(pmod=self.ex1_pmod)
            self.csr_decoder.add(self.ex1_pmod_periph.bus,
                                 addr=self.ex1_pmod_periph_base, name="ex1_pmod_periph")
            self.i2c_ex1 = i2c.Peripheral()
            self.csr_decoder.add(self.i2c_ex1.bus,
                                 addr=self.i2c_ex1_base, name="i2c3")

        # Default routing before firmware boots (and firmware defaults):
        # outputs play the corresponding USB channel, capture channels
        # record the corresponding physical input.
        defaults = [1 + n + d for d in range(n)] + [1 + c for c in range(n)]
        self.router_periph = RouterPeripheral(n_dests=2*n, default_sources=defaults)
        self.csr_decoder.add(self.router_periph.bus,
                             addr=self.router_periph_base, name="router_periph")

        # Let the firmware know (build.rs) which expanders exist in this image.
        os.environ["TILIQUA_EXPANDER_EX0"] = "1" if self.expander_ex0 else "0"
        os.environ["TILIQUA_EXPANDER_EX1"] = "1" if self.expander_ex1 else "0"

        # now we can freeze the memory map
        self.finalize_csr_bridge()

    def elaborate(self, platform):

        m = Module()

        m.submodules.router_periph = self.router_periph

        # FIXME: bit of a hack so we can pluck out peripherals from `tiliqua_soc`
        m.submodules += super().elaborate(platform)

        pmod0 = self.pmod0_periph.pmod
        pmods = [pmod0]

        if self.expander_ex0:
            m.submodules.ex0_pmod = self.ex0_pmod
            m.submodules.ex0_pmod_periph = self.ex0_pmod_periph
            m.submodules.i2c_ex0 = self.i2c_ex0
            wiring.connect(m, self.i2c_ex0.i2c_stream,
                           self.ex0_pmod.i2c_master.i2c_override)
            pmods.append(self.ex0_pmod)
            if sim.is_hw(platform):
                m.submodules.ex0_provider = eurorack_pmod.PMODProvider(0)
                wiring.connect(m, self.ex0_pmod.pins, m.submodules.ex0_provider.pins)
                m.d.comb += self.ex0_pmod_periph.mute.eq(self.reboot.mute)
        if self.expander_ex1:
            m.submodules.ex1_pmod = self.ex1_pmod
            m.submodules.ex1_pmod_periph = self.ex1_pmod_periph
            m.submodules.i2c_ex1 = self.i2c_ex1
            wiring.connect(m, self.i2c_ex1.i2c_stream,
                           self.ex1_pmod.i2c_master.i2c_override)
            pmods.append(self.ex1_pmod)
            if sim.is_hw(platform):
                m.submodules.ex1_provider = eurorack_pmod.PMODProvider(1)
                wiring.connect(m, self.ex1_pmod.pins, m.submodules.ex1_provider.pins)
                m.d.comb += self.ex1_pmod_periph.mute.eq(self.reboot.mute)

        n = self.nr_channels
        assert 4*len(pmods) == n
        fs = self.clock_settings.audio_clock.fs()

        # Mute USB-sourced outputs ~2ms after the host stops streaming.
        m.submodules.router = router = Router(
            n_channels=n, starve_frames=fs//500)
        for d in range(2*n):
            m.d.comb += router.sel[d].eq(self.router_periph.sel[d])

        if sim.is_hw(platform):
            m.submodules.usbif = usbif = usb_audio.USB2AudioInterface(
                    audio_clock=self.clock_settings.audio_clock,
                    nr_channels=n,
                    serial=f"usbaudio-{n}x{n}",
                    bcd_device=0.02)
            # SoC-controlled USB PHY connection (based on typeC CC status)
            m.d.comb += usbif.usb_connect.eq(self.router_periph.usb_connect)
            wiring.connect(m, usbif.o, router.i_usb)

        # Physical capture fan-in: all pmods share the audio clock domain and
        # so tick in lockstep, making a lossless Merge safe here.
        if len(pmods) == 1:
            wiring.connect(m, pmod0.o_cal, router.i_phys)
        else:
            m.submodules.in_merge = in_merge = dsp.Merge(n_channels=n)
            for idx, pmod in enumerate(pmods):
                in_split = dsp.Split(n_channels=4)
                setattr(m.submodules, f"in_split{idx}", in_split)
                wiring.connect(m, pmod.o_cal, in_split.i)
                for ch in range(4):
                    wiring.connect(m, in_split.o[ch], in_merge.i[4*idx + ch])
            wiring.connect(m, in_merge.o, router.i_phys)

        # Routed stream fan-out to each pmod's outputs + the USB capture
        # channels. Split tracks per-output completion, so sinks may assert
        # `ready` independently.
        n_sinks = len(pmods) + (1 if sim.is_hw(platform) else 0)
        m.submodules.out_split = out_split = dsp.Split(
            n_channels=n_sinks, replicate=True,
            shape=data.ArrayLayout(eurorack_pmod.ASQ, 2*n))
        wiring.connect(m, router.o, out_split.i)
        for idx, pmod in enumerate(pmods):
            dsp.channel_remap(m, out_split.o[idx], pmod.i_cal,
                              {4*idx + ch: ch for ch in range(4)})
        if sim.is_hw(platform):
            dsp.channel_remap(m, out_split.o[len(pmods)], usbif.i,
                              {n + ch: ch for ch in range(n)})

        return m


def argparse_callback(parser):
    parser.add_argument('--no-expander-ex0', action='store_true', default=False,
                        help="Do not expect an expander audio board on PMOD expansion port 0 (-4ch USB audio)")
    parser.add_argument('--expander-ex1', action='store_true', default=False,
                        help="Enable extra audio board on PMOD expansion port 1 (+4ch USB audio)")

def argparse_fragment(args):
    # Every expander combination gets a distinct name: the CSR maps, PMOD
    # pinouts and firmware cfgs differ, so their build directories and
    # bootloader entries must never be confused for each other.
    if args.no_expander_ex0:
        args.name = args.name + '-NOEX0'
    if args.expander_ex1:
        args.name = args.name + '-EX1'
    return {
        "expander_ex0": not args.no_expander_ex0,
        "expander_ex1": args.expander_ex1,
    }

if __name__ == "__main__":
    this_path = os.path.dirname(os.path.realpath(__file__))
    top_level_cli(UsbAudioSoc, path=this_path,
                  argparse_callback=argparse_callback,
                  argparse_fragment=argparse_fragment,
                  archiver_callback=lambda archiver: archiver.with_option_storage())
