namespace Permissions {
    class Security {
        public bool Authorize(string userRole) {
            // Check user permission before editing a document.
            return userRole == "admin";
        }
    }
}
