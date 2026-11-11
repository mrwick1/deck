# Feedback loop with bare

`deck` uses only `bare` (decision D2). Each failure in `bare` is a chance to improve it.

## The loop

1. The user gives `deck` a task on a real site.
2. `bare` fails: a page does not load, a script throws, a layout is wrong or a step times out.
3. `deck` tells the user what failed, in plain words.
4. `deck` files one issue for `bare`, with:
   - the site and the step that failed,
   - the `bare-mcp` tool call and its report (the step, the error, console lines and failed requests),
   - the expected result,
   - a capture, if it has one.
5. A background agent picks up the issue and fixes `bare` in a pull request.
6. After the fix ships, `deck` uses the new `bare`. The user can run the task again.

## Where issues go

- GitHub: the `mrwick1/bare` repository, with the label `bare-mcp`.
- The owner's private Plane project "bare MCP" also takes these issues.

## Rules

- File one issue for each distinct failure. Search for an open issue first, and add to it instead of filing a duplicate.
- Never put credentials, cookies or private page content in an issue. Use the site name and a safe description.
- Do not retry a failed site in another browser. There is no other browser.
- Keep a list of open `bare` issues that block user tasks, so the user can see what is waiting.

## Open points

- Which agent fixes the issues, and how it is started (scheduled task or webhook).
- How `deck` updates to a new `bare` build.
