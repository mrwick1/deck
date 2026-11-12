//! Developer control of the guest executor: `deck executor <command>`.
//! It talks to one bot endpoint and the journal. It starts no VM and calls no model.

use std::path::PathBuf;
use std::time::Duration;

use anyhow::{Context, bail};
use serde_json::json;

use super::client::{Options, Session};
use crate::store::Store;

const HELP: &str = "\
Usage: deck executor <command> [options]

Commands:
  run        Dispatch a command: run --id ID --task ID [--deadline-secs N] [--wait-secs N] -- ARGV...
  status     Reconnect, reconcile the journal with the guest and print one command: status --id ID
  cancel     Ask the guest to stop a command: cancel --id ID [--wait-secs N]
  fetch      Copy the artifacts of a command into the bot store: fetch --id ID
  reconcile  Reconnect and settle in-flight commands. Sends no command.
  show       Print journal rows and events. Does not connect.

Options:
  --bot ID          bot ID (required)
  --endpoint PATH   unix socket of the guest executor (all but show)
  --data-dir DIR    host data directory (default: DECK_DATA_DIR, then data_dir of deck.toml)
  --peer-pid PID    require this process at the other end of the socket
  --guest-id ID     require this guest identity
  --timeout-secs N  per request timeout (default 10)

--wait-secs 0 sends and returns at once. A command with an unknown outcome is never sent again.
Exit status of run: 0 succeeded, 3 any other state, 1 error.";

struct Args {
    cmd: String,
    flags: std::collections::HashMap<String, String>,
    rest: Vec<String>,
}

fn parse(a: &[String]) -> anyhow::Result<Args> {
    let cmd = a.first().cloned().unwrap_or_default();
    let (mut flags, mut rest) = (std::collections::HashMap::new(), vec![]);
    let mut it = a.iter().skip(1);
    while let Some(x) = it.next() {
        if x == "--" {
            rest = it.cloned().collect();
            break;
        }
        let k = x
            .strip_prefix("--")
            .with_context(|| format!("unexpected argument {x}"))?;
        flags.insert(
            k.to_string(),
            it.next()
                .with_context(|| format!("--{k} needs a value"))?
                .clone(),
        );
    }
    Ok(Args { cmd, flags, rest })
}

impl Args {
    fn get(&self, k: &str) -> anyhow::Result<&str> {
        self.flags
            .get(k)
            .map(String::as_str)
            .with_context(|| format!("--{k} is required"))
    }
    fn num(&self, k: &str, default: u64) -> anyhow::Result<u64> {
        self.flags.get(k).map_or(Ok(default), |v| {
            v.parse().with_context(|| format!("--{k} must be a number"))
        })
    }
}

pub async fn run(args: &[String]) -> anyhow::Result<()> {
    if args.is_empty() || args.iter().any(|a| a == "--help" || a == "-h") {
        println!("{HELP}");
        return Ok(());
    }
    let a = parse(args)?;
    if !["run", "status", "cancel", "fetch", "reconcile", "show"].contains(&a.cmd.as_str()) {
        bail!("unknown command {}; see --help", a.cmd);
    }
    let data_dir = match a
        .flags
        .get("data-dir")
        .cloned()
        .or_else(|| std::env::var("DECK_DATA_DIR").ok())
    {
        Some(d) => PathBuf::from(d),
        None => crate::config::Config::load()?.data_dir,
    };
    let store = Store::open(&data_dir)?;
    let bot = a.get("bot")?.to_string();
    if a.cmd == "show" {
        let id = a.flags.get("id").cloned();
        let rows = match &id {
            Some(i) => store.command(i.clone()).await?.into_iter().collect(),
            None => store.commands(bot.clone()).await?,
        };
        let events: Vec<_> = store
            .events(bot, id)
            .await?
            .into_iter()
            .map(|(k, e, d)| json!({"kind":k,"epoch":e,"detail":d}))
            .collect();
        println!("{}", json!({"commands": rows, "events": events}));
        return Ok(());
    }
    let mut o = Options::new(&bot, &PathBuf::from(a.get("endpoint")?), &data_dir);
    o.peer_pid = a
        .flags
        .get("peer-pid")
        .map(|v| v.parse())
        .transpose()
        .context("--peer-pid")?;
    o.expected_guest = a.flags.get("guest-id").cloned();
    o.request_timeout = Duration::from_secs(a.num("timeout-secs", 10)?);
    let s = Session::connect(store, o).await?;
    eprintln!(
        "connected: epoch {}, max epoch before {}, guest {}",
        s.epoch, s.max_epoch_before, s.guest_id
    );
    let cmd = match a.cmd.as_str() {
        "run" => {
            if a.rest.is_empty() {
                bail!("give the command after --");
            }
            let id = a.get("id")?;
            let deadline = a.num("deadline-secs", 60)?;
            s.reconcile().await?; // settle earlier rows first; this sends nothing
            let c = s
                .exec(a.get("task")?, id, a.rest.clone(), deadline * 1000)
                .await?;
            let wait = a.num("wait-secs", deadline + 10)?;
            if wait > 0 {
                s.wait(id, Duration::from_secs(wait)).await?
            } else {
                c
            }
        }
        "status" => {
            s.reconcile().await?;
            s.wait(a.get("id")?, Duration::ZERO).await?
        }
        "cancel" => {
            let id = a.get("id")?;
            let (ack, c) = s.cancel(id).await?;
            eprintln!(
                "cancel request: {ack} (accepted is not confirmed; the final state confirms)"
            );
            let wait = a.num("wait-secs", 10)?;
            if wait > 0 && ack == "accepted" {
                s.wait(id, Duration::from_secs(wait)).await?
            } else {
                c
            }
        }
        "fetch" => {
            let id = a.get("id")?;
            let files = s.fetch_artifacts(id).await?;
            println!("{}", json!({"command_id": id, "artifacts": files}));
            return Ok(());
        }
        _ => {
            for c in s.reconcile().await? {
                println!("{}", serde_json::to_string(&c)?);
            }
            return Ok(());
        }
    };
    println!("{}", serde_json::to_string(&cmd)?);
    if a.cmd == "run" && cmd.state != "succeeded" {
        std::process::exit(3);
    }
    Ok(())
}
