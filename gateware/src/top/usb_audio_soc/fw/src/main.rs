#![no_std]
#![no_main]

use critical_section::Mutex;
use log::{info, warn};
use riscv_rt::entry;
use irq::handler;
use core::cell::RefCell;

use tiliqua_fw::*;
use tiliqua_lib::*;
use pac::constants::*;
use tiliqua_lib::calibration::*;

use tiliqua_hal::embedded_graphics::prelude::*;

use options::*;
use opts::persistence::*;
use hal::pca9635::Pca9635Driver;
use tiliqua_hal::tusb322::{TUSB322Driver, TUSB322Mode, AttachedState};
use tiliqua_hal::persist::Persist;

pub const TIMER0_ISR_PERIOD_MS: u32 = 5;

struct App {
    ui: ui::UI<Encoder0, EurorackPmod0, I2c0, Opts>,
}

impl App {
    pub fn new(opts: Opts) -> Self {
        let peripherals = unsafe { pac::Peripherals::steal() };
        let encoder = Encoder0::new(peripherals.ENCODER0);
        let i2cdev = I2c0::new(peripherals.I2C0);
        let pca9635 = Pca9635Driver::new(i2cdev);
        let pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
        Self {
            ui: ui::UI::new(opts, TIMER0_ISR_PERIOD_MS,
                            encoder, pca9635, pmod),
        }
    }
}

fn timer0_handler(app: &Mutex<RefCell<App>>) {
    critical_section::with(|cs| {
        let mut app = app.borrow_ref_mut(cs);
        app.ui.update();

        if app.ui.opts.misc.help.value == HelpPage::Off
            && app.ui.opts.tracker.page.value == Page::Help {
            app.ui.opts.tracker.page.value = Page::Outs;
        }
    });
}

/// Routing destinations in gateware order: physical outputs first (builtin,
/// then each attached expander), then USB capture channels 0..N.
fn route_sources(opts: &Opts) -> heapless::Vec<Source, 24> {
    let mut sources = heapless::Vec::new();
    let o = &opts.outs;
    let c = &opts.capture;
    let mut push = |s: Source| { sources.push(s).ok(); };
    push(o.out0.value); push(o.out1.value); push(o.out2.value); push(o.out3.value);
    #[cfg(any(expander_ex0, expander_ex1))]
    { push(o.out4.value); push(o.out5.value); push(o.out6.value); push(o.out7.value); }
    #[cfg(all(expander_ex0, expander_ex1))]
    { push(o.out8.value); push(o.out9.value); push(o.out10.value); push(o.out11.value); }
    push(c.cap0.value); push(c.cap1.value); push(c.cap2.value); push(c.cap3.value);
    #[cfg(any(expander_ex0, expander_ex1))]
    { push(c.cap4.value); push(c.cap5.value); push(c.cap6.value); push(c.cap7.value); }
    #[cfg(all(expander_ex0, expander_ex1))]
    { push(c.cap8.value); push(c.cap9.value); push(c.cap10.value); push(c.cap11.value); }
    sources
}

