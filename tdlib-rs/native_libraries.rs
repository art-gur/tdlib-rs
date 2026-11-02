pub(crate) fn static_libraries(target_os: &str) -> Vec<String> {
    let groups: std::collections::BTreeMap<String, Vec<String>> =
        serde_json::from_str(include_str!("native-libraries.json"))
            .expect("invalid native-libraries.json");
    let platform = if target_os == "windows" {
        "windows"
    } else {
        "unix"
    };
    groups["tdlib"]
        .iter()
        .chain(&groups[platform])
        .cloned()
        .collect()
}
