fn main() {
    println!("cargo::rustc-check-cfg=cfg(expander_ex0)");
    println!("cargo::rustc-check-cfg=cfg(expander_ex1)");
    // Set by UsbAudioSoc in top.py to match the gateware configuration.
    // Defaults (env unset, e.g. a bare `cargo build`) must match the
    // gateware defaults: ex0 attached, ex1 absent.
    if std::env::var("TILIQUA_EXPANDER_EX0").ok().as_deref() != Some("0") {
        println!("cargo:rustc-cfg=expander_ex0");
    }
    if std::env::var("TILIQUA_EXPANDER_EX1").ok().as_deref() == Some("1") {
        println!("cargo:rustc-cfg=expander_ex1");
    }
    println!("cargo:rerun-if-env-changed=TILIQUA_EXPANDER_EX0");
    println!("cargo:rerun-if-env-changed=TILIQUA_EXPANDER_EX1");
}
