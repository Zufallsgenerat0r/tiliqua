#[macro_export]
macro_rules! impl_spectrum {
    ($( $SPECX:ident: $PACSPECX:ty, )+) => { $(
        pub struct $SPECX {
            registers: $PACSPECX,
        }

        impl $SPECX {
            pub fn new(registers: $PACSPECX) -> Self {
                Self { registers }
            }

            pub fn set_enabled(&mut self, enabled: bool, freq_log: bool) {
                self.registers.flags().write(|w| {
                    w.enable().bit(enabled);
                    w.freq_log().bit(freq_log)
                });
            }

            pub fn set_smooth(&mut self, smooth: u8) {
                self.registers.smooth().write(|w| unsafe { w.smooth().bits(smooth) });
            }

            pub fn n_bins(&self) -> u16 {
                self.registers.n_bins().read().n_bins().bits()
            }

            pub fn fs_bins(&self) -> u32 {
                self.registers.fs_bins().read().fs_bins().bits()
            }
        }
    )+ };
}
