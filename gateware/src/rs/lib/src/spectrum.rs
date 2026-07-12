use serde_derive::{Serialize, Deserialize};
use strum_macros::{EnumIter, IntoStaticStr};

#[derive(Default, Clone, Copy, PartialEq, EnumIter, IntoStaticStr, Serialize, Deserialize)]
#[strum(serialize_all = "kebab-case")]
pub enum FreqScale {
    #[default]
    Linear,
    Log,
}

#[cfg(test)]
mod tests {
    use super::*;

    // Persisted options are postcard-encoded by variant order; these
    // discriminants must never change (append new variants at the end).
    #[test]
    fn freq_scale_discriminants_are_stable() {
        let mut buf = [0u8; 8];
        assert_eq!(postcard::to_slice(&FreqScale::Linear, &mut buf).unwrap(), &[0]);
        assert_eq!(postcard::to_slice(&FreqScale::Log, &mut buf).unwrap(), &[1]);
    }
}
