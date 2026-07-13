use opts::*;
use strum_macros::{EnumIter, IntoStaticStr};
use tiliqua_lib::palette::ColorPalette;
use tiliqua_hal::dma_framebuffer::Rotate;
use serde_derive::{Serialize, Deserialize};

/// Total audio channels in this image (matches gateware `nr_channels`):
/// builtin (4) + one per attached expander board (4 each).
pub const N_CHANNELS: u8 = 4
    + if cfg!(expander_ex0) { 4 } else { 0 }
    + if cfg!(expander_ex1) { 4 } else { 0 };

#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "SCREAMING-KEBAB-CASE")]
pub enum Page {
    #[default]
    Help,
    Outs,
    Capture,
    Beam,
    Misc,
}

#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum HelpPage {
    Off,
    #[default]
    On,
}

/// One source selection for a routing destination (a physical output jack or
/// a USB capture channel).
///
/// Channels are positional: 0-3 = builtin board, 4-7 = first attached
/// expander, 8-11 = second attached expander. The CSR encoding is
/// `0 = off, 1..=N = physical input, N+1..=2N = USB playback channel`
/// (see `RouterPeripheral` in top.py); `to_csr()` maps accordingly.
///
/// NOTE: building with a different expander configuration re-numbers the
/// cfg-gated variants and therefore stales any persisted routing selections;
/// `MISC: wipe-opts` recovers.
#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum Source {
    #[default]
    Off,
    In0,
    In1,
    In2,
    In3,
    #[cfg(any(expander_ex0, expander_ex1))]
    In4,
    #[cfg(any(expander_ex0, expander_ex1))]
    In5,
    #[cfg(any(expander_ex0, expander_ex1))]
    In6,
    #[cfg(any(expander_ex0, expander_ex1))]
    In7,
    #[cfg(all(expander_ex0, expander_ex1))]
    In8,
    #[cfg(all(expander_ex0, expander_ex1))]
    In9,
    #[cfg(all(expander_ex0, expander_ex1))]
    In10,
    #[cfg(all(expander_ex0, expander_ex1))]
    In11,
    Usb0,
    Usb1,
    Usb2,
    Usb3,
    #[cfg(any(expander_ex0, expander_ex1))]
    Usb4,
    #[cfg(any(expander_ex0, expander_ex1))]
    Usb5,
    #[cfg(any(expander_ex0, expander_ex1))]
    Usb6,
    #[cfg(any(expander_ex0, expander_ex1))]
    Usb7,
    #[cfg(all(expander_ex0, expander_ex1))]
    Usb8,
    #[cfg(all(expander_ex0, expander_ex1))]
    Usb9,
    #[cfg(all(expander_ex0, expander_ex1))]
    Usb10,
    #[cfg(all(expander_ex0, expander_ex1))]
    Usb11,
}

impl Source {
    pub fn to_csr(&self) -> u8 {
        const IN_BASE: u8 = 1;
        const USB_BASE: u8 = 1 + N_CHANNELS;
        match self {
            Source::Off  => 0,
            Source::In0  => IN_BASE + 0,
            Source::In1  => IN_BASE + 1,
            Source::In2  => IN_BASE + 2,
            Source::In3  => IN_BASE + 3,
            #[cfg(any(expander_ex0, expander_ex1))]
            Source::In4  => IN_BASE + 4,
            #[cfg(any(expander_ex0, expander_ex1))]
            Source::In5  => IN_BASE + 5,
            #[cfg(any(expander_ex0, expander_ex1))]
            Source::In6  => IN_BASE + 6,
            #[cfg(any(expander_ex0, expander_ex1))]
            Source::In7  => IN_BASE + 7,
            #[cfg(all(expander_ex0, expander_ex1))]
            Source::In8  => IN_BASE + 8,
            #[cfg(all(expander_ex0, expander_ex1))]
            Source::In9  => IN_BASE + 9,
            #[cfg(all(expander_ex0, expander_ex1))]
            Source::In10 => IN_BASE + 10,
            #[cfg(all(expander_ex0, expander_ex1))]
            Source::In11 => IN_BASE + 11,
            Source::Usb0  => USB_BASE + 0,
            Source::Usb1  => USB_BASE + 1,
            Source::Usb2  => USB_BASE + 2,
            Source::Usb3  => USB_BASE + 3,
            #[cfg(any(expander_ex0, expander_ex1))]
            Source::Usb4  => USB_BASE + 4,
            #[cfg(any(expander_ex0, expander_ex1))]
            Source::Usb5  => USB_BASE + 5,
            #[cfg(any(expander_ex0, expander_ex1))]
            Source::Usb6  => USB_BASE + 6,
            #[cfg(any(expander_ex0, expander_ex1))]
            Source::Usb7  => USB_BASE + 7,
            #[cfg(all(expander_ex0, expander_ex1))]
            Source::Usb8  => USB_BASE + 8,
            #[cfg(all(expander_ex0, expander_ex1))]
            Source::Usb9  => USB_BASE + 9,
            #[cfg(all(expander_ex0, expander_ex1))]
            Source::Usb10 => USB_BASE + 10,
            #[cfg(all(expander_ex0, expander_ex1))]
            Source::Usb11 => USB_BASE + 11,
        }
    }
}

