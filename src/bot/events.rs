//! Batching of ingested events. Events of one source and kind join one group. A group closes
//! after a quiet window, or after a maximum delay from its first event, so a steady stream
//! cannot postpone the work forever. Pure logic: the caller supplies the clock.

use std::collections::HashMap;
use std::time::Duration;

use tokio::time::Instant;

struct Group {
    first: Instant,
    last: Instant,
    ids: Vec<i64>,
    priority: i64,
}

#[derive(Debug, PartialEq)]
pub struct Closed {
    pub source: String,
    pub kind: String,
    pub ids: Vec<i64>,
    pub priority: i64,
}

pub struct Batcher {
    window: Duration,
    max_delay: Duration,
    groups: HashMap<(String, String), Group>,
}

impl Batcher {
    pub fn new(window: Duration, max_delay: Duration) -> Self {
        Self {
            window,
            max_delay,
            groups: HashMap::new(),
        }
    }

    /// Add an event received at `at`. After a restart, `at` is the time the event arrived.
    pub fn add(&mut self, at: Instant, source: &str, kind: &str, id: i64, priority: i64) {
        let g = self
            .groups
            .entry((source.into(), kind.into()))
            .or_insert_with(|| Group {
                first: at,
                last: at,
                ids: vec![],
                priority,
            });
        g.first = g.first.min(at);
        g.last = g.last.max(at);
        g.priority = g.priority.max(priority);
        g.ids.push(id);
    }

    fn close_at(&self, g: &Group) -> Instant {
        (g.last + self.window).min(g.first + self.max_delay)
    }

    /// When the next group closes.
    pub fn next_due(&self) -> Option<Instant> {
        self.groups.values().map(|g| self.close_at(g)).min()
    }

    pub fn pending(&self) -> usize {
        self.groups.values().map(|g| g.ids.len()).sum()
    }

    /// Remove and return the groups that are due at `now`, oldest event first.
    pub fn take_due(&mut self, now: Instant) -> Vec<Closed> {
        let due: Vec<_> = self
            .groups
            .iter()
            .filter(|(_, g)| self.close_at(g) <= now)
            .map(|(k, _)| k.clone())
            .collect();
        let mut out: Vec<(Instant, Closed)> = due
            .into_iter()
            .filter_map(|k| {
                let g = self.groups.remove(&k)?;
                Some((
                    g.first,
                    Closed {
                        source: k.0,
                        kind: k.1,
                        ids: g.ids,
                        priority: g.priority,
                    },
                ))
            })
            .collect();
        out.sort_by_key(|(t, _)| *t);
        out.into_iter().map(|(_, c)| c).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const W: Duration = Duration::from_millis(100);
    const M: Duration = Duration::from_millis(1000);

    fn ms(base: Instant, n: u64) -> Instant {
        base + Duration::from_millis(n)
    }

    #[test]
    fn a_burst_closes_once_after_the_quiet_window() {
        let t0 = Instant::now();
        let mut b = Batcher::new(W, M);
        for i in 0..50 {
            b.add(ms(t0, i), "mail", "new", i as i64, 0);
        }
        assert_eq!(b.next_due(), Some(ms(t0, 49 + 100)));
        assert!(
            b.take_due(ms(t0, 100)).is_empty(),
            "still inside the window"
        );
        let closed = b.take_due(ms(t0, 149));
        assert_eq!(closed.len(), 1);
        assert_eq!(closed[0].ids.len(), 50);
        assert_eq!(b.pending(), 0);
    }

    #[test]
    fn a_continuous_stream_cannot_starve_the_batch() {
        let t0 = Instant::now();
        let mut b = Batcher::new(W, M);
        // One event every 50 ms: the quiet window never ends. The maximum delay does.
        for i in 0..20 {
            b.add(ms(t0, i * 50), "mail", "new", i as i64, 0);
            assert!(b.take_due(ms(t0, i * 50)).is_empty(), "closed early at {i}");
        }
        assert_eq!(b.next_due(), Some(ms(t0, 1000)));
        assert_eq!(b.take_due(ms(t0, 1000)).len(), 1);
    }

    #[test]
    fn only_compatible_events_share_a_batch() {
        let t0 = Instant::now();
        let mut b = Batcher::new(W, M);
        b.add(t0, "mail", "new", 1, 0);
        b.add(t0, "mail", "new", 2, 2);
        b.add(t0, "jobs", "new", 3, 0);
        b.add(t0, "mail", "bounce", 4, 0);
        let closed = b.take_due(ms(t0, 200));
        assert_eq!(closed.len(), 3);
        let mail = closed
            .iter()
            .find(|c| c.source == "mail" && c.kind == "new")
            .unwrap();
        assert_eq!((mail.ids.clone(), mail.priority), (vec![1, 2], 2));
    }

    #[test]
    fn a_new_event_after_a_close_starts_a_new_batch() {
        let t0 = Instant::now();
        let mut b = Batcher::new(W, M);
        b.add(t0, "mail", "new", 1, 0);
        assert_eq!(b.take_due(ms(t0, 100)).len(), 1);
        b.add(ms(t0, 500), "mail", "new", 2, 0);
        assert_eq!(b.next_due(), Some(ms(t0, 600)));
    }

    #[test]
    fn events_restored_after_a_restart_keep_their_arrival_time() {
        let t0 = Instant::now();
        let mut b = Batcher::new(W, M);
        // The host was down: both events arrived long ago, so the group is due at once.
        let long_ago = t0 - Duration::from_secs(60);
        b.add(long_ago, "mail", "new", 1, 0);
        b.add(long_ago, "mail", "new", 2, 0);
        assert_eq!(b.take_due(t0).len(), 1);
    }
}
