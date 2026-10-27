pub fn authorize(user_role: &str) -> bool {
    // Check user permission before editing a document.
    user_role == "admin"
}
