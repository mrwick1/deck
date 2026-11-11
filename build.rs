// The web app is embedded from `web/dist`. Without Node that folder does not exist.
// Write a placeholder page then, so `cargo build` and `cargo test` still work.
fn main() {
    let index = std::path::Path::new("web/dist/index.html");
    if !index.exists() {
        std::fs::create_dir_all("web/dist").expect("create web/dist");
        std::fs::write(
            index,
            "<!doctype html><title>deck</title><p>The web app is not built. Run <code>npm run build</code> in <code>web/</code>, then rebuild deck.</p>",
        )
        .expect("write the placeholder page");
    }
    println!("cargo:rerun-if-changed=web/dist");
}
