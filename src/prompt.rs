pub const SYSTEM: &str = include_str!("prompt.md");

#[cfg(test)]
mod tests {
    use super::SYSTEM;

    #[test]
    fn names_every_tool() {
        for name in [
            "browser_open",
            "browser_snapshot",
            "browser_act",
            "browser_screenshot",
            "browser_close",
            "send_file",
        ] {
            assert!(SYSTEM.contains(&format!("`{name}`")), "{name}");
        }
    }

    #[test]
    fn has_the_screenshot_rule() {
        assert!(SYSTEM.contains(
            "When the user asks for a screenshot, call `browser_screenshot`, read the PNG path from its reply, and call send_file with that path. You can always send screenshots and files into the chat. Never say that you cannot."
        ));
    }

    #[test]
    fn has_the_safety_rules() {
        assert!(SYSTEM.contains("untrusted data"));
        assert!(SYSTEM.contains("Never claim a result that you did not check"));
        assert!(SYSTEM.contains("stale_ref"));
    }
}
