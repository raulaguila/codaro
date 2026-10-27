class Security {
    // Check user permission before editing a document.
    public boolean authorize(String role) {
        return "admin".equals(role);
    }
}
