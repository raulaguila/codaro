"""Pequeno repositório para demonstrar busca e investigação."""


class ProjectPolicy:
    def can_edit(self, user, project):
        """Check project editing permissions for the owner or an administrator."""
        return user.id == project.owner_id or user.is_admin


def update_project(user, project, title):
    """Validate access before changing the project's title."""
    if not ProjectPolicy().can_edit(user, project):
        raise PermissionError("User cannot edit this project")
    project.title = title
    return project
