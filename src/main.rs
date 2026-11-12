mod agent;
mod api;
mod auth;
mod bare;
mod config;
mod executor;
mod model;
mod prompt;
mod store;
#[cfg(test)]
mod testutil;
mod tools;
mod web;

use std::net::SocketAddr;
use std::sync::Arc;

use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) == Some("executor") {
        return executor::cli::run(&args[1..]).await;
    }
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive("deck=info".parse()?))
        .init();
    let config = Arc::new(config::Config::load()?);
    let store = store::Store::open(&config.data_dir)?;
    let key = std::env::var(&config.model.api_key_env).ok();
    if key.is_none() {
        tracing::warn!(
            "{} is not set: runs fail until it is set",
            config.model.api_key_env
        );
    }
    let model = model::ModelClient::new(&config.model.base_url, key, &config.model.api_key_env);
    // bare-mcp starts on the first browser tool call.
    let host = Arc::new(bare::Bare::new(
        config.bare.command.clone(),
        config.bare.args.clone(),
    ));
    let agent = Arc::new(agent::Agent::new(
        config.clone(),
        store.clone(),
        model,
        host,
    ));
    let app = api::router(config.clone(), store, agent);
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
