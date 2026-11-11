mod api;
mod auth;
mod bare;
mod config;
mod model;
mod store;
#[cfg(test)]
mod testutil;
mod tools;

use std::net::SocketAddr;
use std::sync::Arc;

use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive("deck=info".parse()?))
        .init();
    let config = Arc::new(config::Config::load()?);
    let store = store::Store::open(&config.data_dir)?;
    let app = api::router(config.clone(), store);
    let listener = tokio::net::TcpListener::bind(config.listen).await?;
    tracing::info!("listening on {}", listener.local_addr()?);
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown())
    .await?;
    Ok(())
}

async fn shutdown() {
    use tokio::signal::unix::{SignalKind, signal};
    let mut term = signal(SignalKind::terminate()).expect("install SIGTERM handler");
    tokio::select! {
        _ = tokio::signal::ctrl_c() => {}
        _ = term.recv() => {}
    }
}