#[entry]
fn main() -> ! {
    let peripherals = pac::Peripherals::take().unwrap();
    let sysclk = pac::clock::sysclk();
    let serial = Serial0::new(peripherals.UART0);
    let mut timer = Timer0::new(peripherals.TIMER0, sysclk);
    let mut persist = Persist0::new(peripherals.PERSIST_PERIPH);
    let spiflash = SPIFlash0::new(
        peripherals.SPIFLASH_CTRL,
        SPIFLASH_BASE,
        SPIFLASH_SZ_BYTES
    );

    tiliqua_fw::handlers::logger_init(serial);

    info!("Hello from Tiliqua USB-AUDIO-SOC! ({}x{} channels)", N_CHANNELS, N_CHANNELS);

    let bootinfo = unsafe { bootinfo::BootInfo::from_addr(BOOTINFO_BASE) }.unwrap();
    let modeline = bootinfo.modeline.maybe_override_fixed(
        FIXED_MODELINE, CLOCK_DVI_HZ);
    let mut display = DMAFramebuffer0::new(
        peripherals.FRAMEBUFFER_PERIPH,
        peripherals.PALETTE_PERIPH,
        peripherals.BLIT,
        peripherals.PIXEL_PLOT,
        peripherals.LINE,
        PSRAM_FB_BASE,
        modeline.clone(),
        BLIT_MEM_BASE,
    );

    let mut i2cdev1 = I2c1::new(peripherals.I2C1);
    let mut pmod = EurorackPmod0::new(peripherals.PMOD0_PERIPH);
    CalibrationConstants::load_or_default(&mut i2cdev1, &mut pmod);

    #[cfg(expander_ex0)]
    {
        info!("Loading calibration for expander EX0...");
        let mut i2c_ex0 = I2cEx0::new(peripherals.I2C2);
        let mut ex0_pmod = EurorackPmodEx0::new(peripherals.EX0_PMOD_PERIPH);
        CalibrationConstants::load_or_default(&mut i2c_ex0, &mut ex0_pmod);
    }

    #[cfg(expander_ex1)]
    {
        info!("Loading calibration for expander EX1...");
        let mut i2c_ex1 = I2cEx1::new(peripherals.I2C3);
        let mut ex1_pmod = EurorackPmodEx1::new(peripherals.EX1_PMOD_PERIPH);
        CalibrationConstants::load_or_default(&mut i2c_ex1, &mut ex1_pmod);
    }

    //
    // Start up TUSB322 in UFP/Device mode
    //

    let i2cdev_tusb = I2c0::new(unsafe { pac::I2C0::steal() } );
    let mut tusb322 = TUSB322Driver::new(i2cdev_tusb);
    tusb322.soft_reset().ok();
    tusb322.set_mode(TUSB322Mode::Ufp).ok();

    //
    // Create options and maybe load from persistent storage
    //

    let mut opts = Opts::default();
    opts.misc.rotation.value = modeline.rotate.clone();
    let mut flash_persist_opt = if let Some(storage_window) = bootinfo.manifest.get_option_storage_window() {
        let mut flash_persist = FlashOptionsPersistence::new(spiflash, storage_window);
        flash_persist.load_options(&mut opts).unwrap();
        Some(flash_persist)
    } else {
        warn!("No option storage region: disable persistent storage");
        None
    };

    //
    // Create App instance
    //

    let mut last_palette = opts.beam.palette.value;
    let app = Mutex::new(RefCell::new(App::new(opts)));

    handler!(timer0 = || timer0_handler(&app));

    irq::scope(|s| {

        s.register(handlers::Interrupt::TIMER0, timer0);

        timer.enable_tick_isr(TIMER0_ISR_PERIOD_MS,
                              pac::Interrupt::TIMER0);

        let router = peripherals.ROUTER_PERIPH;
        let mut first = true;
        let mut usb_cc_attached = false;

        loop {

            let h_active = display.size().width;
            let v_active = display.size().height;

            let (opts, draw_options, save_opts, wipe_opts) = critical_section::with(|cs| {
                let mut app = app.borrow_ref_mut(cs);
                let save_opts = app.ui.opts.misc.save_opts.poll();
                let wipe_opts = app.ui.opts.misc.wipe_opts.poll();
                (app.ui.opts.clone(), app.ui.draw(), save_opts, wipe_opts)
            });

            let on_help_page = opts.tracker.page.value == Page::Help;

            if opts.beam.palette.value != last_palette || first {
                opts.beam.palette.value.write_to_hardware(&mut display);
                last_palette = opts.beam.palette.value;
            }

            if draw_options || on_help_page {
                let (x, y) = if on_help_page {
                    (h_active/2-30, v_active-100)
                } else {
                    (h_active/2-60, v_active/2-50)
                };
                draw::draw_options(&mut display, &opts, x, y, opts.beam.ui_hue.value).ok();
                draw::draw_name(&mut display, h_active/2, v_active-50, opts.beam.ui_hue.value,
                                &bootinfo.manifest.name, &bootinfo.manifest.tag, &modeline).ok();
            }

            if on_help_page {
                draw::draw_help_page(&mut display,
                    MODULE_DOCSTRING,
                    bootinfo.manifest.help.as_ref(),
                    h_active,
                    v_active,
                    opts.help.scroll.value,
                    opts.beam.ui_hue.value).ok();
                persist.set_persistence(64);
            } else {
                persist.set_persistence(opts.beam.persist.value);
            }

            if save_opts {
                if let Some(ref mut flash_persist) = flash_persist_opt {
                    flash_persist.save_options(&opts).unwrap();
                }
            }

            if wipe_opts {
                critical_section::with(|cs| {
                    let mut app = app.borrow_ref_mut(cs);
                    app.ui.opts = Opts::default();
                    app.ui.opts.misc.rotation.value = modeline.rotate.clone();
                    if let Some(ref mut flash_persist) = flash_persist_opt {
                        flash_persist.erase_all().unwrap();
                    }
                });
            }

            // Only connect USB PHY if the TUSB322 Type-C controller says we are attached.
            // This fixes enumeration issues on some machines when using typec <-> typec cables.
            critical_section::with(|_| {
                if let Ok(status) = tusb322.read_connection_status_control() {
                    // Only update on valid reads to reduce risk of unintended toggling mid-stream
                    let new_state = status.attached_state == AttachedState::AttachedSnk;
                    if new_state != usb_cc_attached {
                        info!("USB CC hotplug: {:?}", status);
                        usb_cc_attached = new_state;
                    }
                }
            });

            router.flags().write(|w| w.usb_connect().bit(usb_cc_attached));

            // Push all routing selections to the gateware crossbar.
            for (dest, source) in route_sources(&opts).iter().enumerate() {
                router.route_index().write(|w| unsafe { w.index().bits(dest as u8) });
                router.route_source().write(|w| unsafe { w.source().bits(source.to_csr()) });
            }

            display.rotate(&opts.misc.rotation.value);

            first = false;
        }
    })
}
