use serde_derive::{Serialize, Deserialize};
use strum_macros::{EnumIter, IntoStaticStr};

#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum Timebase {
    #[strum(serialize = "500ms/d")]
    Timebase500ms,
    #[strum(serialize = "200ms/d")]
    Timebase200ms,
    #[default]
    #[strum(serialize = "100ms/d")]
    Timebase100ms,
    #[strum(serialize = "50ms/d")]
    Timebase50ms,
    #[strum(serialize = "20ms/d")]
    Timebase20ms,
    #[strum(serialize = "10ms/d")]
    Timebase10ms,
    #[strum(serialize = "5ms/d")]
    Timebase5ms,
    #[strum(serialize = "2ms/d")]
    Timebase2ms,
    #[strum(serialize = "1ms/d")]
    Timebase1ms,
    #[strum(serialize = "500us/d")]
    Timebase500us,
    #[strum(serialize = "200us/d")]
    Timebase200us,
    #[strum(serialize = "100us/d")]
    Timebase100us,
    #[strum(serialize = "50us/d")]
    Timebase50us,
    // Auto is feature-gated and appended at the end so its presence does not
    // shift the postcard-encoded discriminants of persisted Timebase values.
    #[cfg(feature = "auto_timebase")]
    #[strum(serialize = "auto")]
    Auto,
}

impl Timebase {
    /// Return the time per division in microseconds, or None for `Auto`
    /// (which is driven from a runtime period measurement, not a fixed value).
    pub fn t_div_us(&self) -> Option<u64> {
        match self {
            #[cfg(feature = "auto_timebase")]
            Timebase::Auto          => None,
            Timebase::Timebase500ms => Some(500_000),
            Timebase::Timebase200ms => Some(200_000),
            Timebase::Timebase100ms => Some(100_000),
            Timebase::Timebase50ms  => Some(50_000),
            Timebase::Timebase20ms  => Some(20_000),
            Timebase::Timebase10ms  => Some(10_000),
            Timebase::Timebase5ms   => Some(5_000),
            Timebase::Timebase2ms   => Some(2_000),
            Timebase::Timebase1ms   => Some(1_000),
            Timebase::Timebase500us => Some(500),
            Timebase::Timebase200us => Some(200),
            Timebase::Timebase100us => Some(100),
            Timebase::Timebase50us  => Some(50),
        }
    }
}

#[cfg(feature = "auto_timebase")]
pub const AUTO_TIMEBASE_MIN_PERIOD_SAMPLES: u32 = 8;

#[cfg(feature = "auto_timebase")]
pub fn auto_timebase_period_samples(raw_period: u32, fs_up: u32) -> u32 {
    let max_period_samples =
        auto_timebase_max_period_samples(fs_up).max(AUTO_TIMEBASE_MIN_PERIOD_SAMPLES);

    raw_period
        .max(AUTO_TIMEBASE_MIN_PERIOD_SAMPLES)
        .min(max_period_samples)
}

#[cfg(feature = "auto_timebase")]
fn auto_timebase_max_period_samples(fs_up: u32) -> u32 {
    let max_screen_us = Timebase::Timebase500ms.t_div_us().unwrap_or(0) * 10;
    ((fs_up as u64 * max_screen_us) / 1_000_000).min(u32::MAX as u64) as u32
}

#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum VScale {
    #[strum(serialize = "8V/d")]
    Scale8V,
    #[strum(serialize = "4V/d")]
    Scale4V,
    #[strum(serialize = "2V/d")]
    Scale2V,
    #[default]
    #[strum(serialize = "1V/d")]
    Scale1V,
    #[strum(serialize = "500mV/d")]
    Scale500mV,
    #[strum(serialize = "250mV/d")]
    Scale250mV,
    #[strum(serialize = "125mV/d")]
    Scale125mV,
    #[strum(serialize = "64mV/d")]
    Scale64mV,
}

impl VScale {
    pub fn to_scale_bits(&self) -> u8 {
        match self {
            VScale::Scale8V    => 9,
            VScale::Scale4V    => 8,
            VScale::Scale2V    => 7,
            VScale::Scale1V    => 6,
            VScale::Scale500mV => 5,
            VScale::Scale250mV => 4,
            VScale::Scale125mV => 3,
            VScale::Scale64mV  => 2,
        }
    }
}

#[cfg(test)]
mod tests {
    #[cfg(feature = "auto_timebase")]
    use super::*;

    #[cfg(feature = "auto_timebase")]
    const FS_UP: u32 = 1_536_000;

    #[cfg(feature = "auto_timebase")]
    #[test]
    fn auto_timebase_preserves_one_hz_period_at_scope_sample_rate() {
        assert_eq!(auto_timebase_period_samples(FS_UP, FS_UP), FS_UP);
    }

    #[cfg(feature = "auto_timebase")]
    #[test]
    fn auto_timebase_clamps_short_periods_to_minimum() {
        for raw in [0, 1, AUTO_TIMEBASE_MIN_PERIOD_SAMPLES - 1] {
            assert_eq!(
                auto_timebase_period_samples(raw, FS_UP),
                AUTO_TIMEBASE_MIN_PERIOD_SAMPLES
            );
        }
    }

    #[cfg(feature = "auto_timebase")]
    #[test]
    fn auto_timebase_clamps_long_periods_to_full_screen_at_slowest_timebase() {
        // Slowest manual timebase is 500ms/d * 10 divisions = 5s of screen.
        let max = FS_UP * 5;
        assert_eq!(auto_timebase_period_samples(max, FS_UP), max);
        assert_eq!(auto_timebase_period_samples(max + 1, FS_UP), max);
        assert_eq!(auto_timebase_period_samples(u32::MAX, FS_UP), max);
    }

    #[cfg(feature = "auto_timebase")]
    #[test]
    fn auto_timebase_passes_through_in_range_periods() {
        // 440 Hz at the scope sample rate.
        let period = FS_UP / 440;
        assert_eq!(auto_timebase_period_samples(period, FS_UP), period);
    }
}
