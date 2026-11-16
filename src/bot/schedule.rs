//! Daily schedules for ready bots (D88).

use crate::store::{Store, rfc3339};
use rusqlite::{OptionalExtension, Transaction, TransactionBehavior, params};
use serde::{Deserialize, Serialize};

/// A daily time in one fixed zone. The zone is `UTC` or an offset such as `+05:30`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(try_from = "RawSchedule")]
pub struct Schedule {
    pub time: String,
    pub timezone: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawSchedule {
    time: String,
    timezone: String,
}

impl TryFrom<RawSchedule> for Schedule {
    type Error = String;
    fn try_from(r: RawSchedule) -> Result<Self, String> {
        let s = Schedule {
            time: r.time,
            timezone: r.timezone,
        };
        s.offset_secs()?;
        let ok = s.time.len() == 5
            && s.time.is_char_boundary(2)
            && s.time.as_bytes()[2] == b':'
            && number(&s.time[..2]).is_some_and(|h| h < 24)
            && number(&s.time[3..]).is_some_and(|m| m < 60);
        if !ok {
            return Err("the schedule time must look like 08:00".into());
        }
        Ok(s)
    }
}

fn number(s: &str) -> Option<i64> {
    (s.len() == 2 && s.bytes().all(|b| b.is_ascii_digit())).then(|| s.parse().unwrap())
}

const DAY: i64 = 86_400;

impl Schedule {
    fn offset_secs(&self) -> Result<i64, String> {
        let bad = || "the schedule timezone must be UTC or an offset like +05:30".to_string();
        if self.timezone == "UTC" {
            return Ok(0);
        }
        let z = &self.timezone;
        let sign = match z.as_bytes().first() {
            Some(b'+') => 1,
            Some(b'-') => -1,
            _ => return Err(bad()),
        };
        if z.len() != 6 || !z.is_char_boundary(3) || z.as_bytes()[3] != b':' {
            return Err(bad());
        }
        match (number(&z[1..3]), number(&z[4..])) {
            (Some(h), Some(m)) if h <= 14 && m < 60 => Ok(sign * (h * 3600 + m * 60)),
            _ => Err(bad()),
        }
    }
}

impl Store {
    /// Queue one task for each started bot whose daily time has passed since its last run.
    /// A missed day queues one task. The date commits with the task, so a restart adds none.
    /// A bot seen for the first time records today and queues nothing.
    /// Returns the number of queued tasks.
    pub async fn schedule_tick(&self, now_secs: i64) -> anyhow::Result<usize> {
        self.run(move |c| {
            let t = Transaction::new_unchecked(c, TransactionBehavior::Immediate)?;
            // No schema version: other branches use later versions for their own tables.
            t.execute_batch("CREATE TABLE IF NOT EXISTS bot_schedule(bot_id TEXT PRIMARY KEY, last_date TEXT NOT NULL);")?;
            let rows: Vec<(String, i64, String)> = {
                let mut q = t.prepare("SELECT d.draft_id, s.chat_id, d.body FROM bot_setup_drafts d JOIN bot_state s ON s.bot_id=d.bot_id WHERE d.lifecycle=1")?;
                q.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?.collect::<Result<_, _>>()?
            };
            let mut queued = 0;
            for (id, chat, body) in rows {
                let Ok(setup) = serde_json::from_str::<crate::bot::setup::Setup>(&body) else { continue };
                let Some(s) = setup.draft.schedule.as_ref().filter(|_| setup.started) else { continue };
                let Ok(offset) = s.offset_secs() else { continue };
                let local = rfc3339(now_secs + offset);
                let (today, clock) = (&local[..10], &local[11..16]);
                let due = clock >= s.time.as_str();
                let last: Option<String> = t.query_row("SELECT last_date FROM bot_schedule WHERE bot_id=?1", [&id], |r| r.get(0)).optional()?;
                let last = last.unwrap_or_else(|| {
                    // First sight: today's run is skipped when its time has passed.
                    if due { today.to_owned() } else { rfc3339(now_secs + offset - DAY)[..10].to_owned() }
                });
                if due && last.as_str() < today {
                    let body = setup.draft.task.clone().unwrap_or_else(|| setup.draft.instructions.clone());
                    let title: String = body.chars().take(80).collect();
                    t.execute("INSERT INTO tasks(id,bot_id,chat_id,source,title,body,priority,state,created_at) VALUES (?1,?2,?3,'message',?4,?5,1,'queued',?6)", params![format!("sched-{id}-{today}"), id, chat, title, body, rfc3339(now_secs)])?;
                    queued += 1;
                }
                let last = if due { today } else { last.as_str() };
                t.execute("INSERT INTO bot_schedule(bot_id,last_date) VALUES (?1,?2) ON CONFLICT(bot_id) DO UPDATE SET last_date=excluded.last_date", params![id, last])?;
            }
            t.commit()?;
            Ok(queued)
        }).await
    }
}

#[cfg(test)]
mod tests {
    use super::{DAY, Schedule};
    use crate::bot::setup::{Authority, Draft, Limits, Resources, Setup, Status};
    use crate::store::Store;
    use crate::testutil::temp_dir;