int_params!(PersistParams<u8>     { step: 1, min: 1, max: 80 });
int_params!(HueParams<u8>         { step: 1, min: 0, max: 15 });
int_params!(ScrollParams<u8>      { step: 1, min: 0, max: 125 });

button_params!(OneShotButtonParams { mode: ButtonMode::OneShot });

#[derive(OptionPage, Clone)]
pub struct HelpOpts {
    #[option(0)]
    pub scroll: IntOption<ScrollParams>,
}

#[derive(OptionPage, Clone)]
pub struct OutsOpts {
    #[option(Source::Usb0)]
    pub out0: EnumOption<Source>,
    #[option(Source::Usb1)]
    pub out1: EnumOption<Source>,
    #[option(Source::Usb2)]
    pub out2: EnumOption<Source>,
    #[option(Source::Usb3)]
    pub out3: EnumOption<Source>,
    #[cfg(any(expander_ex0, expander_ex1))]
    #[option(Source::Usb4)]
    pub out4: EnumOption<Source>,
    #[cfg(any(expander_ex0, expander_ex1))]
    #[option(Source::Usb5)]
    pub out5: EnumOption<Source>,
    #[cfg(any(expander_ex0, expander_ex1))]
    #[option(Source::Usb6)]
    pub out6: EnumOption<Source>,
    #[cfg(any(expander_ex0, expander_ex1))]
    #[option(Source::Usb7)]
    pub out7: EnumOption<Source>,
    #[cfg(all(expander_ex0, expander_ex1))]
    #[option(Source::Usb8)]
    pub out8: EnumOption<Source>,
    #[cfg(all(expander_ex0, expander_ex1))]
    #[option(Source::Usb9)]
    pub out9: EnumOption<Source>,
    #[cfg(all(expander_ex0, expander_ex1))]
    #[option(Source::Usb10)]
    pub out10: EnumOption<Source>,
    #[cfg(all(expander_ex0, expander_ex1))]
    #[option(Source::Usb11)]
    pub out11: EnumOption<Source>,
}

#[derive(OptionPage, Clone)]
pub struct CaptureOpts {
    #[option(Source::In0)]
    pub cap0: EnumOption<Source>,
    #[option(Source::In1)]
    pub cap1: EnumOption<Source>,
    #[option(Source::In2)]
    pub cap2: EnumOption<Source>,
    #[option(Source::In3)]
    pub cap3: EnumOption<Source>,
    #[cfg(any(expander_ex0, expander_ex1))]
    #[option(Source::In4)]
    pub cap4: EnumOption<Source>,
    #[cfg(any(expander_ex0, expander_ex1))]
    #[option(Source::In5)]
    pub cap5: EnumOption<Source>,
    #[cfg(any(expander_ex0, expander_ex1))]
    #[option(Source::In6)]
    pub cap6: EnumOption<Source>,
    #[cfg(any(expander_ex0, expander_ex1))]
    #[option(Source::In7)]
    pub cap7: EnumOption<Source>,
    #[cfg(all(expander_ex0, expander_ex1))]
    #[option(Source::In8)]
    pub cap8: EnumOption<Source>,
    #[cfg(all(expander_ex0, expander_ex1))]
    #[option(Source::In9)]
    pub cap9: EnumOption<Source>,
    #[cfg(all(expander_ex0, expander_ex1))]
    #[option(Source::In10)]
    pub cap10: EnumOption<Source>,
    #[cfg(all(expander_ex0, expander_ex1))]
    #[option(Source::In11)]
    pub cap11: EnumOption<Source>,
}

#[derive(OptionPage, Clone)]
pub struct BeamOpts {
    #[option(15)]
    pub persist: IntOption<PersistParams>,
    #[option(10)]
    pub ui_hue: IntOption<HueParams>,
    #[option]
    pub palette: EnumOption<ColorPalette>,
}

#[derive(OptionPage, Clone)]
pub struct MiscOpts {
    #[option]
    pub rotation: EnumOption<Rotate>,
    #[option]
    pub help: EnumOption<HelpPage>,
    #[option(false)]
    pub save_opts: ButtonOption<OneShotButtonParams>,
    #[option(false)]
    pub wipe_opts: ButtonOption<OneShotButtonParams>,
}

#[derive(Options, Clone)]
pub struct Opts {
    pub tracker: ScreenTracker<Page>,
    #[page(Page::Help)]
    pub help: HelpOpts,
    #[page(Page::Outs)]
    pub outs: OutsOpts,
    #[page(Page::Capture)]
    pub capture: CaptureOpts,
    #[page(Page::Beam)]
    pub beam: BeamOpts,
    #[page(Page::Misc)]
    pub misc: MiscOpts,
}
