// cargo run -p tdlib-rs --example test_ci --features default
// cargo run -p tdlib-rs --example test_ci --features download-tdlib
// cargo run -p tdlib-rs --example test_ci --features pkg-config

#[tokio::main]
async fn main() {
    // Force linking the fork's file-system hook as well as the upstream API.
    std::hint::black_box(tdlib_rs::file_system::set_file_system as *const ());
    assert!(tdlib_rs::create_client() > 0);

    // Exit 0
    std::process::exit(0);
}
