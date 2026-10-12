fn main() {
    let library = std::env::var("DEP_ZVEC_RUST_LIB_DIR").expect("candidate native library");
    println!("cargo:rustc-link-arg=-Wl,-rpath,{library}");
}
