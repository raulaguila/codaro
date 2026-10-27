class Policy:
    def authorize(self, user):
        """Check user permission before editing a document."""
        return user.get("role") == "admin"
