//! Host control of the guest executor: protocol, journal and client. See docs/stage2-results.md.
pub mod cli;
pub mod client;
pub mod journal;
pub mod proto;
#[cfg(test)]
mod tests;
