mod agent;
mod api;
mod auth;
mod bare;
mod bot;
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
    // Without [vm], bare-mcp starts on the first browser tool call. With [vm], the guest runs bare.
    let (agent, bot) = match config.vm.as_ref() {
        None => {
            let host = Arc::new(bare::Bare::new(
                config.bare.command.clone(),
                config.bare.args.clone(),
            ));
            (
                Arc::new(agent::Agent::new(
                    config.clone(),
                    store.clone(),
                    model,
                    host,
                )),
                None,
            )
        }
        Some(v) => {
            let vm: Arc<dyn bot::vm::Vm> = Arc::new(bot::vm::Harness::new(v));
            let hub = bot::hub::Hub::new(
                store.clone(),
                &v.bot_id,
                config.data_dir.clone(),
                vm.clone(),
                v.command_secs,
            );
            let tools = tools::Tools::new(
                Arc::new(bot::hub::GuestHost(hub.clone())),
                store.clone(),
                config.workspace(),
                &config.data_dir,
            )
            .with_guest(hub.clone());
            let agent = Arc::new(agent::Agent::with_tools(
                config.clone(),
                store.clone(),
                model,
                tools,
            ));
            let cap = Arc::new(bot::screen::QmpCapture {
                socket: vm.qmp(),
                dir: config.data_dir.join("screen"),
            });
            let bot = bot::Bot::new(&config, store.clone(), agent.clone(), vm, hub, cap).await?;
            bot.start().await?;
            (agent, Some(bot))
        }
    };
    let app = api::router_with(config.clone(), store, agent, bot.clone());
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