    // 2024-03-01T00:00:00Z.
    const D0: i64 = 1_709_251_200;

    fn parse(time: &str, tz: &str) -> Result<Schedule, serde_json::Error> {
        serde_json::from_value(serde_json::json!({"time": time, "timezone": tz}))
    }

    fn draft(schedule: Option<Schedule>) -> Draft {
        Draft {
            revision: 1,
            name: "News".into(),
            instructions: "Read the news".into(),
            model: "fixture".into(),
            authority: Authority::Full,
            resources: Resources {
                cpus: 2,
                memory_mib: 2048,
                disk_gib: 10,
            },
            budget: Some(crate::bot::setup::Budget {
                currency: "TST".into(),
                exponent: 2,
                period: "month".into(),
                allowance: 100,
                ceiling: 1000,
            }),
            credentials: vec![],
            task: Some("Digest".into()),
            schedule,
            max_steps: None,
        }
    }

    #[test]
    fn old_drafts_parse_and_bad_schedules_fail() {
        let v = serde_json::to_value(draft(None)).unwrap();
        assert!(v.get("schedule").is_none());
        assert!(
            serde_json::from_value::<Draft>(v)
                .unwrap()
                .schedule
                .is_none()
        );
        assert!(parse("08:00", "UTC").is_ok());
        assert!(parse("23:59", "+05:30").is_ok());
        assert!(parse("08:00", "-14:00").is_ok());
        for t in ["8:00", "24:00", "08:60", "0800", "08:0a", "é:000", ""] {
            assert!(parse(t, "UTC").is_err(), "{t}");
        }
        for z in [
            "IST", "+5:30", "+15:00", "+05:60", "05:30", "+0530", "utc", "é+05:3",
        ] {
            assert!(parse("08:00", z).is_err(), "{z}");
        }
    }

    async fn start(store: &Store, schedule: Option<Schedule>) {
        store
            .create_setup(&Setup::new("bot", draft(schedule)).unwrap())
            .await
            .unwrap();
        let mut r = store.setup_record("bot").await.unwrap().unwrap();
        let limits = Limits {
            max_cpus: 2,
            max_memory_mib: 2048,
            max_disk_gib: 10,
        };
        store
            .approve_setup("bot", r.version, 1, "owner", &limits, "p")
            .await
            .unwrap();
        r = store.setup_record("bot").await.unwrap().unwrap();
        for step in &mut r.setup.steps {
            step.status = Status::Done;
        }
        store.save_setup_state(r.version, &r.setup).await.unwrap();
        store.finish_setup("bot").await.unwrap();
    }

    async fn queued(store: &Store) -> i64 {
        store
            .run(|c| {
                c.query_row(
                    "SELECT COUNT(*) FROM tasks WHERE id LIKE 'sched-%'",
                    [],
                    |r| r.get(0),
                )
            })
            .await
            .unwrap()
    }

    #[tokio::test]
    async fn due_time_queues_once_and_survives_restart() {
        let dir = temp_dir();
        let sched = parse("08:00", "+05:30").unwrap();
        let store = Store::open(&dir).unwrap();
        start(&store, Some(sched)).await;
        // 02:00Z is 07:30 local. Day 0 is seeded and queues nothing.
        let day0_before = D0 + 2 * 3600;
        assert_eq!(store.schedule_tick(day0_before).await.unwrap(), 0);
        // Before the time on day 1: nothing.
        assert_eq!(store.schedule_tick(D0 + DAY + 2 * 3600).await.unwrap(), 0);
        // 02:30Z on day 1 is 08:00 local: one task.
        let due = D0 + DAY + 2 * 3600 + 1800;
        assert_eq!(store.schedule_tick(due).await.unwrap(), 1);
        assert_eq!(store.schedule_tick(due + 3600).await.unwrap(), 0);
        drop(store);
        let store = Store::open(&dir).unwrap();
        assert_eq!(store.schedule_tick(due + 7200).await.unwrap(), 0);
        assert_eq!(queued(&store).await, 1);
        let t = store.tasks("bot", 10).await.unwrap();
        assert!(t.iter().any(|t| t.body == "Digest"));
    }

    #[tokio::test]
    async fn missed_days_queue_one_catch_up_task() {
        let store = Store::open_memory().unwrap();
        start(&store, Some(parse("08:00", "UTC").unwrap())).await;
        assert_eq!(store.schedule_tick(D0 + 9 * 3600).await.unwrap(), 0);
        // The server was down for three days.
        assert_eq!(
            store.schedule_tick(D0 + 4 * DAY + 9 * 3600).await.unwrap(),
            1
        );
        assert_eq!(
            store.schedule_tick(D0 + 4 * DAY + 10 * 3600).await.unwrap(),
            0
        );
        assert_eq!(queued(&store).await, 1);
    }

    #[tokio::test]
    async fn bots_without_a_schedule_queue_nothing() {
        let store = Store::open_memory().unwrap();
        start(&store, None).await;
        assert_eq!(store.schedule_tick(D0).await.unwrap(), 0);
        assert_eq!(store.schedule_tick(D0 + 5 * DAY).await.unwrap(), 0);
    }
}
