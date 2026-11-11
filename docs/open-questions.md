# Open questions

Answer these before the first code. Record each answer in [decisions.md](decisions.md).

1. **Language and stack of the service.** Rust matches `bare` and the owner's rule to try Rust first for new tools. The web app may need a different stack.
2. **Where it runs.** On the owner's main laptop, a second laptop, or a server. The model proxy and `bare` must be reachable from there.
3. **Access.** A public address with its own login, or a private network only. The web app must work on a phone.
4. **Model.** Which model is the default, and whether a cheaper model handles approvals and routines.
5. **Shell and files.** Whether the bot gets a shell. If it does, which sandbox.
6. **Database.** SQLite or other.
7. **Approvals.** Which actions always ask, and how the user answers on a phone.
8. **Feedback loop.** Which agent fixes `bare` issues, and how `deck` gets each new `bare` build. See [feedback-loop.md](feedback-loop.md).
9. **First milestone.** A suggestion: chat in the web app, one bot, `bare` open, snapshot and screenshot, and screenshots sent to the chat.
10. **License.** `bare` uses MIT or Apache 2.0. `deck` has no license yet.
